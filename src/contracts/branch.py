from pydantic import BaseModel

class BranchSpec(BaseModel):
    projection_id: str
    algorithm_id: str
    params: dict[str, float | int | str | bool]
    rationale: str

class StructuralClaim(BaseModel):
    assertion: str
    entities: list[str]
    score: float
    topology: str

class VerifierResult(BaseModel):
    outcome: str
    evidence_cypher: str
    row_count: int

class StructuralEvidence(BaseModel):
    branch_spec: BranchSpec
    claim: StructuralClaim
    verifier: VerifierResult
    confidence: float
    latency_ms: int
