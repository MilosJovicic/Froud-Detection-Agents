from pydantic import BaseModel, Field


class ProjectionProposal(BaseModel):
    proposal_id: str
    hypothesis_raw: str
    node_query: str
    rel_query: str
    parameters: dict[str, float | int | str | bool | list[str]] = Field(default_factory=dict)
    rationale: str
    status: str
    file_path: str
    autoexec_requested: bool = False
