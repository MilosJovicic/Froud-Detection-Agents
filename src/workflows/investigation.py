import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from contracts.hypothesis import Hypothesis
from contracts.verdict import FraudVerdict

with workflow.unsafe.imports_passed_through():
    from activities.aggregate import aggregate_evidence_activity
    from activities.compose import compose_projection_activity
    from activities.parse import parse_hypothesis_activity
    from activities.plan import plan_branches_activity
    from activities.synthesize import synthesize_verdict_activity
    from workflows.branch import ReasoningBranchWorkflow


WORKFLOW_TIMEOUT = timedelta(minutes=5)
ACTIVITY_TIMEOUT = timedelta(seconds=60)
ACTIVITY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_attempts=3,
    non_retryable_error_types=["ValidationError"],
)


@workflow.defn(sandboxed=False)
class FraudInvestigationWorkflow:
    @workflow.run
    async def run(self, hypothesis_input: Hypothesis | str) -> FraudVerdict:
        trace_id = workflow.info().workflow_id

        if isinstance(hypothesis_input, str):
            hypothesis = await workflow.execute_activity(
                parse_hypothesis_activity,
                hypothesis_input,
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=ACTIVITY_RETRY_POLICY,
            )
        else:
            hypothesis = hypothesis_input

        if hypothesis.claim_type.value == "custom":
            proposal = await workflow.execute_activity(
                compose_projection_activity,
                args=[hypothesis, trace_id],
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=ACTIVITY_RETRY_POLICY,
            )
            return FraudVerdict(
                hypothesis=hypothesis,
                evidence=[],
                final_assessment=(
                    "No pre-registered graph library branch matched this custom hypothesis. "
                    f"A review-only projection proposal was queued at {proposal.file_path}."
                ),
                confidence=0.2,
                recommended_actions=[
                    f"Review the queued projection proposal at {proposal.file_path}",
                    "Do not execute the proposal without analyst approval",
                ],
                trace_id=trace_id,
            )

        branch_specs = await workflow.execute_activity(
            plan_branches_activity,
            hypothesis,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )

        branch_results = await asyncio.gather(
            *[
                workflow.execute_child_workflow(
                    ReasoningBranchWorkflow.run,
                    branch_spec,
                    id=f"{workflow.info().workflow_id}__branch__{index}",
                    task_queue=workflow.info().task_queue,
                    execution_timeout=WORKFLOW_TIMEOUT,
                )
                for index, branch_spec in enumerate(branch_specs, start=1)
            ]
        )

        aggregation = await workflow.execute_activity(
            aggregate_evidence_activity,
            branch_results,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )

        return await workflow.execute_activity(
            synthesize_verdict_activity,
            args=[hypothesis, aggregation, trace_id],
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
