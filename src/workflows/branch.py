from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from contracts.branch import BranchSpec, StructuralEvidence

with workflow.unsafe.imports_passed_through():
    from activities.drop import drop_projection_activity
    from activities.interpret import interpret_result_activity
    from activities.project import project_graph_activity
    from activities.run_algo import run_algorithm_activity
    from activities.verify import verify_with_cypher_activity


ACTIVITY_TIMEOUT = timedelta(seconds=60)
ACTIVITY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_attempts=3,
)


@workflow.defn(sandboxed=False)
class ReasoningBranchWorkflow:
    @workflow.run
    async def run(self, spec: BranchSpec) -> StructuralEvidence:
        started_at = workflow.now()
        projection_name = f"{spec.projection_id}__{workflow.info().run_id.replace('-', '')}"
        projected_spec = spec.model_copy(
            update={"params": {**spec.params, "name": projection_name}}
        )

        handle = await workflow.execute_activity(
            project_graph_activity,
            projected_spec,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )

        try:
            algorithm_rows = await workflow.execute_activity(
                run_algorithm_activity,
                args=[projected_spec, handle],
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=ACTIVITY_RETRY_POLICY,
            )
            claim = await workflow.execute_activity(
                interpret_result_activity,
                args=[spec, algorithm_rows],
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=ACTIVITY_RETRY_POLICY,
            )
            verifier = await workflow.execute_activity(
                verify_with_cypher_activity,
                args=[spec, claim],
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=ACTIVITY_RETRY_POLICY,
            )
        finally:
            await workflow.execute_activity(
                drop_projection_activity,
                handle,
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=ACTIVITY_RETRY_POLICY,
            )

        finished_at = workflow.now()
        latency_ms = int((finished_at - started_at).total_seconds() * 1000)
        confidence = 0.9 if verifier.outcome == "AGREE" else 0.3

        return StructuralEvidence(
            branch_spec=spec,
            claim=claim.model_dump(),
            verifier=verifier.model_dump(),
            confidence=confidence,
            latency_ms=latency_ms,
        )
