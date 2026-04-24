import json
from pathlib import Path

import pytest

from activities.compose import compose_projection_activity
from agents.composer import compose_projection_proposal
from contracts.hypothesis import ClaimType, Hypothesis


def _custom_hypothesis() -> Hypothesis:
    return Hypothesis(
        raw="Investigate merchants with overlapping customers across GB and CY that do not match the standard fraud templates.",
        claim_type=ClaimType.CUSTOM,
        scope={"countries": ["GB", "CY"]},
    )


def _schema_summary() -> dict:
    return {
        "labels": ["Country", "Customer", "Merchant", "PayoutAccount"],
        "relationship_types": ["PAYOUT_TO", "REGISTERED_IN", "TRANSACTED_AT"],
    }


@pytest.mark.asyncio
async def test_composer_llm_mode_uses_runner(monkeypatch):
    hypothesis = _custom_hypothesis()

    async def fake_run_composer_llm(hypothesis_input, schema_summary, *, feedback=None):
        assert hypothesis_input == hypothesis
        assert schema_summary == _schema_summary()
        assert feedback is None
        return {
            "node_query": "MATCH (n:Merchant) RETURN id(n) AS id, labels(n) AS labels",
            "rel_query": "MATCH (c:Customer)-[:TRANSACTED_AT]->(m:Merchant) RETURN id(c) AS source, id(m) AS target, 'TRANSACTED_AT' AS type",
            "parameters": {},
            "rationale": "Use merchants and customer transaction edges.",
        }

    monkeypatch.setenv("COMPOSER_MODE", "llm")
    monkeypatch.setattr("agents.composer._run_composer_llm", fake_run_composer_llm)

    proposal = await compose_projection_proposal(hypothesis, _schema_summary())
    assert "AS id" in proposal.node_query
    assert "AS source" in proposal.rel_query


@pytest.mark.asyncio
async def test_composer_llm_retries_invalid_output_then_succeeds(monkeypatch):
    hypothesis = _custom_hypothesis()
    attempts: list[str | None] = []

    async def fake_run_composer_llm(hypothesis_input, schema_summary, *, feedback=None):
        attempts.append(feedback)
        if len(attempts) == 1:
            return {
                "node_query": "MATCH (n:Merchant) RETURN id(n) AS id",
                "rel_query": "MATCH (c:Customer)-[:TRANSACTED_AT]->(m:Merchant) RETURN id(c) AS source, id(m) AS target, 'TRANSACTED_AT' AS type",
                "parameters": {},
                "rationale": "bad",
            }
        return {
            "node_query": "MATCH (n:Merchant) RETURN id(n) AS id, labels(n) AS labels",
            "rel_query": "MATCH (c:Customer)-[:TRANSACTED_AT]->(m:Merchant) RETURN id(c) AS source, id(m) AS target, 'TRANSACTED_AT' AS type",
            "parameters": {},
            "rationale": "Use merchants and customer transaction edges.",
        }

    monkeypatch.setenv("COMPOSER_MODE", "llm")
    monkeypatch.setattr("agents.composer._run_composer_llm", fake_run_composer_llm)

    proposal = await compose_projection_proposal(hypothesis, _schema_summary())
    assert len(attempts) == 2
    assert attempts[0] is None
    assert "node_query is missing required aliases" in attempts[1]
    assert proposal.rationale


@pytest.mark.asyncio
async def test_composer_llm_falls_back_to_rule_based_on_error(monkeypatch):
    hypothesis = _custom_hypothesis()

    async def failing_composer(*args, **kwargs):
        raise RuntimeError("Ollama unavailable")

    monkeypatch.setenv("COMPOSER_MODE", "llm")
    monkeypatch.setattr("agents.composer._run_composer_llm", failing_composer)

    proposal = await compose_projection_proposal(hypothesis, _schema_summary())
    assert "TRANSACTED_AT" in proposal.rel_query
    assert proposal.parameters["countries"] == ["GB", "CY"]


@pytest.mark.asyncio
async def test_compose_activity_writes_syntactically_valid_review_proposal(neo4j_session, monkeypatch):
    del neo4j_session
    hypothesis = _custom_hypothesis()

    monkeypatch.setenv("COMPOSER_MODE", "rule-based")
    monkeypatch.setenv("ALLOW_COMPOSER_AUTOEXEC", "false")

    proposal = await compose_projection_activity(hypothesis, "custom-workflow-1")
    proposal_path = Path(proposal.file_path)

    assert proposal.status == "review_required"
    assert proposal_path.exists()
    payload = json.loads(proposal_path.read_text())
    assert payload["proposal"]["proposal_id"] == proposal.proposal_id
    assert payload["proposal"]["hypothesis_raw"] == hypothesis.raw
