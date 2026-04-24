import pytest
from pydantic import BaseModel, ValidationError

from verifiers import BINDINGS, LIBRARY, verify


EXPECTED_TEMPLATE_IDS = {
    "count_shared_entity",
    "confirm_component",
    "confirm_merchant_cochargeback",
    "confirm_payout_cluster",
    "confirm_money_flow_path",
    "confirm_decline_routing",
}


AGREE_CASES = [
    (
        "count_shared_entity",
        {
            "customer_ids": ["C1_1", "C1_2"],
            "connector_type": "device",
            "min_shared_count": 1,
        },
        1,
    ),
    (
        "confirm_component",
        {
            "customer_ids": ["C1_1", "C1_8"],
            "connector_type": "device",
        },
        2,
    ),
    (
        "confirm_merchant_cochargeback",
        {
            "merchant_ids": ["M1", "M2"],
            "min_shared_customers": 2,
        },
        2,
    ),
    (
        "confirm_payout_cluster",
        {
            "merchant_ids": ["M1", "M2", "M4"],
            "require_shared_account": True,
            "require_shared_country": True,
        },
        1,
    ),
    (
        "confirm_money_flow_path",
        {
            "acquirer_id": "ACQ_1",
            "issuer_id": "ISS_1",
            "transaction_ids": ["TX1", "TX2"],
            "min_transaction_amount": 200.0,
            "min_path_count": 2,
        },
        2,
    ),
    (
        "confirm_decline_routing",
        {
            "acquirer_ids": ["ACQ_1", "ACQ_2"],
            "min_decline_count": 2,
            "min_transaction_count": 1,
        },
        2,
    ),
]


DISAGREE_CASES = [
    (
        "count_shared_entity",
        {
            "customer_ids": ["C1_1", "C2_1"],
            "connector_type": "device",
            "min_shared_count": 1,
        },
    ),
    (
        "confirm_component",
        {
            "customer_ids": ["C1_1", "C2_1"],
            "connector_type": "device",
        },
    ),
    (
        "confirm_merchant_cochargeback",
        {
            "merchant_ids": ["M1", "M3"],
            "min_shared_customers": 1,
        },
    ),
    (
        "confirm_payout_cluster",
        {
            "merchant_ids": ["M1", "M3"],
            "require_shared_account": True,
            "require_shared_country": True,
        },
    ),
    (
        "confirm_money_flow_path",
        {
            "acquirer_id": "ACQ_3",
            "issuer_id": "ISS_2",
            "transaction_ids": ["TX5", "TX6"],
            "min_transaction_amount": 400.0,
            "min_path_count": 2,
        },
    ),
    (
        "confirm_decline_routing",
        {
            "acquirer_ids": ["ACQ_1", "ACQ_3"],
            "min_decline_count": 2,
            "min_transaction_count": 1,
        },
    ),
]


@pytest.mark.asyncio
async def test_verifier_registry_matches_spec():
    assert set(LIBRARY) == EXPECTED_TEMPLATE_IDS
    assert set(BINDINGS) == EXPECTED_TEMPLATE_IDS


@pytest.mark.asyncio
async def test_each_template_has_binding_model():
    for template_id, model_cls in BINDINGS.items():
        assert template_id in LIBRARY
        assert issubclass(model_cls, BaseModel)


@pytest.mark.asyncio
@pytest.mark.parametrize(("template_id", "bindings", "minimum_row_count"), AGREE_CASES)
async def test_verify_returns_agree_for_known_true_claims(
    neo4j_session,
    template_id,
    bindings,
    minimum_row_count,
):
    result = await verify(neo4j_session, template_id, bindings)

    assert result.outcome == "AGREE"
    assert result.evidence_cypher
    assert "MATCH" in result.evidence_cypher
    assert result.row_count >= minimum_row_count


@pytest.mark.asyncio
@pytest.mark.parametrize(("template_id", "bindings"), DISAGREE_CASES)
async def test_verify_returns_disagree_for_known_false_claims(
    neo4j_session,
    template_id,
    bindings,
):
    result = await verify(neo4j_session, template_id, bindings)

    assert result.outcome == "DISAGREE"
    assert result.evidence_cypher
    assert result.row_count >= 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bindings", "minimum_row_count"),
    [
        (
            {
                "merchant_ids": ["M1", "M2", "M4"],
                "require_shared_account": True,
                "require_shared_country": False,
            },
            1,
        ),
        (
            {
                "merchant_ids": ["M1", "M2", "M4"],
                "require_shared_account": False,
                "require_shared_country": True,
            },
            1,
        ),
    ],
)
async def test_confirm_payout_cluster_supports_individual_flags(
    neo4j_session,
    bindings,
    minimum_row_count,
):
    result = await verify(neo4j_session, "confirm_payout_cluster", bindings)

    assert result.outcome == "AGREE"
    assert result.row_count >= minimum_row_count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("template_id", "bindings"),
    [
        (
            "count_shared_entity",
            {"customer_ids": [], "connector_type": "device"},
        ),
        (
            "count_shared_entity",
            {"customer_ids": ["C1_1", "C1_1"], "connector_type": "device"},
        ),
        (
            "confirm_component",
            {"customer_ids": ["C1_1", "C1_1"], "connector_type": "device"},
        ),
        (
            "confirm_merchant_cochargeback",
            {"merchant_ids": []},
        ),
        (
            "confirm_decline_routing",
            {"acquirer_ids": []},
        ),
    ],
)
async def test_verify_rejects_empty_or_collapsed_identifier_lists(
    neo4j_session,
    template_id,
    bindings,
):
    with pytest.raises(ValidationError):
        await verify(neo4j_session, template_id, bindings)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("template_id", "bindings"),
    [
        (
            "count_shared_entity",
            {
                "customer_ids": ["C1_1", "C1_2"],
                "connector_type": "device",
                "min_shared_count": 0,
            },
        ),
        (
            "confirm_merchant_cochargeback",
            {
                "merchant_ids": ["M1", "M2"],
                "min_shared_customers": 0,
            },
        ),
        (
            "confirm_payout_cluster",
            {
                "merchant_ids": ["M1", "M2"],
                "require_shared_account": False,
                "require_shared_country": False,
            },
        ),
        (
            "confirm_money_flow_path",
            {
                "acquirer_id": "ACQ_1",
                "issuer_id": "ISS_1",
                "min_transaction_amount": -1.0,
            },
        ),
        (
            "confirm_money_flow_path",
            {
                "acquirer_id": "ACQ_1",
                "issuer_id": "ISS_1",
                "min_path_count": 0,
            },
        ),
        (
            "confirm_decline_routing",
            {
                "acquirer_ids": ["ACQ_1"],
                "min_decline_count": 0,
            },
        ),
        (
            "confirm_decline_routing",
            {
                "acquirer_ids": ["ACQ_1"],
                "min_transaction_count": 0,
            },
        ),
    ],
)
async def test_verify_rejects_invalid_thresholds_and_flags(
    neo4j_session,
    template_id,
    bindings,
):
    with pytest.raises(ValidationError):
        await verify(neo4j_session, template_id, bindings)


@pytest.mark.asyncio
async def test_verify_raises_key_error_for_unknown_template(neo4j_session):
    with pytest.raises(KeyError):
        await verify(neo4j_session, "does_not_exist", {})
