from temporalio import activity
from temporalio.exceptions import ApplicationError

from activities._utils import injected_failure_stage
from agents.interpreter import interpret_structural_claim
from contracts.branch import BranchSpec, StructuralClaim
from gds.driver import get_driver
from metrics import instrument_activity


@activity.defn(name="interpret_result")
@instrument_activity("interpret_result")
async def interpret_result_activity(
    spec: BranchSpec,
    algorithm_rows: list[dict],
) -> StructuralClaim:
    if injected_failure_stage(spec) == "after_algorithm":
        raise ApplicationError(
            "Injected failure after algorithm execution",
            non_retryable=True,
        )

    node_ids = sorted(
        {
            int(value)
            for row in algorithm_rows
            for key, value in row.items()
            if key in {"nodeId", "node1", "node2"}
        }
    )
    node_details = await _load_node_details(node_ids)
    return await interpret_structural_claim(spec, algorithm_rows, node_details)


async def _load_node_details(node_ids: list[int]) -> dict[int, dict]:
    if not node_ids:
        return {}

    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (n)
            WHERE id(n) IN $node_ids
            RETURN id(n) AS node_id, coalesce(n.id, toString(id(n))) AS entity_id, labels(n) AS labels
            """,
            {"node_ids": node_ids},
        )

        node_details: dict[int, dict] = {}
        async for record in result:
            node_id = int(record["node_id"])
            node_details[node_id] = {
                "entity_id": record["entity_id"],
                "labels": record["labels"],
            }
        return node_details
