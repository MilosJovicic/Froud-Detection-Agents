from temporalio import activity

from agents.planner import plan_branches
from contracts.branch import BranchSpec
from contracts.hypothesis import Hypothesis
from metrics import instrument_activity


@activity.defn(name="plan_branches")
@instrument_activity("plan_branches")
async def plan_branches_activity(hypothesis: Hypothesis) -> list[BranchSpec]:
    return await plan_branches(hypothesis)
