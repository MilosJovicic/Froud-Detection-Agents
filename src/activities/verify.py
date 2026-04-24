from pydantic import ValidationError
from temporalio import activity
from temporalio.exceptions import ApplicationError

from agents.verifier import select_verifier_request
from contracts.branch import BranchSpec, StructuralClaim, VerifierResult
from gds.driver import get_driver
from metrics import instrument_activity
from verifiers import verify


@activity.defn(name="verify_with_cypher")
@instrument_activity("verify_with_cypher")
async def verify_with_cypher_activity(
    spec: BranchSpec,
    claim: StructuralClaim,
) -> VerifierResult:
    request = await select_verifier_request(spec, claim)
    if request is None:
        return VerifierResult(
            outcome="DISAGREE",
            evidence_cypher="-- no deterministic verifier mapping available",
            row_count=0,
        )

    template_id, bindings = request
    driver = get_driver()
    async with driver.session() as session:
        try:
            return await verify(session, template_id, bindings)
        except ValidationError as exc:
            raise ApplicationError(
                f"Verifier bindings failed validation for {template_id}: {exc}",
                non_retryable=True,
            ) from exc
