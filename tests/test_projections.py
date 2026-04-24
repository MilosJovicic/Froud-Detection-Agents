import pytest
from gds.projections import project_shared_device_ring

@pytest.mark.asyncio
async def test_shared_device_ring_returns_handle(neo4j_session):
    """Test that shared_device_ring projection returns a valid ProjectionHandle."""
    handle = await project_shared_device_ring(
        neo4j_session,
        min_customers_per_device=2
    )

    assert handle.name == "shared_device_ring"
    assert handle.node_count == 27  # 24 ring customers + 3 devices
    assert handle.rel_count == 24   # 24 USES_DEVICE edges
    assert handle.created_at is not None
    assert len(handle.created_at) > 0

@pytest.mark.asyncio
async def test_shared_device_ring_three_components_of_eight(neo4j_session):
    """
    Test that WCC on the projection finds exactly 3 components,
    each with exactly 8 Customer nodes.
    """
    from gds.algorithms import run as wcc_run

    handle = await project_shared_device_ring(
        neo4j_session,
        min_customers_per_device=2
    )

    wcc_result = await wcc_run(neo4j_session, handle)

    # Group by component ID
    component_map = {}
    for row in wcc_result:
        comp_id = row["componentId"]
        if comp_id not in component_map:
            component_map[comp_id] = []
        component_map[comp_id].append(row["nodeId"])

    # Assert exactly 3 components
    assert len(component_map) == 3, f"Expected 3 components, got {len(component_map)}"

    # Fetch labels for all node IDs
    all_node_ids = [row["nodeId"] for row in wcc_result]
    label_result = await neo4j_session.run(
        "MATCH (n) WHERE id(n) IN $ids RETURN id(n) AS nid, labels(n) AS lbls",
        {"ids": all_node_ids}
    )

    node_labels = {}
    async for record in label_result:
        node_labels[record["nid"]] = record["lbls"]

    # Assert each component has exactly 8 Customer nodes
    for comp_id, node_ids in component_map.items():
        customer_count = sum(
            1 for nid in node_ids if "Customer" in node_labels.get(nid, [])
        )
        assert customer_count == 8, (
            f"Component {comp_id} has {customer_count} customers, expected 8"
        )

@pytest.mark.asyncio
async def test_isolate_devices_excluded(neo4j_session):
    """
    Test that isolate devices (with only 1 customer) are excluded from projection.
    """
    handle = await project_shared_device_ring(
        neo4j_session,
        min_customers_per_device=2
    )

    # 27 nodes = 24 ring customers + 3 ring devices
    # Isolate customers (4) and isolate devices (4) should NOT be included
    assert handle.node_count == 27, (
        f"Expected 27 nodes (24 customers + 3 devices), got {handle.node_count}"
    )
