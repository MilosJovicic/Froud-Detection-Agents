import pytest

from agents.synthesizer import synthesize_verdict
from contracts.branch import BranchSpec, StructuralClaim, StructuralEvidence, VerifierResult
from contracts.hypothesis import ClaimType, Hypothesis
from contracts.verdict import EvidenceAggregation


def _sample_hypothesis() -> Hypothesis:
    return Hypothesis(
        raw="Investigate a collusion ring for C1_1 and C1_2",
        claim_type=ClaimType.RING,
        scope={},
    )


def _sample_aggregation() -> EvidenceAggregation:
    return EvidenceAggregation(
        evidence=[
            StructuralEvidence(
                branch_spec=BranchSpec(
                    projection_id="shared_device_ring",
                    algorithm_id="wcc",
                    params={"min_customers_per_device": 2},
                    rationale="ring branch",
                ),
                claim=StructuralClaim(
                    assertion="C1_1 and C1_2 share a device component",
                    entities=["C1_1", "C1_2"],
                    score=2.0,
                    topology="wcc_component",
                ),
                verifier=VerifierResult(
                    outcome="AGREE",
                    evidence_cypher="MATCH ...",
                    row_count=2,
                ),
                confidence=0.9,
                latency_ms=12,
            )
        ],
        consensus="supported",
        confidence=0.9,
        conflicts=[],
        agreeing_branches=1,
        disagreeing_branches=0,
        not_applicable_branches=0,
    )


@pytest.mark.asyncio
async def test_synthesizer_llm_mode_uses_runner(monkeypatch):
    hypothesis = _sample_hypothesis()
    aggregation = _sample_aggregation()

    async def fake_run_synthesizer_llm(hypothesis_input, aggregation_input, *, feedback=None):
        assert hypothesis_input == hypothesis
        assert aggregation_input == aggregation
        assert feedback is None
        return {
            "final_assessment": "The ring hypothesis is strongly supported.",
            "recommended_actions": ["Escalate for review", "Inspect shared devices"],
        }

    monkeypatch.setenv("SYNTHESIZER_MODE", "llm")
    monkeypatch.setattr("agents.synthesizer._run_synthesizer_llm", fake_run_synthesizer_llm)

    verdict = await synthesize_verdict(hypothesis, aggregation, trace_id="wf-123")
    assert verdict.trace_id == "wf-123"
    assert verdict.final_assessment == "The ring hypothesis is strongly supported."
    assert verdict.recommended_actions == ["Escalate for review", "Inspect shared devices"]


@pytest.mark.asyncio
async def test_synthesizer_llm_retries_invalid_output_then_succeeds(monkeypatch):
    hypothesis = _sample_hypothesis()
    aggregation = _sample_aggregation()
    attempts: list[str | None] = []

    async def fake_run_synthesizer_llm(hypothesis_input, aggregation_input, *, feedback=None):
        attempts.append(feedback)
        if len(attempts) == 1:
            return {"final_assessment": " ", "recommended_actions": []}
        return {
            "final_assessment": "The ring hypothesis is supported by the aggregated evidence.",
            "recommended_actions": ["Escalate for review", "Inspect shared devices"],
        }

    monkeypatch.setenv("SYNTHESIZER_MODE", "llm")
    monkeypatch.setattr("agents.synthesizer._run_synthesizer_llm", fake_run_synthesizer_llm)

    verdict = await synthesize_verdict(hypothesis, aggregation, trace_id="wf-456")
    assert len(attempts) == 2
    assert attempts[0] is None
    assert "final_assessment must not be blank" in attempts[1]
    assert verdict.trace_id == "wf-456"
    assert verdict.confidence == 0.9


@pytest.mark.asyncio
async def test_synthesizer_llm_falls_back_to_rule_based_on_error(monkeypatch):
    hypothesis = _sample_hypothesis()
    aggregation = _sample_aggregation()

    async def failing_synthesizer(*args, **kwargs):
        raise RuntimeError("Ollama unavailable")

    monkeypatch.setenv("SYNTHESIZER_MODE", "llm")
    monkeypatch.setattr("agents.synthesizer._run_synthesizer_llm", failing_synthesizer)

    verdict = await synthesize_verdict(hypothesis, aggregation, trace_id="wf-789")
    assert verdict.trace_id == "wf-789"
    assert verdict.confidence == 0.9
    assert verdict.final_assessment
    assert verdict.recommended_actions
