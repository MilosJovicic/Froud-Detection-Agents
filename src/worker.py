import asyncio
import logging
import os
import signal
from typing import Any

from temporalio.client import Client
from temporalio.worker import Worker
from dotenv import load_dotenv

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
from workflows.branch import ReasoningBranchWorkflow
from workflows.investigation import FraudInvestigationWorkflow
from gds.driver import close_driver

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


async def main():
    temporal_host = os.getenv("TEMPORAL_HOST", "localhost:7233")
    temporal_namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "fraud-agent")

    logger.info(f"Connecting to Temporal at {temporal_host}")
    client = await Client.connect(temporal_host, namespace=temporal_namespace)

    logger.info(f"Starting worker on task queue: {task_queue}")
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[FraudInvestigationWorkflow, ReasoningBranchWorkflow],
        activities=[
            parse_hypothesis_activity,
            plan_branches_activity,
            compose_projection_activity,
            aggregate_evidence_activity,
            synthesize_verdict_activity,
            project_graph_activity,
            run_algorithm_activity,
            interpret_result_activity,
            verify_with_cypher_activity,
            drop_projection_activity,
        ],
    )

    shutdown_event = asyncio.Event()

    def handle_signal(signum: int, frame: Any) -> None:
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        async with worker:
            logger.info("Worker is running. Press Ctrl+C to stop.")
            await shutdown_event.wait()
    except KeyboardInterrupt:
        logger.info("Interrupted, shutting down...")
    finally:
        logger.info("Closing Neo4j driver...")
        await close_driver()
        logger.info("Worker stopped.")


if __name__ == "__main__":
    asyncio.run(main())
