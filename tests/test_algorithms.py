import pytest
from gds.projections import project_shared_device_ring
from gds.algorithms import run as wcc_run

@pytest.mark.asyncio
async def test_wcc_returns_list_of_dicts(neo4j_session):
    """Test that WCC returns a list of dicts with nodeId and componentId."""
    handle = await project_shared_device_ring(
        neo4j_session,
        min_customers_per_device=2
    )

    result = await wcc_run(neo4j_session, handle)

    assert isinstance(result, list)
    assert len(result) > 0
    for record in result:
        assert isinstance(record, dict)
        assert "nodeId" in record
        assert "componentId" in record
        assert isinstance(record["nodeId"], int)
        assert isinstance(record["componentId"], int)

@pytest.mark.asyncio
async def test_wcc_component_count(neo4j_session):
    """Test that WCC finds exactly 3 components."""
    handle = await project_shared_device_ring(
        neo4j_session,
        min_customers_per_device=2
    )

    result = await wcc_run(neo4j_session, handle)

    component_ids = set(row["componentId"] for row in result)
    assert len(component_ids) == 3, (
        f"Expected 3 components, got {len(component_ids)}"
    )

@pytest.mark.asyncio
async def test_wcc_no_cross_ring_contamination(neo4j_session):
    """
    Test that each WCC component contains only customers from one ring
    (no cross-ring connections).
    """
    handle = await project_shared_device_ring(
        neo4j_session,
        min_customers_per_device=2
    )

    result = await wcc_run(neo4j_session, handle)

    # Group by component
    component_map = {}
    for row in result:
        comp_id = row["componentId"]
        if comp_id not in component_map:
            component_map[comp_id] = []
        component_map[comp_id].append(row["nodeId"])

    # Fetch labels and IDs for all nodes
    all_node_ids = [row["nodeId"] for row in result]
    node_info_result = await neo4j_session.run(
        "MATCH (n) WHERE id(n) IN $ids RETURN id(n) AS nid, n.id AS node_id, labels(n) AS lbls",
        {"ids": all_node_ids}
    )

    node_info = {}
    async for record in node_info_result:
        node_info[record["nid"]] = {
            "id": record["node_id"],
            "labels": record["lbls"]
        }

    # For each component, extract the ring number from customer IDs
    for comp_id, node_ids in component_map.items():
        customer_node_ids = [
            nid for nid in node_ids
            if "Customer" in node_info.get(nid, {}).get("labels", [])
        ]

        # Extract ring prefix (C1_, C2_, or C3_) from all customer IDs
        ring_prefixes = set()
        for nid in customer_node_ids:
            node_id = node_info[nid]["id"]
            prefix = node_id[:2]  # "C1", "C2", or "C3"
            ring_prefixes.add(prefix)

        # Each component should have customers from only one ring
        assert len(ring_prefixes) == 1, (
            f"Component {comp_id} has customers from multiple rings: {ring_prefixes}"
        )
