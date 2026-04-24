import pytest

from gds.algorithms import LIBRARY as ALGORITHM_LIBRARY
from gds.algorithms import PROJECTION_ALGORITHM_CATALOG
from gds.projections import LIBRARY as PROJECTION_LIBRARY


EXPECTED_PROJECTIONS = {
    "shared_device_ring": {"node_count": 27, "rel_count": 24},
    "shared_card_ring": {"node_count": 11, "rel_count": 8},
    "ip_cohort": {"node_count": 11, "rel_count": 8},
    "merchant_cochargeback": {"node_count": 3, "rel_count": 4},
    "money_flow": {"node_count": 11, "rel_count": 9},
    "payout_cluster": {"node_count": 5, "rel_count": 4},
    "decline_routing": {"node_count": 7, "rel_count": 4},
}


ALGORITHM_SMOKE_CASES = [
    ("wcc", "shared_device_ring", {"nodeId", "componentId"}),
    ("louvain", "shared_device_ring", {"nodeId", "communityId"}),
    ("pagerank", "merchant_cochargeback", {"nodeId", "score"}),
    ("node_similarity", "shared_card_ring", {"node1", "node2", "similarity"}),
    ("betweenness", "money_flow", {"nodeId", "score"}),
    ("fastrp_knn", "shared_card_ring", {"node1", "node2", "similarity"}),
]


@pytest.mark.asyncio
async def test_projection_library_matches_claude_spec():
    assert set(PROJECTION_LIBRARY) == set(EXPECTED_PROJECTIONS)


@pytest.mark.asyncio
async def test_algorithm_library_matches_claude_spec():
    assert set(ALGORITHM_LIBRARY) == {
        "wcc",
        "louvain",
        "pagerank",
        "node_similarity",
        "betweenness",
        "fastrp_knn",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("projection_id", "expected"),
    EXPECTED_PROJECTIONS.items(),
)
async def test_projection_catalog_counts_match_seed(neo4j_session, projection_id, expected):
    handle = await PROJECTION_LIBRARY[projection_id](
        neo4j_session,
        name=f"{projection_id}__counts",
    )

    assert handle.name == f"{projection_id}__counts"
    assert handle.node_count == expected["node_count"]
    assert handle.rel_count == expected["rel_count"]
    assert handle.created_at.endswith("+00:00")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("algorithm_id", "projection_id", "expected_keys"),
    ALGORITHM_SMOKE_CASES,
)
async def test_each_algorithm_returns_expected_shape(
    neo4j_session,
    algorithm_id,
    projection_id,
    expected_keys,
):
    handle = await PROJECTION_LIBRARY[projection_id](
        neo4j_session,
        name=f"{projection_id}__{algorithm_id}",
    )

    rows = await ALGORITHM_LIBRARY[algorithm_id](neo4j_session, handle)

    assert rows, f"{algorithm_id} returned no rows on {projection_id}"
    assert expected_keys.issubset(rows[0].keys())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("projection_id", "algorithm_id"),
    [
        (projection_id, algorithm_id)
        for projection_id, algorithm_ids in PROJECTION_ALGORITHM_CATALOG.items()
        for algorithm_id in algorithm_ids
    ],
)
async def test_catalog_projection_algorithm_pairs_execute_without_error(
    neo4j_session,
    projection_id,
    algorithm_id,
):
    handle = await PROJECTION_LIBRARY[projection_id](
        neo4j_session,
        name=f"{projection_id}__{algorithm_id}__catalog",
    )

    rows = await ALGORITHM_LIBRARY[algorithm_id](neo4j_session, handle)

    assert handle.node_count > 0
    assert handle.rel_count > 0
    assert isinstance(rows, list)
