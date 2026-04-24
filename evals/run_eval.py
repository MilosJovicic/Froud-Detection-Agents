import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

load_dotenv(ROOT / ".env")

import yaml
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
from gds.driver import close_driver, get_driver
from metrics import reset_metrics, snapshot_metrics
from workflows.branch import ReasoningBranchWorkflow
from workflows.investigation import FraudInvestigationWorkflow


async def run_eval(
    *,
    scenario_file: Path,
    output_path: Path,
) -> dict[str, Any]:
    scenarios = yaml.safe_load(scenario_file.read_text())["scenarios"]
    reset_metrics()
    await _seed_graph()

    task_queue = f"eval-{uuid.uuid4()}"
    started_at = time.perf_counter()
    scenario_results: list[dict[str, Any]] = []

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
            for scenario in scenarios:
                workflow_started_at = time.perf_counter()
                verdict = await env.client.execute_workflow(
                    FraudInvestigationWorkflow.run,
                    scenario["hypothesis"],
                    id=f"{task_queue}-{scenario['name']}",
                    task_queue=task_queue,
                )
                wall_time_ms = round((time.perf_counter() - workflow_started_at) * 1000, 3)
                scenario_results.append(
                    {
                        "scenario": scenario,
                        "verdict": verdict,
                        "wall_time_ms": wall_time_ms,
                    }
                )

    total_wall_time_ms = round((time.perf_counter() - started_at) * 1000, 3)
    metrics_snapshot = snapshot_metrics()
    report = _build_report(
        scenario_results,
        metrics_snapshot,
        total_wall_time_ms=total_wall_time_ms,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))
    await close_driver()
    return report


def _build_report(
    scenario_results: list[dict[str, Any]],
    metrics_snapshot: dict[str, Any],
    *,
    total_wall_time_ms: float,
) -> dict[str, Any]:
    scenarios_report = [
        _build_scenario_report(
            item["scenario"],
            item["verdict"],
            item["wall_time_ms"],
            metrics_snapshot["agent_token_metrics"]["by_workflow"],
        )
        for item in scenario_results
    ]

    scenario_successes = [
        scenario
        for scenario in scenarios_report
        if scenario["claim_type_match"] and scenario["primary_branch_match"] is not False
    ]
    claim_type_matches = [scenario for scenario in scenarios_report if scenario["claim_type_match"]]
    primary_branch_matches = [
        scenario
        for scenario in scenarios_report
        if scenario["primary_branch_match"] is True
    ]

    all_branch_results = [
        branch
        for scenario in scenarios_report
        for branch in scenario["branch_results"]
    ]
    total_estimated_tokens = sum(
        branch["estimated_token_count"] for branch in all_branch_results
    ) + sum(
        scenario["top_level_estimated_token_count"] for scenario in scenarios_report
    )

    summary = {
        "scenario_count": len(scenarios_report),
        "successful_scenarios": len(scenario_successes),
        "scenario_accuracy": _ratio(len(scenario_successes), len(scenarios_report)),
        "claim_type_accuracy": _ratio(len(claim_type_matches), len(scenarios_report)),
        "primary_branch_accuracy": _ratio(len(primary_branch_matches), len(scenarios_report)),
        "total_wall_time_ms": total_wall_time_ms,
        "average_wall_time_ms": round(
            total_wall_time_ms / len(scenarios_report),
            3,
        ) if scenarios_report else 0.0,
        "branch_count": len(all_branch_results),
        "average_branch_latency_ms": round(
            sum(branch["latency_ms"] for branch in all_branch_results) / len(all_branch_results),
            3,
        ) if all_branch_results else 0.0,
        "total_estimated_tokens": total_estimated_tokens,
    }

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": summary,
        "config": _config_snapshot(),
        "scenarios": scenarios_report,
        "per_activity_metrics": metrics_snapshot["activity_metrics"],
        "activity_events": metrics_snapshot["activity_events"],
        "agent_token_metrics": metrics_snapshot["agent_token_metrics"],
        "token_count_method": "Estimated from serialized prompt and output length using ~4 characters per token.",
    }


def _build_scenario_report(
    scenario: dict[str, Any],
    verdict,
    wall_time_ms: float,
    token_metrics_by_workflow: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    expected_projection_id = scenario.get("expected_projection_id")
    expected_algorithm_id = scenario.get("expected_algorithm_id")
    top_level_tokens = token_metrics_by_workflow.get(verdict.trace_id, {})

    branch_results = []
    for index, evidence in enumerate(verdict.evidence, start=1):
        branch_workflow_id = f"{verdict.trace_id}__branch__{index}"
        branch_tokens = token_metrics_by_workflow.get(branch_workflow_id, {})
        branch_results.append(
            {
                "index": index,
                "branch_workflow_id": branch_workflow_id,
                "projection_id": evidence.branch_spec.projection_id,
                "algorithm_id": evidence.branch_spec.algorithm_id,
                "verifier_outcome": evidence.verifier.outcome,
                "latency_ms": evidence.latency_ms,
                "confidence": evidence.confidence,
                "matches_expected": (
                    evidence.branch_spec.projection_id == expected_projection_id
                    and evidence.branch_spec.algorithm_id == expected_algorithm_id
                ),
                "estimated_token_count": branch_tokens.get("total_estimated_tokens", 0),
                "estimated_prompt_tokens": branch_tokens.get("prompt_tokens", 0),
                "estimated_completion_tokens": branch_tokens.get("completion_tokens", 0),
            }
        )

    primary_branch_match = None
    if branch_results and expected_projection_id and expected_algorithm_id:
        primary_branch_match = branch_results[0]["matches_expected"]

    return {
        "name": scenario["name"],
        "hypothesis": scenario["hypothesis"],
        "trace_id": verdict.trace_id,
        "claim_type": verdict.hypothesis.claim_type.value,
        "expected_claim_type": scenario.get("expected_claim_type"),
        "claim_type_match": verdict.hypothesis.claim_type.value == scenario.get("expected_claim_type"),
        "wall_time_ms": wall_time_ms,
        "confidence": verdict.confidence,
        "final_assessment": verdict.final_assessment,
        "recommended_actions": verdict.recommended_actions,
        "primary_branch_match": primary_branch_match,
        "top_level_estimated_token_count": top_level_tokens.get("total_estimated_tokens", 0),
        "branch_results": branch_results,
    }


def _config_snapshot() -> dict[str, str]:
    keys = [
        "ROUTER_MODE",
        "PLANNER_MODE",
        "INTERPRETER_MODE",
        "VERIFIER_MODE",
        "SYNTHESIZER_MODE",
        "COMPOSER_MODE",
        "ALLOW_COMPOSER_AUTOEXEC",
        "OLLAMA_MODEL",
        "OLLAMA_URL",
    ]
    return {key: os.getenv(key, "") for key in keys}


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 3)


async def _seed_graph() -> None:
    driver = get_driver()
    async with driver.session() as session:
        try:
            await _drop_all_graphs(session)
        except Exception:
            pass

        result = await session.run("MATCH (n) DETACH DELETE n")
        await result.consume()

        seed_file = ROOT / "tests" / "fixtures" / "graph_seed.cypher"
        statements = [
            statement.strip()
            for statement in seed_file.read_text().split(";")
            if statement.strip()
        ]

        for statement in statements:
            result = await session.run(statement)
            await result.consume()


async def _drop_all_graphs(session) -> None:
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the fraud-agent evaluation harness.")
    parser.add_argument(
        "--scenario-file",
        default=str(ROOT / "evals" / "scenarios.yaml"),
        help="Path to the YAML scenario file.",
    )
    parser.add_argument(
        "--output",
        default=str(ROOT / "evals" / "reports" / "latest.json"),
        help="Path to the output JSON report.",
    )
    parser.add_argument("--router", choices=["llm", "rule-based"])
    parser.add_argument("--planner", choices=["llm", "rule-based"])
    parser.add_argument("--interpreter", choices=["llm", "rule-based"])
    parser.add_argument("--verifier", choices=["llm", "rule-based"])
    parser.add_argument("--synthesizer", choices=["llm", "rule-based"])
    parser.add_argument("--composer", choices=["llm", "rule-based"])
    parser.add_argument("--allow-composer-autoexec", action="store_true")
    return parser.parse_args()


def _apply_overrides(args: argparse.Namespace) -> None:
    overrides = {
        "ROUTER_MODE": args.router,
        "PLANNER_MODE": args.planner,
        "INTERPRETER_MODE": args.interpreter,
        "VERIFIER_MODE": args.verifier,
        "SYNTHESIZER_MODE": args.synthesizer,
        "COMPOSER_MODE": args.composer,
    }
    for key, value in overrides.items():
        if value:
            os.environ[key] = value

    if args.allow_composer_autoexec:
        os.environ["ALLOW_COMPOSER_AUTOEXEC"] = "true"


def main() -> int:
    args = _parse_args()
    _apply_overrides(args)
    report = asyncio.run(
        run_eval(
            scenario_file=Path(args.scenario_file),
            output_path=Path(args.output),
        )
    )
    print(json.dumps({"output": args.output, "summary": report["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
