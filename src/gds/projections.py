from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel


class ProjectionHandle(BaseModel):
    name: str
    node_count: int
    rel_count: int
    created_at: str


def _validate_int(name: str, value: int, *, minimum: int = 1) -> int:
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _validate_float(name: str, value: float, *, minimum: float = 0.0) -> float:
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


async def _project_graph(
    session: Any,
    *,
    name: str,
    node_query: str,
    rel_query: str,
    parameters: dict[str, Any] | None = None,
) -> ProjectionHandle:
    result = await session.run(
        """
        CALL gds.graph.project.cypher(
            $name,
            $node_query,
            $rel_query,
            {parameters: $parameters}
        )
        YIELD graphName, nodeCount, relationshipCount
        RETURN graphName, nodeCount, relationshipCount
        """,
        {
            "name": name,
            "node_query": node_query,
            "rel_query": rel_query,
            "parameters": parameters or {},
        },
    )

    record = await result.single()
    if record is None:
        raise RuntimeError(f"Failed to project graph {name}")

    return ProjectionHandle(
        name=record["graphName"],
        node_count=record["nodeCount"],
        rel_count=record["relationshipCount"],
        created_at=datetime.now(UTC).isoformat(),
    )


async def project_shared_device_ring(
    session: Any,
    *,
    min_customers_per_device: int = 2,
    name: str = "shared_device_ring",
) -> ProjectionHandle:
    _validate_int("min_customers_per_device", min_customers_per_device, minimum=2)

    node_query = """
    MATCH (d:Device)<-[:USES_DEVICE]-(c:Customer)
    WITH d, count(c) AS cnt
    WHERE cnt >= $min_customers
    MATCH (n)
    WHERE n = d OR (n:Customer AND (n)-[:USES_DEVICE]->(d))
    RETURN DISTINCT id(n) AS id, labels(n) AS labels
    """

    rel_query = """
    MATCH (d:Device)<-[:USES_DEVICE]-(c:Customer)
    WITH d, count(c) AS cnt
    WHERE cnt >= $min_customers
    MATCH (c:Customer)-[:USES_DEVICE]->(d)
    RETURN id(c) AS source, id(d) AS target, 'USES_DEVICE' AS type
    """

    return await _project_graph(
        session,
        name=name,
        node_query=node_query,
        rel_query=rel_query,
        parameters={"min_customers": min_customers_per_device},
    )


async def project_shared_card_ring(
    session: Any,
    *,
    min_customers_per_card: int = 2,
    name: str = "shared_card_ring",
) -> ProjectionHandle:
    _validate_int("min_customers_per_card", min_customers_per_card, minimum=2)

    node_query = """
    MATCH (card:Card)<-[:USES_CARD]-(customer:Customer)
    WITH card, count(customer) AS cnt
    WHERE cnt >= $min_customers
    MATCH (n)
    WHERE n = card OR (n:Customer AND (n)-[:USES_CARD]->(card))
    RETURN DISTINCT id(n) AS id, labels(n) AS labels
    """

    rel_query = """
    MATCH (card:Card)<-[:USES_CARD]-(customer:Customer)
    WITH card, count(customer) AS cnt
    WHERE cnt >= $min_customers
    MATCH (customer:Customer)-[:USES_CARD]->(card)
    RETURN id(customer) AS source, id(card) AS target, 'USES_CARD' AS type
    """

    return await _project_graph(
        session,
        name=name,
        node_query=node_query,
        rel_query=rel_query,
        parameters={"min_customers": min_customers_per_card},
    )


async def project_ip_cohort(
    session: Any,
    *,
    min_customers_per_ip: int = 2,
    name: str = "ip_cohort",
) -> ProjectionHandle:
    _validate_int("min_customers_per_ip", min_customers_per_ip, minimum=2)

    node_query = """
    MATCH (ip:IP)<-[:USES_IP]-(customer:Customer)
    WITH ip, count(customer) AS cnt
    WHERE cnt >= $min_customers
    MATCH (n)
    WHERE n = ip OR (n:Customer AND (n)-[:USES_IP]->(ip))
    RETURN DISTINCT id(n) AS id, labels(n) AS labels
    """

    rel_query = """
    MATCH (ip:IP)<-[:USES_IP]-(customer:Customer)
    WITH ip, count(customer) AS cnt
    WHERE cnt >= $min_customers
    MATCH (customer:Customer)-[:USES_IP]->(ip)
    RETURN id(customer) AS source, id(ip) AS target, 'USES_IP' AS type
    """

    return await _project_graph(
        session,
        name=name,
        node_query=node_query,
        rel_query=rel_query,
        parameters={"min_customers": min_customers_per_ip},
    )


async def project_merchant_cochargeback(
    session: Any,
    *,
    min_shared_customers: int = 1,
    name: str = "merchant_cochargeback",
) -> ProjectionHandle:
    _validate_int("min_shared_customers", min_shared_customers, minimum=1)

    node_query = """
    MATCH (customer:Customer {charged_back: true})-[:TRANSACTED_AT]->(merchant:Merchant)
    RETURN DISTINCT id(merchant) AS id, labels(merchant) AS labels
    """

    rel_query = """
    MATCH (customer:Customer {charged_back: true})-[:TRANSACTED_AT]->(m1:Merchant)
    MATCH (customer)-[:TRANSACTED_AT]->(m2:Merchant)
    WHERE id(m1) < id(m2)
    WITH m1, m2, count(DISTINCT customer) AS shared_customers
    WHERE shared_customers >= $min_shared_customers
    RETURN id(m1) AS source, id(m2) AS target, 'CO_CHARGEBACK' AS type
    UNION
    MATCH (customer:Customer {charged_back: true})-[:TRANSACTED_AT]->(m1:Merchant)
    MATCH (customer)-[:TRANSACTED_AT]->(m2:Merchant)
    WHERE id(m1) < id(m2)
    WITH m1, m2, count(DISTINCT customer) AS shared_customers
    WHERE shared_customers >= $min_shared_customers
    RETURN id(m2) AS source, id(m1) AS target, 'CO_CHARGEBACK' AS type
    """

    return await _project_graph(
        session,
        name=name,
        node_query=node_query,
        rel_query=rel_query,
        parameters={"min_shared_customers": min_shared_customers},
    )


async def project_money_flow(
    session: Any,
    *,
    min_transaction_amount: float = 0.0,
    name: str = "money_flow",
) -> ProjectionHandle:
    _validate_float("min_transaction_amount", min_transaction_amount, minimum=0.0)

    node_query = """
    MATCH (transaction:Transaction)-[:ROUTED_TO]->(acquirer:Acquirer)-[:SETTLES_WITH]->(issuer:Issuer)
    WHERE transaction.amount >= $min_transaction_amount
    UNWIND [transaction, acquirer, issuer] AS n
    RETURN DISTINCT id(n) AS id, labels(n) AS labels
    """

    rel_query = """
    MATCH (transaction:Transaction)-[:ROUTED_TO]->(acquirer:Acquirer)
    WHERE transaction.amount >= $min_transaction_amount
    RETURN id(transaction) AS source, id(acquirer) AS target, 'ROUTED_TO' AS type
    UNION
    MATCH (transaction:Transaction)-[:ROUTED_TO]->(acquirer:Acquirer)-[:SETTLES_WITH]->(issuer:Issuer)
    WHERE transaction.amount >= $min_transaction_amount
    RETURN DISTINCT id(acquirer) AS source, id(issuer) AS target, 'SETTLES_WITH' AS type
    """

    return await _project_graph(
        session,
        name=name,
        node_query=node_query,
        rel_query=rel_query,
        parameters={"min_transaction_amount": min_transaction_amount},
    )


async def project_payout_cluster(
    session: Any,
    *,
    min_merchants_per_account: int = 2,
    name: str = "payout_cluster",
) -> ProjectionHandle:
    _validate_int("min_merchants_per_account", min_merchants_per_account, minimum=1)

    node_query = """
    MATCH (merchant:Merchant)-[:PAYOUT_TO]->(account:PayoutAccount)-[:REGISTERED_IN]->(country:Country)
    WITH account, country, collect(DISTINCT merchant) AS merchants
    WHERE size(merchants) >= $min_merchants_per_account
    UNWIND merchants + [account, country] AS n
    RETURN DISTINCT id(n) AS id, labels(n) AS labels
    """

    rel_query = """
    MATCH (merchant:Merchant)-[:PAYOUT_TO]->(account:PayoutAccount)-[:REGISTERED_IN]->(country:Country)
    WITH account, country, collect(DISTINCT merchant) AS merchants
    WHERE size(merchants) >= $min_merchants_per_account
    UNWIND merchants AS merchant
    RETURN id(merchant) AS source, id(account) AS target, 'PAYOUT_TO' AS type
    UNION
    MATCH (merchant:Merchant)-[:PAYOUT_TO]->(account:PayoutAccount)-[:REGISTERED_IN]->(country:Country)
    WITH account, country, collect(DISTINCT merchant) AS merchants
    WHERE size(merchants) >= $min_merchants_per_account
    RETURN DISTINCT id(account) AS source, id(country) AS target, 'REGISTERED_IN' AS type
    """

    return await _project_graph(
        session,
        name=name,
        node_query=node_query,
        rel_query=rel_query,
        parameters={"min_merchants_per_account": min_merchants_per_account},
    )


async def project_decline_routing(
    session: Any,
    *,
    min_declines: int = 1,
    name: str = "decline_routing",
) -> ProjectionHandle:
    _validate_int("min_declines", min_declines, minimum=1)

    node_query = """
    MATCH (transaction:Transaction)-[route:ROUTED_TO]->(acquirer:Acquirer)
    WHERE coalesce(route.decline_count, 0) >= $min_declines
    RETURN DISTINCT id(transaction) AS id, labels(transaction) AS labels
    UNION
    MATCH (transaction:Transaction)-[route:ROUTED_TO]->(acquirer:Acquirer)
    WHERE coalesce(route.decline_count, 0) >= $min_declines
    RETURN DISTINCT id(acquirer) AS id, labels(acquirer) AS labels
    """

    rel_query = """
    MATCH (transaction:Transaction)-[route:ROUTED_TO]->(acquirer:Acquirer)
    WHERE coalesce(route.decline_count, 0) >= $min_declines
    RETURN id(transaction) AS source, id(acquirer) AS target, 'DECLINE_ROUTE' AS type
    """

    return await _project_graph(
        session,
        name=name,
        node_query=node_query,
        rel_query=rel_query,
        parameters={"min_declines": min_declines},
    )


LIBRARY = {
    "shared_device_ring": project_shared_device_ring,
    "shared_card_ring": project_shared_card_ring,
    "ip_cohort": project_ip_cohort,
    "merchant_cochargeback": project_merchant_cochargeback,
    "money_flow": project_money_flow,
    "payout_cluster": project_payout_cluster,
    "decline_routing": project_decline_routing,
}
