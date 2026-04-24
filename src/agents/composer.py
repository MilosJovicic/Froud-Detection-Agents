import json
import os
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from agents.model import qwen_model
from contracts.hypothesis import Hypothesis
from metrics import record_agent_tokens


class ProjectionProposalDraft(BaseModel):
    node_query: str
    rel_query: str
    parameters: dict[str, float | int | str | bool | list[str]] = Field(default_factory=dict)
    rationale: str


COMPOSER_SYSTEM_PROMPT = """You propose a Neo4j GDS projection for a custom fraud hypothesis.
Write only reviewable proposal output.
Return JSON with:
- node_query: Cypher returning id(n) AS id and labels(n) AS labels
- rel_query: Cypher returning source, target, and type
- parameters: only bool, int, float, or string values
- rationale: why this projection fits the hypothesis

Use only the provided schema summary. Do not invent labels or relationship types."""


def _composer_agent() -> Agent:
    return Agent(
        qwen_model(temperature=0.2, max_tokens=512),
        output_type=ProjectionProposalDraft,
        system_prompt=COMPOSER_SYSTEM_PROMPT,
        name="ProjectionComposerAgent",
        retries=0,
    )


async def _run_composer_llm(
    hypothesis: Hypothesis,
    schema_summary: dict[str, Any],
    *,
    feedback: str | None = None,
) -> ProjectionProposalDraft:
    prompt_parts = [
        "Hypothesis:",
        hypothesis.model_dump_json(indent=2),
        "",
        "Neo4j schema summary:",
        json.dumps(schema_summary, indent=2, sort_keys=True),
        "",
        "Return a review-only projection proposal.",
    ]
    if feedback:
        prompt_parts.extend(["", "Validation feedback from the previous attempt:", feedback])

    prompt = "\n".join(prompt_parts)
    try:
        result = await _composer_agent().run(prompt)
    except Exception:
        record_agent_tokens("ProjectionComposerAgent", prompt=prompt, output="", status="failure")
        raise

    record_agent_tokens("ProjectionComposerAgent", prompt=prompt, output=result.output)
    return result.output


async def compose_projection_proposal(
    hypothesis: Hypothesis,
    schema_summary: dict[str, Any],
) -> ProjectionProposalDraft:
    mode = os.getenv("COMPOSER_MODE", "llm").strip().lower()
    fallback = _rule_based_projection_draft(hypothesis)

    if mode == "rule-based":
        return fallback

    try:
        first_attempt = await _run_composer_llm(hypothesis, schema_summary)
        return _validate_projection_draft(first_attempt)
    except Exception as exc:
        try:
            second_attempt = await _run_composer_llm(
                hypothesis,
                schema_summary,
                feedback=str(exc),
            )
            return _validate_projection_draft(second_attempt)
        except Exception:
            return fallback


def _validate_projection_draft(
    output: ProjectionProposalDraft | dict[str, Any],
) -> ProjectionProposalDraft:
    draft = ProjectionProposalDraft.model_validate(output)

    node_query = draft.node_query.strip()
    rel_query = draft.rel_query.strip()
    rationale = draft.rationale.strip()

    if not node_query:
        raise ValueError("node_query must not be blank")
    if not rel_query:
        raise ValueError("rel_query must not be blank")
    if not rationale:
        raise ValueError("rationale must not be blank")

    _ensure_required_aliases(
        node_query,
        required_aliases={" AS id", " AS labels"},
        field_name="node_query",
    )
    _ensure_required_aliases(
        rel_query,
        required_aliases={" AS source", " AS target", " AS type"},
        field_name="rel_query",
    )

    parameters = {
        key: value
        for key, value in draft.parameters.items()
        if isinstance(value, (bool, int, float, str))
        or (
            isinstance(value, list)
            and all(isinstance(item, str) for item in value)
        )
    }

    return draft.model_copy(
        update={
            "node_query": node_query,
            "rel_query": rel_query,
            "rationale": rationale,
            "parameters": parameters,
        }
    )


def _rule_based_projection_draft(hypothesis: Hypothesis) -> ProjectionProposalDraft:
    text = hypothesis.raw.lower()
    countries = hypothesis.scope.countries

    if "merchant" in text and ("customer" in text or "customers" in text):
        return ProjectionProposalDraft(
            node_query="""
            MATCH (customer:Customer)-[:TRANSACTED_AT]->(merchant:Merchant)
            WHERE size($countries) = 0
               OR EXISTS {
                    MATCH (merchant)-[:PAYOUT_TO]->(:PayoutAccount)-[:REGISTERED_IN]->(country:Country)
                    WHERE country.id IN $countries
               }
            RETURN DISTINCT id(customer) AS id, labels(customer) AS labels
            UNION
            MATCH (customer:Customer)-[:TRANSACTED_AT]->(merchant:Merchant)
            WHERE size($countries) = 0
               OR EXISTS {
                    MATCH (merchant)-[:PAYOUT_TO]->(:PayoutAccount)-[:REGISTERED_IN]->(country:Country)
                    WHERE country.id IN $countries
               }
            RETURN DISTINCT id(merchant) AS id, labels(merchant) AS labels
            """.strip(),
            rel_query="""
            MATCH (customer:Customer)-[:TRANSACTED_AT]->(merchant:Merchant)
            WHERE size($countries) = 0
               OR EXISTS {
                    MATCH (merchant)-[:PAYOUT_TO]->(:PayoutAccount)-[:REGISTERED_IN]->(country:Country)
                    WHERE country.id IN $countries
               }
            RETURN id(customer) AS source, id(merchant) AS target, 'TRANSACTED_AT' AS type
            """.strip(),
            parameters={"countries": countries},
            rationale="Rule-based composer linked customers to merchants so analysts can review overlapping customer activity for a custom merchant hypothesis.",
        )

    if "merchant" in text and ("country" in text or countries):
        return ProjectionProposalDraft(
            node_query="""
            MATCH (merchant:Merchant)-[:PAYOUT_TO]->(account:PayoutAccount)-[:REGISTERED_IN]->(country:Country)
            WHERE size($countries) = 0 OR country.id IN $countries
            RETURN DISTINCT id(merchant) AS id, labels(merchant) AS labels
            UNION
            MATCH (merchant:Merchant)-[:PAYOUT_TO]->(account:PayoutAccount)-[:REGISTERED_IN]->(country:Country)
            WHERE size($countries) = 0 OR country.id IN $countries
            RETURN DISTINCT id(account) AS id, labels(account) AS labels
            UNION
            MATCH (merchant:Merchant)-[:PAYOUT_TO]->(account:PayoutAccount)-[:REGISTERED_IN]->(country:Country)
            WHERE size($countries) = 0 OR country.id IN $countries
            RETURN DISTINCT id(country) AS id, labels(country) AS labels
            """.strip(),
            rel_query="""
            MATCH (merchant:Merchant)-[:PAYOUT_TO]->(account:PayoutAccount)-[:REGISTERED_IN]->(country:Country)
            WHERE size($countries) = 0 OR country.id IN $countries
            RETURN id(merchant) AS source, id(account) AS target, 'PAYOUT_TO' AS type
            UNION
            MATCH (merchant:Merchant)-[:PAYOUT_TO]->(account:PayoutAccount)-[:REGISTERED_IN]->(country:Country)
            WHERE size($countries) = 0 OR country.id IN $countries
            RETURN id(account) AS source, id(country) AS target, 'REGISTERED_IN' AS type
            """.strip(),
            parameters={"countries": countries},
            rationale="Rule-based composer focused on the merchant payout geography because the custom hypothesis referenced merchant-country structure.",
        )

    return ProjectionProposalDraft(
        node_query="""
        MATCH (customer:Customer)-[:TRANSACTED_AT]->(merchant:Merchant)
        RETURN DISTINCT id(customer) AS id, labels(customer) AS labels
        UNION
        MATCH (customer:Customer)-[:TRANSACTED_AT]->(merchant:Merchant)
        RETURN DISTINCT id(merchant) AS id, labels(merchant) AS labels
        """.strip(),
        rel_query="""
        MATCH (customer:Customer)-[:TRANSACTED_AT]->(merchant:Merchant)
        RETURN id(customer) AS source, id(merchant) AS target, 'TRANSACTED_AT' AS type
        """.strip(),
        parameters={},
        rationale="Rule-based composer proposed a general customer-merchant bipartite graph for manual analyst review of a custom fraud hypothesis.",
    )


def _ensure_required_aliases(
    query: str,
    *,
    required_aliases: set[str],
    field_name: str,
) -> None:
    normalized = " ".join(query.upper().split())
    required = {alias.upper() for alias in required_aliases}
    missing = [alias for alias in required if alias not in normalized]
    if missing:
        joined = ", ".join(sorted(missing))
        raise ValueError(f"{field_name} is missing required aliases: {joined}")
