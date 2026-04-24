import asyncio
import argparse
import json
import logging
import os
import sys
import uuid
from datetime import timedelta
from typing import Optional

from temporalio.client import Client, WorkflowFailureError
from dotenv import load_dotenv

from contracts.hypothesis import Hypothesis
from contracts.verdict import FraudVerdict
from workflows.investigation import FraudInvestigationWorkflow

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


async def submit_investigation(
    hypothesis: str | Hypothesis,
    temporal_host: Optional[str] = None,
    temporal_namespace: Optional[str] = None,
    task_queue: Optional[str] = None,
    workflow_id: Optional[str] = None,
    timeout_seconds: int = 300,
) -> FraudVerdict:
    """
    Submit a fraud investigation hypothesis and wait for the verdict.

    Args:
        hypothesis: Raw text or Hypothesis object describing the investigation
        temporal_host: Temporal server host:port (default: env TEMPORAL_HOST)
        temporal_namespace: Temporal namespace (default: env TEMPORAL_NAMESPACE)
        task_queue: Task queue name (default: env TEMPORAL_TASK_QUEUE)
        workflow_id: Unique workflow ID (default: auto-generated)
        timeout_seconds: Max seconds to wait for result (default: 300)

    Returns:
        FraudVerdict with the investigation results

    Raises:
        WorkflowFailureError: If the workflow fails
        TimeoutError: If the workflow exceeds timeout_seconds
    """
    temporal_host = temporal_host or os.getenv("TEMPORAL_HOST", "localhost:7233")
    temporal_namespace = temporal_namespace or os.getenv(
        "TEMPORAL_NAMESPACE", "default"
    )
    task_queue = task_queue or os.getenv("TEMPORAL_TASK_QUEUE", "fraud-agent")
    workflow_id = workflow_id or f"fraud-investigation-{uuid.uuid4()}"

    logger.info(
        f"Submitting investigation: {workflow_id} on {temporal_host}/{temporal_namespace}"
    )

    try:
        client = await Client.connect(temporal_host, namespace=temporal_namespace)

        handle = await client.start_workflow(
            FraudInvestigationWorkflow.run,
            hypothesis,
            id=workflow_id,
            task_queue=task_queue,
            execution_timeout=timedelta(seconds=timeout_seconds),
        )

        logger.info(f"Workflow started with ID: {handle.id}")
        logger.info(f"Waiting for result (timeout: {timeout_seconds}s)...")

        result = await asyncio.wait_for(
            handle.result(), timeout=timeout_seconds + 10
        )

        logger.info(f"Investigation complete. Confidence: {result.confidence:.2f}")
        return result

    except asyncio.TimeoutError:
        logger.error(f"Workflow timed out after {timeout_seconds} seconds")
        raise TimeoutError(
            f"Investigation workflow exceeded {timeout_seconds}s timeout"
        ) from None
    except WorkflowFailureError as e:
        logger.error(f"Workflow failed: {e}")
        raise
    finally:
        await client.close()


def verdict_to_dict(verdict: FraudVerdict) -> dict:
    """Convert a FraudVerdict to a JSON-serializable dict."""
    return {
        "trace_id": verdict.trace_id,
        "hypothesis": {
            "raw": verdict.hypothesis.raw,
            "claim_type": verdict.hypothesis.claim_type.value,
            "urgency": verdict.hypothesis.urgency,
            "scope": {
                "customer_ids": verdict.hypothesis.scope.customer_ids,
                "merchant_ids": verdict.hypothesis.scope.merchant_ids,
                "time_window_hours": verdict.hypothesis.scope.time_window_hours,
                "min_transaction_amount": verdict.hypothesis.scope.min_transaction_amount,
                "countries": verdict.hypothesis.scope.countries,
            },
        },
        "evidence_count": len(verdict.evidence),
        "final_assessment": verdict.final_assessment,
        "confidence": verdict.confidence,
        "recommended_actions": verdict.recommended_actions,
    }


async def main():
    parser = argparse.ArgumentParser(
        description="Submit a fraud investigation hypothesis to the agent"
    )
    parser.add_argument(
        "--hypothesis",
        required=True,
        help="The fraud hypothesis to investigate (text description)",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("TEMPORAL_HOST", "localhost:7233"),
        help="Temporal server host:port",
    )
    parser.add_argument(
        "--namespace",
        default=os.getenv("TEMPORAL_NAMESPACE", "default"),
        help="Temporal namespace",
    )
    parser.add_argument(
        "--task-queue",
        default=os.getenv("TEMPORAL_TASK_QUEUE", "fraud-agent"),
        help="Task queue name",
    )
    parser.add_argument(
        "--workflow-id",
        help="Unique workflow ID (auto-generated if not provided)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Max seconds to wait for result (default: 300)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON instead of pretty-printed text",
    )

    args = parser.parse_args()

    try:
        verdict = await submit_investigation(
            hypothesis=args.hypothesis,
            temporal_host=args.host,
            temporal_namespace=args.namespace,
            task_queue=args.task_queue,
            workflow_id=args.workflow_id,
            timeout_seconds=args.timeout,
        )

        if args.json:
            print(json.dumps(verdict_to_dict(verdict), indent=2))
        else:
            _print_verdict(verdict)

    except (TimeoutError, WorkflowFailureError) as e:
        logger.error(f"Investigation failed: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)


def _print_verdict(verdict: FraudVerdict) -> None:
    """Pretty-print a FraudVerdict."""
    print("\n" + "=" * 80)
    print("FRAUD INVESTIGATION VERDICT")
    print("=" * 80)
    print(f"\nTrace ID: {verdict.trace_id}")
    print(f"\nHypothesis: {verdict.hypothesis.raw}")
    print(f"Claim Type: {verdict.hypothesis.claim_type.value}")
    print(f"Urgency: {verdict.hypothesis.urgency}/5")

    if verdict.hypothesis.scope.customer_ids:
        print(f"Scope - Customers: {', '.join(verdict.hypothesis.scope.customer_ids)}")
    if verdict.hypothesis.scope.merchant_ids:
        print(f"Scope - Merchants: {', '.join(verdict.hypothesis.scope.merchant_ids)}")
    if verdict.hypothesis.scope.time_window_hours:
        print(f"Scope - Time Window: {verdict.hypothesis.scope.time_window_hours} hours")
    if verdict.hypothesis.scope.min_transaction_amount:
        print(
            f"Scope - Min Amount: ${verdict.hypothesis.scope.min_transaction_amount:.2f}"
        )
    if verdict.hypothesis.scope.countries:
        print(f"Scope - Countries: {', '.join(verdict.hypothesis.scope.countries)}")

    print(f"\n--- Investigation Results ---")
    print(f"Evidence Branches: {len(verdict.evidence)}")
    print(f"Overall Confidence: {verdict.confidence:.1%}")

    if verdict.evidence:
        print(f"\nBranch Results:")
        for i, ev in enumerate(verdict.evidence, 1):
            print(f"\n  Branch {i}: {ev.branch_spec.projection_id}")
            print(f"    Claim: {ev.claim.assertion}")
            print(f"    Score: {ev.claim.score:.2f}")
            print(f"    Verifier: {ev.verifier.outcome}")
            print(f"    Confidence: {ev.confidence:.1%}")
            print(f"    Latency: {ev.latency_ms}ms")

    print(f"\n--- Verdict ---")
    print(f"\n{verdict.final_assessment}")

    if verdict.recommended_actions:
        print(f"\n--- Recommended Actions ---")
        for action in verdict.recommended_actions:
            print(f"  • {action}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
