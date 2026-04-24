import json
import os

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from agents.model import qwen_model
from contracts.hypothesis import ClaimType, Hypothesis
from contracts.verdict import EvidenceAggregation, FraudVerdict
from metrics import record_agent_tokens


class SynthesisResult(BaseModel):
    final_assessment: str
    recommended_actions: list[str] = Field(default_factory=list)


SYNTHESIZER_SYSTEM_PROMPT = """You write concise analyst-facing fraud verdicts.
Use the hypothesis and aggregated structural evidence only.
Return JSON with:
- final_assessment: one short paragraph
- recommended_actions: 2 to 4 concrete analyst actions"""


def _synthesizer_agent() -> Agent:
    return Agent(
        qwen_model(temperature=0.3, max_tokens=512),
        output_type=SynthesisResult,
        system_prompt=SYNTHESIZER_SYSTEM_PROMPT,
        name="SynthesizerAgent",
        retries=0,
    )


async def _run_synthesizer_llm(
    hypothesis: Hypothesis,
    aggregation: EvidenceAggregation,
    *,
    feedback: str | None = None,
) -> SynthesisResult:
    prompt_parts = [
        "Hypothesis:",
        hypothesis.model_dump_json(indent=2),
        "",
        "Evidence aggregation:",
        aggregation.model_dump_json(indent=2),
        "",
        "Write a concise analyst-facing verdict.",
    ]
    if feedback:
        prompt_parts.extend(["", "Validation feedback from the previous attempt:", feedback])

    prompt = "\n".join(prompt_parts)
    try:
        result = await _synthesizer_agent().run(prompt)
    except Exception:
        record_agent_tokens("SynthesizerAgent", prompt=prompt, output="", status="failure")
        raise

    record_agent_tokens("SynthesizerAgent", prompt=prompt, output=result.output)
    return result.output


async def synthesize_verdict(
    hypothesis: Hypothesis,
    aggregation: EvidenceAggregation,
    *,
    trace_id: str,
) -> FraudVerdict:
    mode = os.getenv("SYNTHESIZER_MODE", "llm").strip().lower()
    fallback = _rule_based_verdict(hypothesis, aggregation, trace_id=trace_id)

    if mode == "rule-based":
        return fallback

    try:
        first_attempt = await _run_synthesizer_llm(hypothesis, aggregation)
        synthesis = _validate_synthesis_result(first_attempt)
        return _build_verdict(hypothesis, aggregation, synthesis, trace_id=trace_id)
    except Exception as exc:
        try:
            second_attempt = await _run_synthesizer_llm(
                hypothesis,
                aggregation,
                feedback=str(exc),
            )
            synthesis = _validate_synthesis_result(second_attempt)
            return _build_verdict(hypothesis, aggregation, synthesis, trace_id=trace_id)
        except Exception:
            return fallback


def _validate_synthesis_result(output: SynthesisResult | dict) -> SynthesisResult:
    synthesis = SynthesisResult.model_validate(output)
    final_assessment = synthesis.final_assessment.strip()
    if not final_assessment:
        raise ValueError("final_assessment must not be blank")

    recommended_actions = _dedupe_actions(synthesis.recommended_actions)
    if not recommended_actions:
        raise ValueError("recommended_actions must contain at least one action")

    return synthesis.model_copy(
        update={
            "final_assessment": final_assessment,
            "recommended_actions": recommended_actions,
        }
    )


def _build_verdict(
    hypothesis: Hypothesis,
    aggregation: EvidenceAggregation,
    synthesis: SynthesisResult,
    *,
    trace_id: str,
) -> FraudVerdict:
    return FraudVerdict(
        hypothesis=hypothesis,
        evidence=aggregation.evidence,
        final_assessment=synthesis.final_assessment,
        confidence=aggregation.confidence,
        recommended_actions=synthesis.recommended_actions,
        trace_id=trace_id,
    )


def _rule_based_verdict(
    hypothesis: Hypothesis,
    aggregation: EvidenceAggregation,
    *,
    trace_id: str,
) -> FraudVerdict:
    branch_count = len(aggregation.evidence)
    agreeing_count = aggregation.agreeing_branches
    primary_entities = aggregation.evidence[0].claim.entities if aggregation.evidence else []
    entity_preview = ", ".join(primary_entities[:4]) if primary_entities else "the investigated entities"

    if aggregation.consensus == "supported":
        stance = "supports"
    elif aggregation.consensus == "mixed":
        stance = "partially supports"
    else:
        stance = "does not support"

    claim_phrase = _claim_phrase(hypothesis.claim_type)
    final_assessment = (
        f"The aggregated graph evidence {stance} the {claim_phrase} hypothesis. "
        f"{agreeing_count} of {branch_count} branch(es) agreed, and the strongest signal centers on {entity_preview}."
    )
    if aggregation.conflicts:
        final_assessment += f" Conflicts detected: {aggregation.conflicts[0]}"

    return FraudVerdict(
        hypothesis=hypothesis,
        evidence=aggregation.evidence,
        final_assessment=final_assessment,
        confidence=aggregation.confidence,
        recommended_actions=_recommended_actions(hypothesis.claim_type, aggregation.consensus),
        trace_id=trace_id,
    )


def _claim_phrase(claim_type: ClaimType) -> str:
    phrases = {
        ClaimType.RING: "collusion ring",
        ClaimType.CHARGEBACK_CASCADE: "chargeback cluster",
        ClaimType.ROUTING_ANOMALY: "routing anomaly",
        ClaimType.ENTITY_SIMILARITY: "entity similarity",
        ClaimType.MONEY_FLOW: "money-flow",
        ClaimType.CUSTOM: "custom fraud",
    }
    return phrases[claim_type]


def _recommended_actions(claim_type: ClaimType, consensus: str) -> list[str]:
    lead_action = (
        "Escalate for immediate manual review"
        if consensus == "supported"
        else "Queue for analyst review with the attached evidence"
    )
    claim_specific = {
        ClaimType.RING: [
            "Review the shared connectors across the linked customers",
            "Apply step-up controls or temporary holds on the affected accounts",
        ],
        ClaimType.CHARGEBACK_CASCADE: [
            "Inspect shared charged-back customers across the merchant set",
            "Review merchant settlement and payout controls",
        ],
        ClaimType.ROUTING_ANOMALY: [
            "Inspect decline rates and routing concentration by acquirer",
            "Compare recent routed transactions against normal baselines",
        ],
        ClaimType.ENTITY_SIMILARITY: [
            "Review the closest lookalike entities for linked behavior",
            "Check whether the similar entities overlap on shared credentials or devices",
        ],
        ClaimType.MONEY_FLOW: [
            "Trace the highlighted payment path end to end",
            "Review counterparties connected through the strongest routing path",
        ],
        ClaimType.CUSTOM: [
            "Review the supporting branch evidence manually",
            "Decide whether a custom projection is needed for follow-up",
        ],
    }
    return [lead_action, *claim_specific[claim_type]]


def _dedupe_actions(actions: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()

    for action in actions:
        normalized = action.strip()
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)

    return deduped
