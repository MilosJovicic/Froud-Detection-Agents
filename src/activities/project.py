from temporalio import activity

from activities._utils import execution_params
from contracts.branch import BranchSpec
from gds.driver import get_driver
from gds.projections import LIBRARY, ProjectionHandle
from metrics import instrument_activity


@activity.defn(name="project_graph")
@instrument_activity("project_graph")
async def project_graph_activity(spec: BranchSpec) -> ProjectionHandle:
    driver = get_driver()
    async with driver.session() as session:
        projection = LIBRARY[spec.projection_id]
        return await projection(session, **execution_params(spec, projection))
