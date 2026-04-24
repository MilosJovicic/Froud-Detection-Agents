import time
import uuid
from pathlib import Path

import pytest
import yaml
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from activities.aggregate import aggregate_evidence_activity
from activities.drop import drop_projection_activity
from activities.interpret import interpret_result_activity
from activities.parse import parse_hypothesis_activity
from activities.plan import plan_branches_activity
from activities.project import project_graph_activity
from activities.run_algo import run_algorithm_activity
from activities.synthesize import synthesize_verdict_activity
from activities.verify import verify_with_cypher_activity
from contracts.verdict import FraudVerdict
from workflows.branch import ReasoningBranchWorkflow
from workflows.investigation import FraudInvestigationWorkflow


@pytest.fixture(autouse=True)
def _force_rule_based_agents(monkeypatch):
    monkeypatch.setenv("ROUTER_MODE", "rule-based")
    monkeypatch.setenv("PLANNER_MODE", "rule-based")
    monkeypatch.setenv("INTERPRETER_MODE", "rule-based")
    monkeypatch.setenv("VERIFIER_MODE", "rule-based")
    monkeypatch.setenv("SYNTHESIZER_MODE", "rule-based")


@pytest.mark.asyncio
async def test_demo_scenarios_produce_fraud_verdicts_under_60_seconds(neo4j_session):
    del neo4j_session

    scenarios_path = Path("evals/scenarios.yaml")
    scenarios = yaml.safe_load(scenarios_path.read_text())["scenarios"]
    task_queue = f"investigation-{uuid.uuid4()}"

    started_at = time.perf_counter()

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
                project_graph_activity,
                run_algorithm_activity,
                interpret_result_activity,
                verify_with_cypher_activity,
                drop_projection_activity,
                aggregate_evidence_activity,
                synthesize_verdict_activity,
            ],
        ):
            verdicts = []
            for scenario in scenarios:
                verdict = await env.client.execute_workflow(
                    FraudInvestigationWorkflow.run,
                    scenario["hypothesis"],
                    id=f"{task_queue}-{scenario['name']}",
                    task_queue=task_queue,
                )
                verdicts.append(verdict)

    elapsed = time.perf_counter() - started_at

    assert elapsed < 60
    assert len(verdicts) == 3
    for verdict, scenario in zip(verdicts, scenarios):
        assert isinstance(verdict, FraudVerdict)
        assert verdict.trace_id
        assert verdict.hypothesis.claim_type.value == scenario["expected_claim_type"]
        assert verdict.evidence
        assert verdict.final_assessment
        assert verdict.recommended_actions
        assert 0.0 <= verdict.confidence <= 1.0
