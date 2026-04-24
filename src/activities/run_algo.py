from temporalio import activity

from activities._utils import execution_params
from contracts.branch import BranchSpec
from gds.algorithms import LIBRARY
from gds.driver import get_driver
from gds.projections import ProjectionHandle
from metrics import instrument_activity


@activity.defn(name="run_algorithm")
@instrument_activity("run_algorithm")
async def run_algorithm_activity(
    spec: BranchSpec,
    handle: ProjectionHandle,
) -> list[dict]:
    driver = get_driver()
    async with driver.session() as session:
        algorithm = LIBRARY[spec.algorithm_id]
        return await algorithm(session, handle, **execution_params(spec, algorithm))
