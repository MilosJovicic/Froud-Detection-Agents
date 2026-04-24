import json
import os
from collections import Counter
from typing import Any

from pydantic_ai import Agent

from agents.model import qwen_model
from agents.stubs import build_structural_claim
from contracts.branch import BranchSpec, StructuralClaim
from metrics import record_agent_tokens


INTERPRETER_SYSTEM_PROMPT = """You convert graph algorithm output into a compact StructuralClaim.
Use only the entity ids provided in node_details.
Ground every field in the provided rows and branch metadata.
If the rows show no evidence, return an empty claim with score 0 and topology "empty"."""


def _interpreter_agent() -> Agent:
    return Agent(
        qwen_model(temperature=0.1, max_tokens=256),
        output_type=StructuralClaim,
        system_prompt=INTERPRETER_SYSTEM_PROMPT,
        name="InterpreterAgent",
        retries=0,
    )


async def _run_interpreter_llm(
    spec: BranchSpec,
    algorithm_rows: list[dict[str, Any]],
    node_details: dict[int, dict[str, Any]],
    *,
    feedback: str | None = None,
) -> StructuralClaim:
    prompt_rows = _prepare_algorithm_rows_for_prompt(algorithm_rows)
    prompt_node_details = _prepare_node_details_for_prompt(prompt_rows, node_details)
    prompt_parts = [
        "BranchSpec:",
        spec.model_dump_json(indent=2),
        "",
        "Algorithm rows (already filtered for you):",
        json.dumps(prompt_rows, indent=2),
        "",
        "Node details keyed by graph node id:",
        json.dumps(prompt_node_details, indent=2, sort_keys=True),
        "",
        "Return a StructuralClaim grounded in the rows.",
    ]
    if feedback:
        prompt_parts.extend(["", "Validation feedback from the previous attempt:", feedback])

    prompt = "\n".join(prompt_parts)
    try:
        result = await _interpreter_agent().run(prompt)
    except Exception:
        record_agent_tokens("InterpreterAgent", prompt=prompt, output="", status="failure")
        raise

    record_agent_tokens("InterpreterAgent", prompt=prompt, output=result.output)
    return result.output


async def interpret_structural_claim(
    spec: BranchSpec,
    algorithm_rows: list[dict[str, Any]],
    node_details: dict[int, dict[str, Any]],
) -> StructuralClaim:
    mode = os.getenv("INTERPRETER_MODE", "llm").strip().lower()
    fallback = build_structural_claim(spec, algorithm_rows, node_details)

    if mode == "rule-based":
        return fallback

    try:
        first_attempt = await _run_interpreter_llm(spec, algorithm_rows, node_details)
        return _validate_structural_claim(first_attempt, algorithm_rows)
    except Exception as exc:
        try:
            second_attempt = await _run_interpreter_llm(
                spec,
                algorithm_rows,
                node_details,
                feedback=str(exc),
            )
            return _validate_structural_claim(second_attempt, algorithm_rows)
        except Exception:
            return fallback


def _validate_structural_claim(
    output: StructuralClaim | dict[str, Any],
    algorithm_rows: list[dict[str, Any]],
) -> StructuralClaim:
    claim = StructuralClaim.model_validate(output)
    assertion = claim.assertion.strip()
    if not assertion:
        raise ValueError("assertion must not be blank")

    topology = claim.topology.strip()
    if not topology:
        raise ValueError("topology must not be blank")

    entities = _dedupe_entities(claim.entities)
    if algorithm_rows and not entities:
        raise ValueError("entities must not be empty when algorithm_rows are present")

    return claim.model_copy(
        update={
            "assertion": assertion,
            "topology": topology,
            "entities": entities,
        }
    )


def _prepare_algorithm_rows_for_prompt(
    algorithm_rows: list[dict[str, Any]],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not algorithm_rows:
        return []

    sample_row = algorithm_rows[0]

    if "score" in sample_row:
        ordered = sorted(
            algorithm_rows,
            key=lambda row: (-float(row["score"]), int(row["nodeId"])),
        )
        return ordered[:limit]

    if "similarity" in sample_row:
        ordered = sorted(
            algorithm_rows,
            key=lambda row: (
                -float(row["similarity"]),
                int(row["node1"]),
                int(row["node2"]),
            ),
        )
        return ordered[:limit]

    grouping_key = None
    if "componentId" in sample_row:
        grouping_key = "componentId"
    elif "communityId" in sample_row:
        grouping_key = "communityId"

    if grouping_key:
        counts = Counter(int(row[grouping_key]) for row in algorithm_rows)
        ordered = sorted(
            algorithm_rows,
            key=lambda row: (
                -counts[int(row[grouping_key])],
                int(row[grouping_key]),
                int(row["nodeId"]),
            ),
        )
        return ordered[:limit]

    return algorithm_rows[:limit]


def _prepare_node_details_for_prompt(
    algorithm_rows: list[dict[str, Any]],
    node_details: dict[int, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    referenced_node_ids = {
        int(value)
        for row in algorithm_rows
        for key, value in row.items()
        if key in {"nodeId", "node1", "node2"}
    }

    return {
        str(node_id): node_details[node_id]
        for node_id in sorted(referenced_node_ids)
        if node_id in node_details
    }


def _dedupe_entities(entities: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()

    for entity in entities:
        normalized = entity.strip()
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)

    return deduped
