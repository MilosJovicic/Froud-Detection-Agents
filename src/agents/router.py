import os
import re

from pydantic_ai import Agent

from agents.model import qwen_model
from contracts.hypothesis import ClaimType
from metrics import record_agent_tokens


ROUTER_SYSTEM_PROMPT = """You classify fraud investigation requests into one of these categories:
- ring: collusion between multiple customers
- chargeback: dispute/loss clusters
- routing: payment decline or acquirer anomalies
- similarity: "who behaves like X"
- money_flow: tracing funds between entities
- custom: none of the above

Return only the category name."""


def _router_agent() -> Agent:
    return Agent(
        qwen_model(temperature=0.0, max_tokens=16),
        output_type=ClaimType,
        system_prompt=ROUTER_SYSTEM_PROMPT,
        name="RouterAgent",
        retries=0,
    )


async def _run_router_llm(raw_text: str) -> ClaimType:
    try:
        result = await _router_agent().run(raw_text)
    except Exception:
        record_agent_tokens("RouterAgent", prompt=raw_text, output="", status="failure")
        raise

    record_agent_tokens("RouterAgent", prompt=raw_text, output=result.output)
    return result.output


async def classify_hypothesis(raw_text: str) -> ClaimType:
    mode = os.getenv("ROUTER_MODE", "llm").strip().lower()
    if mode == "rule-based":
        return _rule_based_claim_type(raw_text)

    try:
        return await _run_router_llm(raw_text)
    except Exception:
        return _rule_based_claim_type(raw_text)


def _rule_based_claim_type(raw_text: str) -> ClaimType:
    text = raw_text.lower()
    customer_ids = {
        match.upper()
        for match in re.findall(r"\bC[A-Z0-9_]+\b", raw_text, flags=re.IGNORECASE)
    }

    if any(
        token in text
        for token in (
            "chargeback",
            "charged back",
            "dispute",
            "loss cluster",
        )
    ):
        return ClaimType.CHARGEBACK_CASCADE

    if any(
        token in text
        for token in (
            "decline",
            "routing",
            "route",
            "acquirer anomaly",
            "acquirer anomalies",
        )
    ):
        return ClaimType.ROUTING_ANOMALY

    if any(
        token in text
        for token in (
            "who behaves like",
            "looks like",
            "lookalike",
            "look alike",
            "similar",
        )
    ):
        return ClaimType.ENTITY_SIMILARITY

    if any(
        token in text
        for token in (
            "money flow",
            "trace funds",
            "trace fund",
            "funds",
            "settlement",
            "issuer",
            "path-based",
        )
    ):
        return ClaimType.MONEY_FLOW

    if any(
        token in text
        for token in (
            "ring",
            "collusion",
            "shared device",
            "shared card",
            "device",
            "card",
            "ip cohort",
            "bot farm",
            "cohort",
        )
    ):
        return ClaimType.RING

    if len(customer_ids) >= 2 and any(
        token in text
        for token in (
            "linked",
            "shared",
            "same",
            "connected",
            "together",
        )
    ):
        return ClaimType.RING

    return ClaimType.CUSTOM
