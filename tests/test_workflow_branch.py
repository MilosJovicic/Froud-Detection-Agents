import asyncio
import uuid

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from activities.drop import drop_projection_activity
from activities.interpret import interpret_result_activity
from activities.project import project_graph_activity
from activities.run_algo import run_algorithm_activity
from activities.verify import verify_with_cypher_activity
from contracts.branch import BranchSpec
from workflows.branch import ReasoningBranchWorkflow


DEFAULT_BRANCH_SPEC = BranchSpec(
    projection_id="shared_device_ring",
    algorithm_id="wcc",
    params={"min_customers_per_device": 2},
    rationale="Deterministic M3 workflow path",
)


@pytest.fixture(autouse=True)
def _force_rule_based_branch_agents(monkeypatch):
    monkeypatch.setenv("INTERPRETER_MODE", "rule-based")
    monkeypatch.setenv("VERIFIER_MODE", "rule-based")


async def _graph_names(session) -> list[str]:
    result = await session.run(
        """
        CALL gds.graph.list()
        YIELD graphName
        RETURN collect(graphName) AS graph_names
        """
    )
    record = await result.single()
    return sorted(record["graph_names"]) if record is not None else []


@pytest.mark.asyncio
async def test_reasoning_branch_workflow_returns_structural_evidence(neo4j_session):
    task_queue = f"branch-{uuid.uuid4()}"
    async with await WorkflowEnvironment.start_local(
        data_converter=pydantic_data_converter,
        dev_server_log_level="error",
    ) as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[ReasoningBranchWorkflow],
            activities=[
                project_graph_activity,
                run_algorithm_activity,
                interpret_result_activity,
                verify_with_cypher_activity,
                drop_projection_activity,
            ],
        ):
            result = await env.client.execute_workflow(
                ReasoningBranchWorkflow.run,
                DEFAULT_BRANCH_SPEC,
                id=str(uuid.uuid4()),
                task_queue=task_queue,
            )

    assert result.branch_spec == DEFAULT_BRANCH_SPEC
    assert result.claim.topology == "wcc_component"
    assert len(result.claim.entities) == 8
    assert result.claim.score == 8.0
    assert result.verifier.outcome == "AGREE"
    assert result.confidence >= 0.8
    assert result.latency_ms >= 0
    assert await _graph_names(neo4j_session) == []


@pytest.mark.asyncio
async def test_reasoning_branch_workflow_drops_projection_on_failure(neo4j_session):
    failing_spec = DEFAULT_BRANCH_SPEC.model_copy(
        update={
            "params": {
                **DEFAULT_BRANCH_SPEC.params,
                "__inject_failure_stage": "after_algorithm",
            }
        }
    )
    task_queue = f"branch-fail-{uuid.uuid4()}"

    async with await WorkflowEnvironment.start_local(
        data_converter=pydantic_data_converter,
        dev_server_log_level="error",
    ) as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[ReasoningBranchWorkflow],
            activities=[
                project_graph_activity,
                run_algorithm_activity,
                interpret_result_activity,
                verify_with_cypher_activity,
                drop_projection_activity,
            ],
        ):
            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    ReasoningBranchWorkflow.run,
                    failing_spec,
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

    assert await _graph_names(neo4j_session) == []


@pytest.mark.asyncio
async def test_reasoning_branch_workflow_handles_50_parallel_branches_without_projection_leaks(
    neo4j_session,
):
    task_queue = f"branch-parallel-{uuid.uuid4()}"

    async with await WorkflowEnvironment.start_local(
        data_converter=pydantic_data_converter,
        dev_server_log_level="error",
    ) as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[ReasoningBranchWorkflow],
            activities=[
                project_graph_activity,
                run_algorithm_activity,
                interpret_result_activity,
                verify_with_cypher_activity,
                drop_projection_activity,
            ],
        ):
            handles = await asyncio.gather(
                *[
                    env.client.start_workflow(
                        ReasoningBranchWorkflow.run,
                        DEFAULT_BRANCH_SPEC,
                        id=str(uuid.uuid4()),
                        task_queue=task_queue,
                    )
                    for _ in range(50)
                ]
            )
            results = await asyncio.gather(*(handle.result() for handle in handles))

    assert len(results) == 50
    assert all(result.verifier.outcome == "AGREE" for result in results)
    assert await _graph_names(neo4j_session) == []
