from temporalio import activity

from gds.driver import get_driver
from gds.projections import ProjectionHandle
from metrics import instrument_activity


@activity.defn(name="drop_projection")
@instrument_activity("drop_projection")
async def drop_projection_activity(handle: ProjectionHandle) -> None:
    driver = get_driver()
    async with driver.session() as session:
        try:
            result = await session.run(
                "CALL gds.graph.drop($graph_name, false) YIELD graphName",
                {"graph_name": handle.name},
            )
            await result.consume()
        except Exception:
            pass
