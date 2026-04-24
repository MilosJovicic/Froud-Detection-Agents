import uuid
from pathlib import Path

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from activities.aggregate import aggregate_evidence_activity
from activities.compose import compose_projection_activity
from activities.drop import drop_projection_activity
from activities.interpret import interpret_result_activity
from activities.parse import parse_hypothesis_activity
from activities.plan import plan_branches_activity
from activities.project import project_graph_activity
from activities.run_algo import run_algorithm_activity
from activities.synthesize import synthesize_verdict_activity
from activities.verify import verify_with_cypher_activity
from workflows.branch import ReasoningBranchWorkflow
from workflows.investigation import FraudInvestigationWorkflow


@pytest.fixture(autouse=True)
def _force_rule_based_agents(monkeypatch):
    monkeypatch.setenv("ROUTER_MODE", "rule-based")
    monkeypatch.setenv("PLANNER_MODE", "rule-based")
    monkeypatch.setenv("INTERPRETER_MODE", "rule-based")
    monkeypatch.setenv("VERIFIER_MODE", "rule-based")
    monkeypatch.setenv("SYNTHESIZER_MODE", "rule-based")
    monkeypatch.setenv("COMPOSER_MODE", "rule-based")
    monkeypatch.setenv("ALLOW_COMPOSER_AUTOEXEC", "false")


@pytest.mark.asyncio
async def test_custom_hypothesis_produces_review_only_projection_proposal(neo4j_session):
    del neo4j_session
    task_queue = f"custom-{uuid.uuid4()}"
    hypothesis = (
        "Investigate merchants with overlapping customers across GB and CY that do not "
        "fit the standard fraud templates."
    )

    async with await WorkflowEnvironment.start_local(
        data_converter=pydantic_data_converter,
        dev_server_log_level="error",
    ) as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[FraudInvestigationWorkflow, ReasoningBranchWorkflow],
            activities=[
                parse_hypothesis_activity,
                plan_branches_activity,
                compose_projection_activity,
                project_graph_activity,
                run_algorithm_activity,
                interpret_result_activity,
                verify_with_cypher_activity,
                drop_projection_activity,
                aggregate_evidence_activity,
                synthesize_verdict_activity,
            ],
        ):
            verdict = await env.client.execute_workflow(
                FraudInvestigationWorkflow.run,
                hypothesis,
                id=f"{task_queue}-wf",
                task_queue=task_queue,
            )

    assert verdict.hypothesis.claim_type.value == "custom"
    assert verdict.evidence == []
    assert verdict.confidence == 0.2
    assert "review-only projection proposal" in verdict.final_assessment
    proposal_action = verdict.recommended_actions[0]
    proposal_path = Path(proposal_action.removeprefix("Review the queued projection proposal at "))
    assert proposal_path.exists()
