from pydantic import BaseModel, Field

from contracts.branch import StructuralEvidence
from contracts.hypothesis import Hypothesis


class EvidenceAggregation(BaseModel):
    evidence: list[StructuralEvidence]
    consensus: str
    confidence: float = Field(ge=0.0, le=1.0)
    conflicts: list[str] = Field(default_factory=list)
    agreeing_branches: int = Field(ge=0)
    disagreeing_branches: int = Field(ge=0)
    not_applicable_branches: int = Field(ge=0)


class FraudVerdict(BaseModel):
    hypothesis: Hypothesis
    evidence: list[StructuralEvidence]
    final_assessment: str
    confidence: float
    recommended_actions: list[str]
    trace_id: str
