import re

from temporalio import activity

from agents.router import classify_hypothesis
from contracts.hypothesis import EntityScope, Hypothesis
from metrics import instrument_activity


CUSTOMER_ID_PATTERN = re.compile(r"\b(?:C\d+_\d+|ISOLATE_C\d+)\b", re.IGNORECASE)
MERCHANT_ID_PATTERN = re.compile(r"\bM\d+\b", re.IGNORECASE)
AMOUNT_PATTERN = re.compile(r"(?:over|above|at\s+least|>=?)\s*\$?(\d+(?:\.\d+)?)", re.IGNORECASE)
HOURS_PATTERN = re.compile(r"\b(\d+)\s*hours?\b", re.IGNORECASE)
COUNTRY_PATTERN = re.compile(r"\b(GB|CY|US|UK|DE|FR|HU)\b")


@activity.defn(name="parse_hypothesis")
@instrument_activity("parse_hypothesis")
async def parse_hypothesis_activity(raw_text: str) -> Hypothesis:
    normalized = raw_text.strip()
    if not normalized:
        raise ValueError("raw_text must not be empty")

    claim_type = await classify_hypothesis(normalized)
    scope = EntityScope(
        customer_ids=sorted({match.upper() for match in CUSTOMER_ID_PATTERN.findall(normalized)}),
        merchant_ids=sorted({match.upper() for match in MERCHANT_ID_PATTERN.findall(normalized)}),
        time_window_hours=_extract_time_window_hours(normalized),
        min_transaction_amount=_extract_min_transaction_amount(normalized),
        countries=sorted({match.upper() for match in COUNTRY_PATTERN.findall(normalized)}),
    )

    return Hypothesis(
        raw=normalized,
        claim_type=claim_type,
        scope=scope,
        urgency=_infer_urgency(normalized),
    )


def _extract_time_window_hours(raw_text: str) -> int | None:
    match = HOURS_PATTERN.search(raw_text)
    return int(match.group(1)) if match else None


def _extract_min_transaction_amount(raw_text: str) -> float | None:
    match = AMOUNT_PATTERN.search(raw_text)
    return float(match.group(1)) if match else None


def _infer_urgency(raw_text: str) -> int:
    text = raw_text.lower()
    if any(token in text for token in ("urgent", "immediately", "asap", "critical", "high priority")):
        return 5
    if any(token in text for token in ("suspicious", "fraud", "chargeback", "review now")):
        return 4
    return 3
