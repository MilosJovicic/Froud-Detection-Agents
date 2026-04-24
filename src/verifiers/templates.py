from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from contracts.branch import VerifierResult


ConnectorType = Literal["device", "card", "ip"]

CONNECTOR_MAP = {
    "device": {"label": "Device", "relationship": "USES_DEVICE"},
    "card": {"label": "Card", "relationship": "USES_CARD"},
    "ip": {"label": "IP", "relationship": "USES_IP"},
}


def _normalize_identifier(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _dedupe_identifiers(values: list[str], *, field_name: str) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()

    for value in values:
        normalized = _normalize_identifier(value, field_name=field_name)
        if normalized not in seen:
            deduped.append(normalized)
            seen.add(normalized)

    return deduped


async def _run_supporting_count(
    session: Any,
    query: str,
    parameters: dict[str, Any],
) -> int:
    result = await session.run(query, parameters)
    record = await result.single()
    if record is None:
        return 0
    return int(record["supporting_count"])


def _build_result(
    *,
    query: str,
    supporting_count: int,
    agreed: bool,
) -> VerifierResult:
    return VerifierResult(
        outcome="AGREE" if agreed else "DISAGREE",
        evidence_cypher=query.strip(),
        row_count=supporting_count,
    )


class _VerifierBindings(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CountSharedEntityBindings(_VerifierBindings):
    customer_ids: list[str]
    connector_type: ConnectorType
    min_shared_count: int = Field(default=1, ge=1)

    @field_validator("customer_ids")
    @classmethod
    def validate_customer_ids(cls, values: list[str]) -> list[str]:
        deduped = _dedupe_identifiers(values, field_name="customer_ids")
        if len(deduped) < 2:
            raise ValueError("customer_ids must contain at least 2 unique ids")
        return deduped


class ConfirmComponentBindings(_VerifierBindings):
    customer_ids: list[str]
    connector_type: ConnectorType

    @field_validator("customer_ids")
    @classmethod
    def validate_customer_ids(cls, values: list[str]) -> list[str]:
        deduped = _dedupe_identifiers(values, field_name="customer_ids")
        if len(deduped) < 2:
            raise ValueError("customer_ids must contain at least 2 unique ids")
        return deduped


class ConfirmMerchantCochargebackBindings(_VerifierBindings):
    merchant_ids: list[str]
    min_shared_customers: int = Field(default=1, ge=1)

    @field_validator("merchant_ids")
    @classmethod
    def validate_merchant_ids(cls, values: list[str]) -> list[str]:
        deduped = _dedupe_identifiers(values, field_name="merchant_ids")
        if len(deduped) < 2:
            raise ValueError("merchant_ids must contain at least 2 unique ids")
        return deduped


class ConfirmPayoutClusterBindings(_VerifierBindings):
    merchant_ids: list[str]
    require_shared_account: bool = True
    require_shared_country: bool = True

    @field_validator("merchant_ids")
    @classmethod
    def validate_merchant_ids(cls, values: list[str]) -> list[str]:
        deduped = _dedupe_identifiers(values, field_name="merchant_ids")
        if len(deduped) < 2:
            raise ValueError("merchant_ids must contain at least 2 unique ids")
        return deduped

    @model_validator(mode="after")
    def validate_flags(self) -> "ConfirmPayoutClusterBindings":
        if not self.require_shared_account and not self.require_shared_country:
            raise ValueError(
                "at least one of require_shared_account or require_shared_country must be true"
            )
        return self


class ConfirmMoneyFlowPathBindings(_VerifierBindings):
    acquirer_id: str
    issuer_id: str
    transaction_ids: list[str] = Field(default_factory=list)
    min_transaction_amount: float = Field(default=0.0, ge=0.0)
    min_path_count: int = Field(default=1, ge=1)

    @field_validator("acquirer_id", "issuer_id")
    @classmethod
    def validate_scalar_ids(cls, value: str, info) -> str:
        return _normalize_identifier(value, field_name=info.field_name)

    @field_validator("transaction_ids")
    @classmethod
    def validate_transaction_ids(cls, values: list[str]) -> list[str]:
        return _dedupe_identifiers(values, field_name="transaction_ids")


class ConfirmDeclineRoutingBindings(_VerifierBindings):
    acquirer_ids: list[str]
    min_decline_count: int = Field(default=1, ge=1)
    min_transaction_count: int = Field(default=1, ge=1)

    @field_validator("acquirer_ids")
    @classmethod
    def validate_acquirer_ids(cls, values: list[str]) -> list[str]:
        deduped = _dedupe_identifiers(values, field_name="acquirer_ids")
        if not deduped:
            raise ValueError("acquirer_ids must contain at least 1 unique id")
        return deduped


async def count_shared_entity(
    session: Any,
    bindings: CountSharedEntityBindings,
) -> VerifierResult:
    connector = CONNECTOR_MAP[bindings.connector_type]
    query = f"""
    MATCH (customer:Customer)-[:{connector["relationship"]}]->(connector:{connector["label"]})
    WHERE customer.id IN $customer_ids
    WITH connector, collect(DISTINCT customer.id) AS matched_customers
    WHERE size(matched_customers) = $customer_count
    RETURN count(connector) AS supporting_count
    """

    supporting_count = await _run_supporting_count(
        session,
        query,
        {
            "customer_ids": bindings.customer_ids,
            "customer_count": len(bindings.customer_ids),
        },
    )

    return _build_result(
        query=query,
        supporting_count=supporting_count,
        agreed=supporting_count >= bindings.min_shared_count,
    )


async def confirm_component(
    session: Any,
    bindings: ConfirmComponentBindings,
) -> VerifierResult:
    connector = CONNECTOR_MAP[bindings.connector_type]
    query = f"""
    MATCH (anchor:Customer {{id: $anchor_id}})
    MATCH (anchor)-[:{connector["relationship"]}*0..20]-(reachable:Customer)
    WHERE reachable.id IN $customer_ids
    RETURN count(DISTINCT reachable) AS supporting_count
    """

    supporting_count = await _run_supporting_count(
        session,
        query,
        {
            "anchor_id": bindings.customer_ids[0],
            "customer_ids": bindings.customer_ids,
        },
    )

    return _build_result(
        query=query,
        supporting_count=supporting_count,
        agreed=supporting_count == len(bindings.customer_ids),
    )


async def confirm_merchant_cochargeback(
    session: Any,
    bindings: ConfirmMerchantCochargebackBindings,
) -> VerifierResult:
    query = """
    MATCH (customer:Customer {charged_back: true})-[:TRANSACTED_AT]->(merchant:Merchant)
    WHERE merchant.id IN $merchant_ids
    WITH customer, collect(DISTINCT merchant.id) AS matched_merchants
    WHERE size(matched_merchants) = $merchant_count
    RETURN count(customer) AS supporting_count
    """

    supporting_count = await _run_supporting_count(
        session,
        query,
        {
            "merchant_ids": bindings.merchant_ids,
            "merchant_count": len(bindings.merchant_ids),
        },
    )

    return _build_result(
        query=query,
        supporting_count=supporting_count,
        agreed=supporting_count >= bindings.min_shared_customers,
    )


async def confirm_payout_cluster(
    session: Any,
    bindings: ConfirmPayoutClusterBindings,
) -> VerifierResult:
    if bindings.require_shared_account and bindings.require_shared_country:
        query = """
        MATCH (merchant:Merchant)-[:PAYOUT_TO]->(account:PayoutAccount)-[:REGISTERED_IN]->(country:Country)
        WHERE merchant.id IN $merchant_ids
        WITH account, country, collect(DISTINCT merchant.id) AS matched_merchants
        WHERE size(matched_merchants) = $merchant_count
        RETURN count(*) AS supporting_count
        """
    elif bindings.require_shared_account:
        query = """
        MATCH (merchant:Merchant)-[:PAYOUT_TO]->(account:PayoutAccount)
        WHERE merchant.id IN $merchant_ids
        WITH account, collect(DISTINCT merchant.id) AS matched_merchants
        WHERE size(matched_merchants) = $merchant_count
        RETURN count(*) AS supporting_count
        """
    else:
        query = """
        MATCH (merchant:Merchant)-[:PAYOUT_TO]->(:PayoutAccount)-[:REGISTERED_IN]->(country:Country)
        WHERE merchant.id IN $merchant_ids
        WITH country, collect(DISTINCT merchant.id) AS matched_merchants
        WHERE size(matched_merchants) = $merchant_count
        RETURN count(*) AS supporting_count
        """

    supporting_count = await _run_supporting_count(
        session,
        query,
        {
            "merchant_ids": bindings.merchant_ids,
            "merchant_count": len(bindings.merchant_ids),
        },
    )

    return _build_result(
        query=query,
        supporting_count=supporting_count,
        agreed=supporting_count >= 1,
    )


async def confirm_money_flow_path(
    session: Any,
    bindings: ConfirmMoneyFlowPathBindings,
) -> VerifierResult:
    query = """
    MATCH (transaction:Transaction)-[:ROUTED_TO]->(acquirer:Acquirer {id: $acquirer_id})-[:SETTLES_WITH]->(issuer:Issuer {id: $issuer_id})
    WHERE transaction.amount >= $min_transaction_amount
      AND (size($transaction_ids) = 0 OR transaction.id IN $transaction_ids)
    RETURN count(DISTINCT transaction) AS supporting_count
    """

    supporting_count = await _run_supporting_count(
        session,
        query,
        {
            "acquirer_id": bindings.acquirer_id,
            "issuer_id": bindings.issuer_id,
            "transaction_ids": bindings.transaction_ids,
            "min_transaction_amount": bindings.min_transaction_amount,
        },
    )

    return _build_result(
        query=query,
        supporting_count=supporting_count,
        agreed=supporting_count >= bindings.min_path_count,
    )


async def confirm_decline_routing(
    session: Any,
    bindings: ConfirmDeclineRoutingBindings,
) -> VerifierResult:
    query = """
    MATCH (transaction:Transaction)-[route:ROUTED_TO]->(acquirer:Acquirer)
    WHERE acquirer.id IN $acquirer_ids
      AND coalesce(route.decline_count, 0) >= $min_decline_count
    WITH acquirer.id AS acquirer_id, count(DISTINCT transaction) AS declined_transactions
    WHERE declined_transactions >= $min_transaction_count
    RETURN count(acquirer_id) AS supporting_count
    """

    supporting_count = await _run_supporting_count(
        session,
        query,
        {
            "acquirer_ids": bindings.acquirer_ids,
            "min_decline_count": bindings.min_decline_count,
            "min_transaction_count": bindings.min_transaction_count,
        },
    )

    return _build_result(
        query=query,
        supporting_count=supporting_count,
        agreed=supporting_count == len(bindings.acquirer_ids),
    )


LIBRARY = {
    "count_shared_entity": count_shared_entity,
    "confirm_component": confirm_component,
    "confirm_merchant_cochargeback": confirm_merchant_cochargeback,
    "confirm_payout_cluster": confirm_payout_cluster,
    "confirm_money_flow_path": confirm_money_flow_path,
    "confirm_decline_routing": confirm_decline_routing,
}


BINDINGS = {
    "count_shared_entity": CountSharedEntityBindings,
    "confirm_component": ConfirmComponentBindings,
    "confirm_merchant_cochargeback": ConfirmMerchantCochargebackBindings,
    "confirm_payout_cluster": ConfirmPayoutClusterBindings,
    "confirm_money_flow_path": ConfirmMoneyFlowPathBindings,
    "confirm_decline_routing": ConfirmDeclineRoutingBindings,
}


async def verify(
    session: Any,
    template_id: str,
    bindings: dict[str, Any] | BaseModel,
) -> VerifierResult:
    if template_id not in LIBRARY:
        raise KeyError(template_id)

    model_cls = BINDINGS[template_id]

    if isinstance(bindings, model_cls):
        validated_bindings = bindings
    else:
        payload = bindings.model_dump() if isinstance(bindings, BaseModel) else bindings
        validated_bindings = model_cls.model_validate(payload)

    return await LIBRARY[template_id](session, validated_bindings)
