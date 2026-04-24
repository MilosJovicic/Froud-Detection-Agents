from temporalio import activity

from contracts.branch import StructuralEvidence
from contracts.verdict import EvidenceAggregation
from metrics import instrument_activity


@activity.defn(name="aggregate_evidence")
@instrument_activity("aggregate_evidence")
async def aggregate_evidence_activity(
    evidence: list[StructuralEvidence],
) -> EvidenceAggregation:
    agreeing = [item for item in evidence if item.verifier.outcome == "AGREE"]
    disagreeing = [item for item in evidence if item.verifier.outcome == "DISAGREE"]
    not_applicable = [
        item for item in evidence if item.verifier.outcome == "NOT_APPLICABLE"
    ]

    if agreeing and not disagreeing:
        consensus = "supported"
    elif agreeing and disagreeing:
        consensus = "mixed"
    else:
        consensus = "unsupported"

    confidence = _aggregate_confidence(evidence, agreeing, disagreeing, not_applicable)
    conflicts = _collect_conflicts(agreeing, disagreeing)

    return EvidenceAggregation(
        evidence=evidence,
        consensus=consensus,
        confidence=confidence,
        conflicts=conflicts,
        agreeing_branches=len(agreeing),
        disagreeing_branches=len(disagreeing),
        not_applicable_branches=len(not_applicable),
    )


def _aggregate_confidence(
    evidence: list[StructuralEvidence],
    agreeing: list[StructuralEvidence],
    disagreeing: list[StructuralEvidence],
    not_applicable: list[StructuralEvidence],
) -> float:
    if not evidence:
        return 0.0

    if agreeing:
        base_confidence = sum(item.confidence for item in agreeing) / len(agreeing)
    else:
        base_confidence = sum(item.confidence for item in evidence) / len(evidence)

    total = len(evidence)
    consensus_ratio = (len(agreeing) + 0.5 * len(not_applicable)) / total
    disagreement_penalty = 0.15 * (len(disagreeing) / total)

    aggregated = base_confidence * consensus_ratio - disagreement_penalty
    return round(max(0.0, min(1.0, aggregated)), 3)


def _collect_conflicts(
    agreeing: list[StructuralEvidence],
    disagreeing: list[StructuralEvidence],
) -> list[str]:
    conflicts: list[str] = [
        (
            f"{item.branch_spec.projection_id}/{item.branch_spec.algorithm_id} "
            f"returned {item.verifier.outcome}"
        )
        for item in disagreeing
    ]

    entity_sets = {
        tuple(sorted(item.claim.entities))
        for item in agreeing
        if item.claim.entities
    }
    if len(entity_sets) > 1:
        conflicts.append("Agreeing branches reference different entity sets")

    topologies = {item.claim.topology for item in agreeing}
    if len(topologies) > 1:
        conflicts.append("Agreeing branches surfaced different topology labels")

    return conflicts
