import pytest
import pytest_asyncio
from pathlib import Path
from gds.driver import get_driver, close_driver


async def _drop_all_graphs(session):
    result = await session.run(
        """
        CALL gds.graph.list()
        YIELD graphName
        RETURN collect(graphName) AS graph_names
        """
    )
    record = await result.single()
    graph_names = record["graph_names"] if record is not None else []

    for graph_name in graph_names:
        drop_result = await session.run(
            "CALL gds.graph.drop($graph_name, false) YIELD graphName",
            {"graph_name": graph_name},
        )
        await drop_result.consume()


@pytest_asyncio.fixture
async def neo4j_session():
    """
    Function-scoped fixture that provides a Neo4j async session with seeded graph.
    Loads graph_seed.cypher and executes it.
    """
    driver = get_driver()
    async with driver.session() as session:
        # Start each test from a clean graph and no leftover projections.
        try:
            await _drop_all_graphs(session)
        except Exception:
            pass

        result = await session.run("MATCH (n) DETACH DELETE n")
        await result.consume()

        # Read the seed Cypher file
        seed_file = Path(__file__).parent / "fixtures" / "graph_seed.cypher"
        seed_cypher = seed_file.read_text()

        # Execute each statement separately (split on ';')
        statements = [
            stmt.strip()
            for stmt in seed_cypher.split(";")
            if stmt.strip()
        ]

        for statement in statements:
            result = await session.run(statement)
            await result.consume()

        try:
            yield session
        finally:
            try:
                await _drop_all_graphs(session)
            except Exception:
                pass  # Projection may not exist

    await close_driver()
