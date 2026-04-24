import json
import os
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai import Agent

from agents.model import qwen_model
from contracts.branch import BranchSpec
from contracts.hypothesis import ClaimType, Hypothesis
from gds.algorithms import PROJECTION_ALGORITHM_CATALOG
from metrics import record_agent_tokens


BRANCH_OUTPUT_ADAPTER = TypeAdapter(list[BranchSpec])

DEFAULT_BRANCH_SPEC = BranchSpec(
    projection_id="shared_device_ring",
    algorithm_id="wcc",
    params={"min_customers_per_device": 2},
    rationale="Deterministic fallback after planner validation failure",
)

PLANNER_SYSTEM_PROMPT = """You are the fraud branch planner.
Pick 1 to 3 BranchSpec objects for the hypothesis.
Only use projection_id and algorithm_id values from the provided catalog.
Prefer diverse, high-signal branches over near-duplicates.
Keep params minimal and valid for the selected projection/algorithm.
If the hypothesis is narrow, return a single precise branch."""


def _planner_agent() -> Agent:
    return Agent(
        qwen_model(temperature=0.2, max_tokens=256),
        output_type=list[BranchSpec],
        system_prompt=PLANNER_SYSTEM_PROMPT,
        name="BranchPlannerAgent",
        retries=0,
    )


async def _run_planner_llm(
    hypothesis: Hypothesis,
    *,
    feedback: str | None = None,
) -> list[BranchSpec]:
    prompt_parts = [
        "Hypothesis:",
        hypothesis.model_dump_json(indent=2),
        "",
        "Allowed projection -> algorithm catalog:",
        json.dumps(PROJECTION_ALGORITHM_CATALOG, indent=2),
        "",
        "Return 1 to 3 BranchSpec objects.",
    ]
    if feedback:
        prompt_parts.extend(["", "Validation feedback from the previous attempt:", feedback])

    prompt = "\n".join(prompt_parts)
    try:
        result = await _planner_agent().run(prompt)
    except Exception:
        record_agent_tokens("BranchPlannerAgent", prompt=prompt, output="", status="failure")
        raise

    record_agent_tokens("BranchPlannerAgent", prompt=prompt, output=result.output)
    return result.output


async def plan_branches(hypothesis: Hypothesis) -> list[BranchSpec]:
    mode = os.getenv("PLANNER_MODE", "llm").strip().lower()
    if mode == "rule-based":
        return _rule_based_branches(hypothesis)

    try:
        first_attempt = await _run_planner_llm(hypothesis)
        return _validate_branch_specs(first_attempt)
    except Exception as exc:
        try:
            second_attempt = await _run_planner_llm(hypothesis, feedback=str(exc))
            return _validate_branch_specs(second_attempt)
        except Exception:
            return [DEFAULT_BRANCH_SPEC.model_copy(deep=True)]


def _validate_branch_specs(output: list[BranchSpec] | list[dict[str, Any]]) -> list[BranchSpec]:
    branch_specs = BRANCH_OUTPUT_ADAPTER.validate_python(output)
    if not 1 <= len(branch_specs) <= 3:
        raise ValueError(f"expected 1 to 3 branches, got {len(branch_specs)}")

    validated: list[BranchSpec] = []
    seen_pairs: set[tuple[str, str]] = set()

    for spec in branch_specs:
        allowed_algorithms = PROJECTION_ALGORITHM_CATALOG.get(spec.projection_id)
        if allowed_algorithms is None:
            raise ValueError(f"unknown projection_id: {spec.projection_id}")
        if spec.algorithm_id not in allowed_algorithms:
            raise ValueError(
                f"algorithm_id {spec.algorithm_id!r} is not allowed for {spec.projection_id!r}"
            )

        pair = (spec.projection_id, spec.algorithm_id)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)

        params = {
            key: value
            for key, value in spec.params.items()
            if isinstance(value, (bool, int, float, str))
        }
        rationale = spec.rationale.strip() or "Planner-selected branch"
        validated.append(spec.model_copy(update={"params": params, "rationale": rationale}))

    if not validated:
        raise ValueError("planner output contained no unique valid branches")

    return validated


def _rule_based_branches(hypothesis: Hypothesis) -> list[BranchSpec]:
    text = hypothesis.raw.lower()
    claim_type = hypothesis.claim_type

    if claim_type == ClaimType.RING:
        if "card" in text:
            return [
                BranchSpec(
                    projection_id="shared_card_ring",
                    algorithm_id="louvain",
                    params={"min_customers_per_card": 2},
                    rationale="Rule-based planner chose shared cards for a ring hypothesis",
                )
            ]
        if "ip" in text or "cohort" in text or "bot" in text:
            return [
                BranchSpec(
                    projection_id="ip_cohort",
                    algorithm_id="pagerank",
                    params={"min_customers_per_ip": 2},
                    rationale="Rule-based planner chose shared IP analysis for a ring hypothesis",
                )
            ]
        return [
            BranchSpec(
                projection_id="shared_device_ring",
                algorithm_id="wcc",
                params={"min_customers_per_device": 2},
                rationale="Rule-based planner chose shared devices for a ring hypothesis",
            )
        ]

    if claim_type == ClaimType.CHARGEBACK_CASCADE:
        if "payout" in text or "shell" in text or "country" in text:
            return [
                BranchSpec(
                    projection_id="payout_cluster",
                    algorithm_id="wcc",
                    params={"min_merchants_per_account": 2},
                    rationale="Rule-based planner chose payout clustering for a chargeback hypothesis",
                )
            ]
        return [
            BranchSpec(
                projection_id="merchant_cochargeback",
                algorithm_id="louvain",
                params={"min_shared_customers": 1},
                rationale="Rule-based planner chose merchant co-chargeback clustering",
            )
        ]

    if claim_type == ClaimType.ROUTING_ANOMALY:
        if "flow" in text or "issuer" in text or "settlement" in text:
            return [
                BranchSpec(
                    projection_id="money_flow",
                    algorithm_id="betweenness",
                    params={"min_transaction_amount": 0.0},
                    rationale="Rule-based planner chose money-flow bottleneck analysis",
                )
            ]
        return [
            BranchSpec(
                projection_id="decline_routing",
                algorithm_id="pagerank",
                params={"min_declines": 1},
                rationale="Rule-based planner chose decline routing analysis",
            )
        ]

    if claim_type == ClaimType.ENTITY_SIMILARITY:
        return [
            BranchSpec(
                projection_id="shared_card_ring",
                algorithm_id="louvain",
                params={"min_customers_per_card": 2},
                rationale="Rule-based planner chose shared-card similarity clustering",
            )
        ]

    if claim_type == ClaimType.MONEY_FLOW:
        return [
            BranchSpec(
                projection_id="money_flow",
                algorithm_id="betweenness",
                params={"min_transaction_amount": 0.0},
                rationale="Rule-based planner chose direct money-flow path analysis",
            )
        ]

    return [DEFAULT_BRANCH_SPEC.model_copy(deep=True)]
