import json
import os
from pathlib import Path

from temporalio import activity

from agents.composer import compose_projection_proposal
from contracts.composer import ProjectionProposal
from contracts.hypothesis import Hypothesis
from gds.driver import get_driver
from metrics import instrument_activity


PROPOSAL_DIR = Path(".claude") / "composer_proposals"


@activity.defn(name="compose_projection")
@instrument_activity("compose_projection")
async def compose_projection_activity(
    hypothesis: Hypothesis,
    trace_id: str,
) -> ProjectionProposal:
    driver = get_driver()
    async with driver.session() as session:
        schema_summary = await _schema_summary(session)
        draft = await compose_projection_proposal(hypothesis, schema_summary)
        await _validate_projection_queries(session, draft.node_query, draft.rel_query, draft.parameters)

    proposal_id = _proposal_id(trace_id)
    PROPOSAL_DIR.mkdir(parents=True, exist_ok=True)
    file_path = PROPOSAL_DIR / f"{proposal_id}.json"
    autoexec_requested = os.getenv("ALLOW_COMPOSER_AUTOEXEC", "false").strip().lower() == "true"
    status = "autoexec_allowed" if autoexec_requested else "review_required"

    proposal = ProjectionProposal(
        proposal_id=proposal_id,
        hypothesis_raw=hypothesis.raw,
        node_query=draft.node_query,
        rel_query=draft.rel_query,
        parameters=draft.parameters,
        rationale=draft.rationale,
        status=status,
        file_path=str(file_path.resolve()),
        autoexec_requested=autoexec_requested,
    )

    file_path.write_text(
        json.dumps(
            {
                "proposal": proposal.model_dump(),
                "schema_summary": schema_summary,
            },
            indent=2,
        )
    )

    return proposal


async def _schema_summary(session) -> dict[str, list[str] | dict[str, list[str]]]:
    labels_result = await session.run("CALL db.labels() YIELD label RETURN label ORDER BY label")
    labels = [record["label"] async for record in labels_result]

    rels_result = await session.run(
        "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType ORDER BY relationshipType"
    )
    relationship_types = [record["relationshipType"] async for record in rels_result]

    return {
        "labels": labels,
        "relationship_types": relationship_types,
    }


async def _validate_projection_queries(
    session,
    node_query: str,
    rel_query: str,
    parameters: dict[str, float | int | str | bool | list[str]],
) -> None:
    node_result = await session.run(f"EXPLAIN {node_query}", parameters)
    await node_result.consume()

    rel_result = await session.run(f"EXPLAIN {rel_query}", parameters)
    await rel_result.consume()


def _proposal_id(trace_id: str) -> str:
    cleaned = "".join(character if character.isalnum() else "_" for character in trace_id)
    return f"{cleaned}_composer"
