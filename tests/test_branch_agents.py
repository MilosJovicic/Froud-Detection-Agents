import pytest

from agents.interpreter import interpret_structural_claim
from agents.verifier import select_verifier_request
from contracts.branch import BranchSpec, StructuralClaim


SHARED_DEVICE_SPEC = BranchSpec(
    projection_id="shared_device_ring",
    algorithm_id="wcc",
    params={"min_customers_per_device": 2},
    rationale="Known fraud ring branch",
)

WCC_ROWS = [
    {"nodeId": 101, "componentId": 7},
    {"nodeId": 102, "componentId": 7},
    {"nodeId": 103, "componentId": 7},
]

NODE_DETAILS = {
    101: {"entity_id": "C1_1", "labels": ["Customer"]},
    102: {"entity_id": "C1_2", "labels": ["Customer"]},
    103: {"entity_id": "DEV_RING_1", "labels": ["Device"]},
}


@pytest.mark.asyncio
async def test_interpreter_llm_mode_uses_runner(monkeypatch):
    async def fake_run_interpreter_llm(spec, algorithm_rows, node_details, *, feedback=None):
        assert spec == SHARED_DEVICE_SPEC
        assert algorithm_rows == WCC_ROWS
        assert node_details == NODE_DETAILS
        assert feedback is None
        return StructuralClaim(
            assertion="C1_1 and C1_2 are in the same connected ring",
            entities=["C1_1", "C1_2"],
            score=2.0,
            topology="wcc_component",
        )

    monkeypatch.setenv("INTERPRETER_MODE", "llm")
    monkeypatch.setattr("agents.interpreter._run_interpreter_llm", fake_run_interpreter_llm)

    claim = await interpret_structural_claim(SHARED_DEVICE_SPEC, WCC_ROWS, NODE_DETAILS)
    assert claim.topology == "wcc_component"
    assert claim.entities == ["C1_1", "C1_2"]


@pytest.mark.asyncio
async def test_interpreter_llm_retries_invalid_output_then_succeeds(monkeypatch):
    attempts: list[str | None] = []

    async def fake_run_interpreter_llm(spec, algorithm_rows, node_details, *, feedback=None):
        attempts.append(feedback)
        if len(attempts) == 1:
            return {
                "assertion": "   ",
                "entities": [],
                "score": 2.0,
                "topology": "wcc_component",
            }
        return StructuralClaim(
            assertion="C1_1 and C1_2 form a device-sharing component",
            entities=["C1_1", "C1_2"],
            score=2.0,
            topology="wcc_component",
        )

    monkeypatch.setenv("INTERPRETER_MODE", "llm")
    monkeypatch.setattr("agents.interpreter._run_interpreter_llm", fake_run_interpreter_llm)

    claim = await interpret_structural_claim(SHARED_DEVICE_SPEC, WCC_ROWS, NODE_DETAILS)
    assert len(attempts) == 2
    assert attempts[0] is None
    assert "assertion must not be blank" in attempts[1]
    assert claim.entities == ["C1_1", "C1_2"]


@pytest.mark.asyncio
async def test_interpreter_llm_falls_back_to_rule_based_on_error(monkeypatch):
    async def failing_interpreter(*args, **kwargs):
        raise RuntimeError("Ollama unavailable")

    monkeypatch.setenv("INTERPRETER_MODE", "llm")
    monkeypatch.setattr("agents.interpreter._run_interpreter_llm", failing_interpreter)

    claim = await interpret_structural_claim(SHARED_DEVICE_SPEC, WCC_ROWS, NODE_DETAILS)
    assert claim.topology == "wcc_component"
    assert claim.entities == ["C1_1", "C1_2"]
    assert claim.score == 2.0


@pytest.mark.asyncio
async def test_verifier_llm_mode_uses_runner(monkeypatch):
    claim = StructuralClaim(
        assertion="C1_1 and C1_2 form a device-sharing component",
        entities=["C1_1", "C1_2"],
        score=2.0,
        topology="wcc_component",
    )

    async def fake_run_verifier_llm(spec, claim_input, *, feedback=None):
        assert spec == SHARED_DEVICE_SPEC
        assert claim_input == claim
        assert feedback is None
        return {
            "template_id": "confirm_component",
            "bindings": {
                "customer_ids": ["C1_1", "C1_2"],
                "connector_type": "device",
            },
        }

    monkeypatch.setenv("VERIFIER_MODE", "llm")
    monkeypatch.setattr("agents.verifier._run_verifier_llm", fake_run_verifier_llm)

    request = await select_verifier_request(SHARED_DEVICE_SPEC, claim)
    assert request == (
        "confirm_component",
        {
            "customer_ids": ["C1_1", "C1_2"],
            "connector_type": "device",
        },
    )


@pytest.mark.asyncio
async def test_verifier_llm_retries_invalid_output_then_succeeds(monkeypatch):
    claim = StructuralClaim(
        assertion="C1_1 and C1_2 form a device-sharing component",
        entities=["C1_1", "C1_2"],
        score=2.0,
        topology="wcc_component",
    )
    attempts: list[str | None] = []

    async def fake_run_verifier_llm(spec, claim_input, *, feedback=None):
        attempts.append(feedback)
        if len(attempts) == 1:
            return {"template_id": "not_real", "bindings": {}}
        return {
            "template_id": "confirm_component",
            "bindings": {
                "customer_ids": ["C1_1", "C1_2"],
                "connector_type": "device",
            },
        }

    monkeypatch.setenv("VERIFIER_MODE", "llm")
    monkeypatch.setattr("agents.verifier._run_verifier_llm", fake_run_verifier_llm)

    request = await select_verifier_request(SHARED_DEVICE_SPEC, claim)
    assert len(attempts) == 2
    assert attempts[0] is None
    assert "unknown template_id" in attempts[1]
    assert request is not None
    assert request[0] == "confirm_component"


@pytest.mark.asyncio
async def test_verifier_llm_falls_back_to_rule_based_on_error(monkeypatch):
    claim = StructuralClaim(
        assertion="C1_1 and C1_2 form a device-sharing component",
        entities=["C1_1", "C1_2"],
        score=2.0,
        topology="wcc_component",
    )

    async def failing_verifier(*args, **kwargs):
        raise RuntimeError("Ollama unavailable")

    monkeypatch.setenv("VERIFIER_MODE", "llm")
    monkeypatch.setattr("agents.verifier._run_verifier_llm", failing_verifier)

    request = await select_verifier_request(SHARED_DEVICE_SPEC, claim)
    assert request == (
        "confirm_component",
        {
            "customer_ids": ["C1_1", "C1_2"],
            "connector_type": "device",
        },
    )
