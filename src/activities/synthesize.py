from temporalio import activity

from agents.synthesizer import synthesize_verdict
from contracts.hypothesis import Hypothesis
from contracts.verdict import EvidenceAggregation, FraudVerdict
from metrics import instrument_activity


@activity.defn(name="synthesize_verdict")
@instrument_activity("synthesize_verdict")
async def synthesize_verdict_activity(
    hypothesis: Hypothesis,
    aggregation: EvidenceAggregation,
    trace_id: str,
) -> FraudVerdict:
    return await synthesize_verdict(hypothesis, aggregation, trace_id=trace_id)
