from pathlib import Path

import pytest
import yaml

from activities.parse import parse_hypothesis_activity
from activities.plan import plan_branches_activity
from agents.planner import DEFAULT_BRANCH_SPEC, plan_branches
from agents.router import classify_hypothesis
from contracts.branch import BranchSpec
from contracts.hypothesis import ClaimType, Hypothesis


@pytest.mark.asyncio
async def test_router_llm_mode_uses_llm_runner(monkeypatch):
    async def fake_run_router_llm(raw_text: str):
        assert "routing" in raw_text
        return ClaimType.ROUTING_ANOMALY

    monkeypatch.setenv("ROUTER_MODE", "llm")
    monkeypatch.setattr("agents.router._run_router_llm", fake_run_router_llm)

    result = await classify_hypothesis("routing anomaly around an acquirer")
    assert result == ClaimType.ROUTING_ANOMALY


@pytest.mark.asyncio
async def test_router_llm_mode_falls_back_to_rule_based_on_error(monkeypatch):
    async def failing_router(_: str):
        raise RuntimeError("Ollama unavailable")

    monkeypatch.setenv("ROUTER_MODE", "llm")
    monkeypatch.setattr("agents.router._run_router_llm", failing_router)

    result = await classify_hypothesis("Investigate a chargeback cluster with disputes")
    assert result == ClaimType.CHARGEBACK_CASCADE


@pytest.mark.asyncio
async def test_planner_llm_retries_invalid_output_then_succeeds(monkeypatch):
    hypothesis = Hypothesis(
        raw="Investigate merchants sharing charged-back customers",
        claim_type=ClaimType.CHARGEBACK_CASCADE,
        scope={},
    )
    attempts: list[str | None] = []

    async def fake_run_planner_llm(hypothesis_input, *, feedback=None):
        attempts.append(feedback)
        if len(attempts) == 1:
            return [
                {
                    "projection_id": "not_real",
                    "algorithm_id": "wcc",
                    "params": {},
                    "rationale": "bad",
                }
            ]
        return [
            BranchSpec(
                projection_id="merchant_cochargeback",
                algorithm_id="louvain",
                params={"min_shared_customers": 1},
                rationale="retry succeeded",
            )
        ]

    monkeypatch.setenv("PLANNER_MODE", "llm")
    monkeypatch.setattr("agents.planner._run_planner_llm", fake_run_planner_llm)

    branches = await plan_branches(hypothesis)
    assert len(attempts) == 2
    assert attempts[0] is None
    assert "unknown projection_id" in attempts[1]
    assert branches[0].projection_id == "merchant_cochargeback"
    assert branches[0].algorithm_id == "louvain"


@pytest.mark.asyncio
async def test_planner_llm_falls_back_after_double_invalid(monkeypatch):
    hypothesis = Hypothesis(
        raw="Investigate routing anomalies",
        claim_type=ClaimType.ROUTING_ANOMALY,
        scope={},
    )

    async def always_invalid(*args, **kwargs):
        return [
            {
                "projection_id": "invalid_projection",
                "algorithm_id": "invalid_algorithm",
                "params": {},
                "rationale": "still bad",
            }
        ]

    monkeypatch.setenv("PLANNER_MODE", "llm")
    monkeypatch.setattr("agents.planner._run_planner_llm", always_invalid)

    branches = await plan_branches(hypothesis)
    assert branches == [DEFAULT_BRANCH_SPEC]


@pytest.mark.asyncio
async def test_parse_hypothesis_activity_builds_scope_and_urgency(monkeypatch):
    monkeypatch.setenv("ROUTER_MODE", "rule-based")

    hypothesis = await parse_hypothesis_activity(
        "Urgent review of customers C1_1 and C1_2 linked to merchant M2 in GB over $300 within 24 hours."
    )

    assert hypothesis.claim_type == ClaimType.RING
    assert hypothesis.scope.customer_ids == ["C1_1", "C1_2"]
    assert hypothesis.scope.merchant_ids == ["M2"]
    assert hypothesis.scope.countries == ["GB"]
    assert hypothesis.scope.min_transaction_amount == 300.0
    assert hypothesis.scope.time_window_hours == 24
    assert hypothesis.urgency == 5


@pytest.mark.asyncio
async def test_demo_scenarios_route_with_full_consistency(monkeypatch):
    scenarios_path = Path("evals/scenarios.yaml")
    scenarios = yaml.safe_load(scenarios_path.read_text())["scenarios"]

    monkeypatch.setenv("ROUTER_MODE", "rule-based")
    monkeypatch.setenv("PLANNER_MODE", "rule-based")

    for scenario in scenarios:
        matches = 0
        for _ in range(10):
            hypothesis = await parse_hypothesis_activity(scenario["hypothesis"])
            branches = await plan_branches_activity(hypothesis)
            primary_branch = branches[0]

            if (
                hypothesis.claim_type.value == scenario["expected_claim_type"]
                and primary_branch.projection_id == scenario["expected_projection_id"]
                and primary_branch.algorithm_id == scenario["expected_algorithm_id"]
            ):
                matches += 1

        assert matches >= 9, f"{scenario['name']} matched only {matches}/10 runs"
