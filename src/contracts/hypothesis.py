from enum import Enum
from pydantic import BaseModel, Field

class ClaimType(str, Enum):
    RING = "ring"
    CHARGEBACK_CASCADE = "chargeback"
    ROUTING_ANOMALY = "routing"
    ENTITY_SIMILARITY = "similarity"
    MONEY_FLOW = "money_flow"
    CUSTOM = "custom"

class EntityScope(BaseModel):
    """Bounded subgraph the investigation should focus on."""
    customer_ids: list[str] = Field(default_factory=list)
    merchant_ids: list[str] = Field(default_factory=list)
    time_window_hours: int | None = None
    min_transaction_amount: float | None = None
    countries: list[str] = Field(default_factory=list)

class Hypothesis(BaseModel):
    raw: str
    claim_type: ClaimType
    scope: EntityScope
    urgency: int = Field(ge=1, le=5, default=3)
