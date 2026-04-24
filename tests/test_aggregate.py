import pytest

from activities.aggregate import aggregate_evidence_activity
from contracts.branch import BranchSpec, StructuralClaim, StructuralEvidence, VerifierResult


def _evidence(
    *,
    projection_id: str,
    algorithm_id: str,
    outcome: str,
    confidence: float,
    entities: list[str],
    topology: str,
) -> StructuralEvidence:
    return StructuralEvidence(
        branch_spec=BranchSpec(
            projection_id=projection_id,
            algorithm_id=algorithm_id,
            params={},
            rationale="test branch",
        ),
        claim=StructuralClaim(
            assertion="test assertion",
            entities=entities,
            score=confidence,
            topology=topology,
        ),
        verifier=VerifierResult(
            outcome=outcome,
            evidence_cypher="MATCH ...",
            row_count=len(entities),
        ),
        confidence=confidence,
        latency_ms=10,
    )


@pytest.mark.asyncio
async def test_aggregate_evidence_supported_consensus():
    aggregation = await aggregate_evidence_activity(
        [
            _evidence(
                projection_id="shared_device_ring",
                algorithm_id="wcc",
                outcome="AGREE",
                confidence=0.9,
                entities=["C1_1", "C1_2"],
                topology="wcc_component",
            )
        ]
    )

    assert aggregation.consensus == "supported"
    assert aggregation.confidence == 0.9
    assert aggregation.conflicts == []


@pytest.mark.asyncio
async def test_aggregate_evidence_mixed_consensus_collects_conflicts():
    aggregation = await aggregate_evidence_activity(
        [
            _evidence(
                projection_id="shared_device_ring",
                algorithm_id="wcc",
                outcome="AGREE",
                confidence=0.9,
                entities=["C1_1", "C1_2"],
                topology="wcc_component",
            ),
            _evidence(
                projection_id="shared_card_ring",
                algorithm_id="louvain",
                outcome="DISAGREE",
                confidence=0.3,
                entities=["C1_1", "C1_2"],
                topology="louvain_community",
            ),
        ]
    )

    assert aggregation.consensus == "mixed"
    assert aggregation.disagreeing_branches == 1
    assert aggregation.confidence < 0.9
    assert any("returned DISAGREE" in conflict for conflict in aggregation.conflicts)
