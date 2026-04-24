import json
import os
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from agents.model import qwen_model
from agents.stubs import choose_verifier_request
from contracts.branch import BranchSpec, StructuralClaim
from metrics import record_agent_tokens
from verifiers.templates import BINDINGS


class VerifierSelection(BaseModel):
    template_id: str
    bindings: dict[str, Any] = Field(default_factory=dict)


VERIFIER_SYSTEM_PROMPT = """You choose a deterministic verifier template for a structural fraud claim.
Do not write Cypher.
Pick exactly one template_id from the provided library and provide bindings that satisfy that template's schema."""


def _verifier_agent() -> Agent:
    return Agent(
        qwen_model(temperature=0.0, max_tokens=128),
        output_type=VerifierSelection,
        system_prompt=VERIFIER_SYSTEM_PROMPT,
        name="VerifierAgent",
        retries=0,
    )


async def _run_verifier_llm(
    spec: BranchSpec,
    claim: StructuralClaim,
    *,
    feedback: str | None = None,
) -> VerifierSelection:
    prompt_parts = [
        "BranchSpec:",
        spec.model_dump_json(indent=2),
        "",
        "StructuralClaim:",
        claim.model_dump_json(indent=2),
        "",
        "Verifier template schemas:",
        json.dumps(_binding_schemas(), indent=2, sort_keys=True),
        "",
        "Return a VerifierSelection.",
    ]
    if feedback:
        prompt_parts.extend(["", "Validation feedback from the previous attempt:", feedback])

    prompt = "\n".join(prompt_parts)
    try:
        result = await _verifier_agent().run(prompt)
    except Exception:
        record_agent_tokens("VerifierAgent", prompt=prompt, output="", status="failure")
        raise

    record_agent_tokens("VerifierAgent", prompt=prompt, output=result.output)
    return result.output


async def select_verifier_request(
    spec: BranchSpec,
    claim: StructuralClaim,
) -> tuple[str, dict[str, Any]] | None:
    mode = os.getenv("VERIFIER_MODE", "llm").strip().lower()
    fallback = choose_verifier_request(spec, claim)

    if mode == "rule-based":
        return fallback

    try:
        first_attempt = await _run_verifier_llm(spec, claim)
        return _validate_verifier_selection(first_attempt)
    except Exception as exc:
        try:
            second_attempt = await _run_verifier_llm(spec, claim, feedback=str(exc))
            return _validate_verifier_selection(second_attempt)
        except Exception:
            return fallback


def _validate_verifier_selection(
    output: VerifierSelection | dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    selection = VerifierSelection.model_validate(output)
    template_id = selection.template_id.strip()
    if template_id not in BINDINGS:
        raise ValueError(f"unknown template_id: {template_id}")

    validated_bindings = BINDINGS[template_id].model_validate(selection.bindings)
    return template_id, validated_bindings.model_dump()


def _binding_schemas() -> dict[str, dict[str, Any]]:
    return {
        template_id: model_cls.model_json_schema()
        for template_id, model_cls in BINDINGS.items()
    }
