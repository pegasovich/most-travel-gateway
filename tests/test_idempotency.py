"""QA Automation Test Suite: Financial Idempotency & Deduplication Engine.

Tests cover:
- Duplicate request handling using the Idempotency-Key header
- Strict database assertion: zero duplicate records in the transactions ledger
- Idempotent-Replay response headers and status codes (201 Created vs 200 OK)
- Multi-replay consistency across sequential duplicate dispatches
"""

import uuid
from decimal import Decimal
import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models import Transaction


@pytest.mark.asyncio
async def test_duplicate_request_does_not_create_db_duplicate(
    async_client: AsyncClient,
    db_session: Session,
):
    """
    Core Financial Idempotency Test:
    Verify that repeating an identical payment request with the same Idempotency-Key
    returns the original transaction and ensures exactly ONE record exists in the DB.
    """
    idempotency_key = f"idemp-guard-{uuid.uuid4()}"
    payload = {
        "amount": 149.99,
        "currency": "USD",
        "description": "Inflight WiFi Full Flight Pass",
        "metadata": {"device_mac": "AA:BB:CC:DD:EE:FF", "seat": "11C"},
        "auto_authorize": True,
    }

    # 1. Initial request -> Should return 201 Created
    response_1 = await async_client.post(
        "/api/v1/payments",
        json=payload,
        headers={"Idempotency-Key": idempotency_key},
    )
    assert response_1.status_code == 201, response_1.text
    data_1 = response_1.json()
    created_tx_id = data_1["id"]
    assert data_1["idempotency_key"] == idempotency_key
    assert Decimal(str(data_1["amount"])) == Decimal("149.99")
    assert "Idempotent-Replay" not in response_1.headers

    # Database assertion: exactly 1 transaction in DB
    initial_count = db_session.query(Transaction).filter_by(idempotency_key=idempotency_key).count()
    assert initial_count == 1, f"Expected 1 record in database, found {initial_count}"

    # 2. Duplicate request (e.g. timeout retry from satellite connection)
    response_2 = await async_client.post(
        "/api/v1/payments",
        json=payload,
        headers={"Idempotency-Key": idempotency_key},
    )
    assert response_2.status_code == 200, response_2.text
    data_2 = response_2.json()

    # Verify standard idempotent response markers
    assert response_2.headers.get("Idempotent-Replay") == "true"
    assert data_2["id"] == created_tx_id
    assert data_2["idempotency_key"] == idempotency_key
    assert Decimal(str(data_2["amount"])) == Decimal("149.99")

    # CRITICAL LEDGER VERIFICATION:
    # Expire / refresh session to ensure reading actual persisted state
    db_session.expire_all()
    final_count = db_session.query(Transaction).filter_by(idempotency_key=idempotency_key).count()
    assert final_count == 1, (
        f"CRITICAL FINANCIAL FAILURE: Duplicate transaction created in DB! Count={final_count}"
    )

    total_db_records = db_session.query(Transaction).count()
    assert total_db_records == 1, f"Expected total table count to be 1, got {total_db_records}"


@pytest.mark.asyncio
async def test_multiple_sequential_duplicate_replays(
    async_client: AsyncClient,
    db_session: Session,
):
    """Verify that multiple consecutive duplicate retries all safely resolve to the same record."""
    idempotency_key = f"idemp-stress-{uuid.uuid4()}"
    payload = {
        "amount": 30.00,
        "currency": "EUR",
        "description": "Cabin Bistro Tapas",
        "auto_authorize": True,
    }

    # First dispatch
    first_resp = await async_client.post(
        "/api/v1/payments",
        json=payload,
        headers={"Idempotency-Key": idempotency_key},
    )
    assert first_resp.status_code == 201
    original_id = first_resp.json()["id"]

    # Replay 4 times in a row
    for attempt in range(1, 5):
        replay_resp = await async_client.post(
            "/api/v1/payments",
            json=payload,
            headers={"Idempotency-Key": idempotency_key},
        )
        assert replay_resp.status_code == 200, f"Attempt {attempt} failed"
        assert replay_resp.headers.get("Idempotent-Replay") == "true"
        assert replay_resp.json()["id"] == original_id

    # Verify DB still contains exactly one transaction
    db_session.expire_all()
    tx_count = db_session.query(Transaction).filter_by(idempotency_key=idempotency_key).count()
    assert tx_count == 1


@pytest.mark.asyncio
async def test_distinct_idempotency_keys_create_distinct_transactions(
    async_client: AsyncClient,
    db_session: Session,
):
    """Verify that distinct idempotency keys properly create independent ledger records."""
    key_1 = f"key-alpha-{uuid.uuid4()}"
    key_2 = f"key-beta-{uuid.uuid4()}"

    resp_1 = await async_client.post(
        "/api/v1/payments",
        json={"amount": 10.00, "currency": "USD"},
        headers={"Idempotency-Key": key_1},
    )
    resp_2 = await async_client.post(
        "/api/v1/payments",
        json={"amount": 10.00, "currency": "USD"},
        headers={"Idempotency-Key": key_2},
    )

    assert resp_1.status_code == 201
    assert resp_2.status_code == 201
    assert resp_1.json()["id"] != resp_2.json()["id"]

    db_session.expire_all()
    assert db_session.query(Transaction).count() == 2


@pytest.mark.asyncio
async def test_omitted_idempotency_key_generates_unique_records(
    async_client: AsyncClient,
    db_session: Session,
):
    """Verify requests omitting the header are safely assigned unique generated keys."""
    resp_1 = await async_client.post(
        "/api/v1/payments",
        json={"amount": 5.00, "currency": "USD"},
    )
    resp_2 = await async_client.post(
        "/api/v1/payments",
        json={"amount": 5.00, "currency": "USD"},
    )

    assert resp_1.status_code == 201
    assert resp_2.status_code == 201
    assert resp_1.json()["id"] != resp_2.json()["id"]
    assert resp_1.json()["idempotency_key"] != resp_2.json()["idempotency_key"]

    db_session.expire_all()
    assert db_session.query(Transaction).count() == 2
