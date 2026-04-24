from typing import Any

from gds.projections import ProjectionHandle


PROJECTION_ALGORITHM_CATALOG = {
    "shared_device_ring": ("wcc", "louvain", "pagerank"),
    "shared_card_ring": ("wcc", "louvain"),
    "ip_cohort": ("wcc", "louvain", "pagerank"),
    "merchant_cochargeback": ("louvain", "pagerank", "betweenness"),
    "money_flow": ("betweenness", "pagerank"),
    "payout_cluster": ("wcc", "louvain"),
    "decline_routing": ("pagerank",),
}


def _validate_int(name: str, value: int, *, minimum: int = 1) -> int:
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _validate_float(
    name: str,
    value: float,
    *,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> float:
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


async def _collect_rows(
    session: Any,
    query: str,
    parameters: dict[str, Any],
) -> list[dict]:
    result = await session.run(query, parameters)
    rows: list[dict] = []
    async for record in result:
        rows.append(dict(record))
    return rows


async def run_wcc(
    session: Any,
    handle: ProjectionHandle,
    **_: Any,
) -> list[dict]:
    return await _collect_rows(
        session,
        """
        CALL gds.wcc.stream($graph_name, {})
        YIELD nodeId, componentId
        RETURN nodeId, componentId
        ORDER BY componentId, nodeId
        """,
        {"graph_name": handle.name},
    )


async def run_louvain(
    session: Any,
    handle: ProjectionHandle,
    *,
    max_levels: int = 10,
    max_iterations: int = 10,
) -> list[dict]:
    _validate_int("max_levels", max_levels)
    _validate_int("max_iterations", max_iterations)

    return await _collect_rows(
        session,
        """
        CALL gds.louvain.stream(
            $graph_name,
            {maxLevels: $max_levels, maxIterations: $max_iterations}
        )
        YIELD nodeId, communityId
        RETURN nodeId, communityId
        ORDER BY communityId, nodeId
        """,
        {
            "graph_name": handle.name,
            "max_levels": max_levels,
            "max_iterations": max_iterations,
        },
    )


async def run_pagerank(
    session: Any,
    handle: ProjectionHandle,
    *,
    max_iterations: int = 20,
    damping_factor: float = 0.85,
) -> list[dict]:
    _validate_int("max_iterations", max_iterations)
    _validate_float("damping_factor", damping_factor, minimum=0.0, maximum=1.0)

    return await _collect_rows(
        session,
        """
        CALL gds.pageRank.stream(
            $graph_name,
            {maxIterations: $max_iterations, dampingFactor: $damping_factor}
        )
        YIELD nodeId, score
        RETURN nodeId, score
        ORDER BY score DESC, nodeId
        """,
        {
            "graph_name": handle.name,
            "max_iterations": max_iterations,
            "damping_factor": damping_factor,
        },
    )


async def run_node_similarity(
    session: Any,
    handle: ProjectionHandle,
    *,
    top_k: int = 10,
    similarity_cutoff: float = 0.1,
) -> list[dict]:
    _validate_int("top_k", top_k)
    _validate_float("similarity_cutoff", similarity_cutoff, minimum=0.0, maximum=1.0)

    return await _collect_rows(
        session,
        """
        CALL gds.nodeSimilarity.stream(
            $graph_name,
            {topK: $top_k, similarityCutoff: $similarity_cutoff}
        )
        YIELD node1, node2, similarity
        RETURN node1, node2, similarity
        ORDER BY similarity DESC, node1, node2
        """,
        {
            "graph_name": handle.name,
            "top_k": top_k,
            "similarity_cutoff": similarity_cutoff,
        },
    )


async def run_betweenness(
    session: Any,
    handle: ProjectionHandle,
    *,
    sampling_size: int = 4,
) -> list[dict]:
    _validate_int("sampling_size", sampling_size)

    return await _collect_rows(
        session,
        """
        CALL gds.betweenness.stream(
            $graph_name,
            {samplingSize: $sampling_size, samplingSeed: 42}
        )
        YIELD nodeId, score
        RETURN nodeId, score
        ORDER BY score DESC, nodeId
        """,
        {
            "graph_name": handle.name,
            "sampling_size": sampling_size,
        },
    )


async def run_fastrp_knn(
    session: Any,
    handle: ProjectionHandle,
    *,
    embedding_dimension: int = 16,
    top_k: int = 5,
    similarity_cutoff: float = 0.0,
) -> list[dict]:
    _validate_int("embedding_dimension", embedding_dimension)
    _validate_int("top_k", top_k)
    _validate_float("similarity_cutoff", similarity_cutoff, minimum=0.0, maximum=1.0)

    embedding_property = "__fastrp_embedding__"

    mutate_result = await session.run(
        """
        CALL gds.fastRP.mutate(
            $graph_name,
            {
                mutateProperty: $embedding_property,
                embeddingDimension: $embedding_dimension,
                randomSeed: 42,
                concurrency: 1
            }
        )
        YIELD nodePropertiesWritten
        RETURN nodePropertiesWritten
        """,
        {
            "graph_name": handle.name,
            "embedding_property": embedding_property,
            "embedding_dimension": embedding_dimension,
        },
    )
    await mutate_result.consume()

    return await _collect_rows(
        session,
        """
        CALL gds.knn.stream(
            $graph_name,
            {
                nodeProperties: [$embedding_property],
                topK: $top_k,
                similarityCutoff: $similarity_cutoff,
                randomSeed: 42,
                concurrency: 1
            }
        )
        YIELD node1, node2, similarity
        RETURN node1, node2, similarity
        ORDER BY similarity DESC, node1, node2
        """,
        {
            "graph_name": handle.name,
            "embedding_property": embedding_property,
            "top_k": top_k,
            "similarity_cutoff": similarity_cutoff,
        },
    )


run = run_wcc


LIBRARY = {
    "wcc": run_wcc,
    "louvain": run_louvain,
    "pagerank": run_pagerank,
    "node_similarity": run_node_similarity,
    "betweenness": run_betweenness,
    "fastrp_knn": run_fastrp_knn,
}
