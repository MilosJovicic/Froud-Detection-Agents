from collections import defaultdict
from typing import Any

from contracts.branch import BranchSpec, StructuralClaim


def build_structural_claim(
    spec: BranchSpec,
    algorithm_rows: list[dict[str, Any]],
    node_details: dict[int, dict[str, Any]],
) -> StructuralClaim:
    if not algorithm_rows:
        return StructuralClaim(
            assertion=f"No structural evidence found for {spec.projection_id}",
            entities=[],
            score=0.0,
            topology="empty",
        )

    sample_row = algorithm_rows[0]

    if "componentId" in sample_row or "communityId" in sample_row:
        grouping_key = "componentId" if "componentId" in sample_row else "communityId"
        topology = "wcc_component" if grouping_key == "componentId" else "louvain_community"
        grouped_nodes: dict[int, list[int]] = defaultdict(list)

        for row in algorithm_rows:
            grouped_nodes[int(row[grouping_key])].append(int(row["nodeId"]))

        _, selected_node_ids = max(
            grouped_nodes.items(),
            key=lambda item: (
                len(_preferred_entities(spec, item[1], node_details)),
                len(item[1]),
            ),
        )

        entities = _preferred_entities(spec, selected_node_ids, node_details)
        return StructuralClaim(
            assertion=f"{len(entities)} entities form a {topology} for {spec.projection_id}",
            entities=entities,
            score=float(len(entities)),
            topology=topology,
        )

    if "nodeId" in sample_row and "score" in sample_row:
        selected_row = max(algorithm_rows, key=lambda row: float(row["score"]))
        node_detail = node_details.get(int(selected_row["nodeId"]), {})
        entity_id = node_detail.get("entity_id", str(selected_row["nodeId"]))
        return StructuralClaim(
            assertion=f"{entity_id} is the strongest {spec.algorithm_id} signal in {spec.projection_id}",
            entities=[entity_id],
            score=float(selected_row["score"]),
            topology=f"{spec.algorithm_id}_score",
        )

    if "node1" in sample_row and "node2" in sample_row and "similarity" in sample_row:
        selected_row = max(algorithm_rows, key=lambda row: float(row["similarity"]))
        left = node_details.get(int(selected_row["node1"]), {}).get(
            "entity_id", str(selected_row["node1"])
        )
        right = node_details.get(int(selected_row["node2"]), {}).get(
            "entity_id", str(selected_row["node2"])
        )
        return StructuralClaim(
            assertion=f"{left} and {right} are a high-similarity pair in {spec.projection_id}",
            entities=[left, right],
            score=float(selected_row["similarity"]),
            topology=f"{spec.algorithm_id}_pair",
        )

    return StructuralClaim(
        assertion=f"Unhandled algorithm output shape for {spec.algorithm_id}",
        entities=[],
        score=0.0,
        topology="unknown",
    )


def choose_verifier_request(
    spec: BranchSpec,
    claim: StructuralClaim,
) -> tuple[str, dict[str, Any]] | None:
    if spec.projection_id == "shared_device_ring" and len(claim.entities) >= 2:
        return (
            "confirm_component",
            {
                "customer_ids": claim.entities,
                "connector_type": "device",
            },
        )

    if spec.projection_id == "shared_card_ring" and len(claim.entities) >= 2:
        return (
            "confirm_component",
            {
                "customer_ids": claim.entities,
                "connector_type": "card",
            },
        )

    if spec.projection_id == "ip_cohort" and len(claim.entities) >= 2:
        return (
            "confirm_component",
            {
                "customer_ids": claim.entities,
                "connector_type": "ip",
            },
        )

    if spec.projection_id == "merchant_cochargeback" and len(claim.entities) >= 2:
        return (
            "confirm_merchant_cochargeback",
            {
                "merchant_ids": claim.entities,
                "min_shared_customers": 1,
            },
        )

    if spec.projection_id == "payout_cluster" and len(claim.entities) >= 2:
        return (
            "confirm_payout_cluster",
            {
                "merchant_ids": claim.entities,
                "require_shared_account": True,
                "require_shared_country": True,
            },
        )

    if spec.projection_id == "money_flow" and claim.entities:
        transaction_ids = [entity for entity in claim.entities if entity.startswith("TX")]
        acquirer_ids = [entity for entity in claim.entities if entity.startswith("ACQ_")]
        issuer_ids = [entity for entity in claim.entities if entity.startswith("ISS_")]
        if acquirer_ids and issuer_ids:
            return (
                "confirm_money_flow_path",
                {
                    "acquirer_id": acquirer_ids[0],
                    "issuer_id": issuer_ids[0],
                    "transaction_ids": transaction_ids,
                    "min_transaction_amount": 0.0,
                    "min_path_count": 1,
                },
            )

    if spec.projection_id == "decline_routing" and claim.entities:
        acquirer_ids = [entity for entity in claim.entities if entity.startswith("ACQ_")]
        if acquirer_ids:
            return (
                "confirm_decline_routing",
                {
                    "acquirer_ids": acquirer_ids,
                    "min_decline_count": 1,
                    "min_transaction_count": 1,
                },
            )

    return None


def _preferred_entities(
    spec: BranchSpec,
    node_ids: list[int],
    node_details: dict[int, dict[str, Any]],
) -> list[str]:
    label_preference = {
        "shared_device_ring": "Customer",
        "shared_card_ring": "Customer",
        "ip_cohort": "Customer",
        "merchant_cochargeback": "Merchant",
        "payout_cluster": "Merchant",
        "money_flow": None,
        "decline_routing": "Acquirer",
    }
    preferred_label = label_preference.get(spec.projection_id)

    preferred = [
        node_details[node_id]["entity_id"]
        for node_id in node_ids
        if node_id in node_details
        and (
            preferred_label is None
            or preferred_label in node_details[node_id].get("labels", [])
        )
    ]
    if preferred:
        return sorted(preferred)

    return sorted(
        node_details[node_id]["entity_id"]
        for node_id in node_ids
        if node_id in node_details
    )
