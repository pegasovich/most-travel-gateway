"""QA Automation Test Suite: Store-and-Forward Inflight Offline Batch Sync.

Tests cover:
- Clean offline batch ingestion
- Partial success handling: gracefully skipping duplicates (both pre-existing DB and within-batch)
- Full batch re-transmission deduplication (POS retry scenario)
- Preservation of travel context (flight number, terminal ID, offline clock timestamp)
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models import Transaction, TransactionStatus


@pytest.mark.asyncio
async def test_offline_batch_sync_happy_path(
    async_client: AsyncClient,
    db_session: Session,
):
    """Verify that a batch of offline inflight purchases is ingested cleanly."""
    batch_payload = {
        "batch_id": f"batch-{uuid.uuid4()}",
        "flight_id": "LH-402",
        "terminal_id": "POS-CREW-PAD-01",
        "transactions": [
            {
                "idempotency_key": f"tx-offline-1-{uuid.uuid4()}",
                "amount": 18.50,
                "currency": "EUR",
                "description": "Artisan sandwich & sparkling water",
                "status": "CAPTURED",
                "metadata": {"seat": "07A"},
            },
            {
                "idempotency_key": f"tx-offline-2-{uuid.uuid4()}",
                "amount": 210.00,
                "currency": "EUR",
                "description": "Duty free watch",
                "status": "CAPTURED",
                "metadata": {"seat": "14C"},
            },
        ],
    }

    response = await async_client.post(
        "/api/v1/payments/sync-offline-batch",
        json=batch_payload,
    )
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["batch_id"] == batch_payload["batch_id"]
    assert data["total_received"] == 2
    assert data["processed"] == 2
    assert data["duplicates_skipped"] == 0
    assert data["errors"] == 0

    # Validate results array
    for item in data["results"]:
        assert item["status"] == "SYNCED"
        assert item["transaction_id"] is not None

    # Database verification
    db_session.expire_all()
    records = db_session.query(Transaction).all()
    assert len(records) == 2
    for rec in records:
        assert rec.status == TransactionStatus.CAPTURED
        # Travel metadata should combine batch and item context
        meta = rec.travel_metadata
        assert meta["flight_id"] == "LH-402"
        assert meta["terminal_id"] == "POS-CREW-PAD-01"
        assert meta["synced_batch_id"] == batch_payload["batch_id"]


@pytest.mark.asyncio
async def test_offline_batch_sync_partial_success_with_duplicates(
    async_client: AsyncClient,
    db_session: Session,
):
    """
    Test Partial Success Handling:
    Upload an offline batch containing:
    1. A transaction already saved in DB from prior sync.
    2. A brand-new transaction.
    3. Another brand-new transaction.
    4. A duplicate of item 2 within the exact same batch.

    System must ingest the 2 new ones, gracefully skip the 2 duplicates,
    and avoid rolling back or failing the batch.
    """
    existing_key = f"pre-existing-{uuid.uuid4()}"
    new_key_1 = f"new-item-1-{uuid.uuid4()}"
    new_key_2 = f"new-item-2-{uuid.uuid4()}"

    # Pre-seed one record in the central DB
    pre_seeded_tx = Transaction(
        id=str(uuid.uuid4()),
        idempotency_key=existing_key,
        amount=Decimal("50.00"),
        currency="USD",
        status=TransactionStatus.CAPTURED,
        description="Earlier synced item",
    )
    db_session.add(pre_seeded_tx)
    db_session.commit()

    batch_payload = {
        "flight_id": "UA-909",
        "terminal_id": "POS-TERMINAL-02",
        "transactions": [
            # 1. Duplicate of pre-existing DB record
            {
                "idempotency_key": existing_key,
                "amount": 50.00,
                "currency": "USD",
                "description": "Pre-existing item repeat",
                "status": "CAPTURED",
            },
            # 2. Fresh new item
            {
                "idempotency_key": new_key_1,
                "amount": 12.00,
                "currency": "USD",
                "description": "Snack bar",
                "status": "CAPTURED",
            },
            # 3. Fresh new item
            {
                "idempotency_key": new_key_2,
                "amount": 85.00,
                "currency": "USD",
                "description": "Noise-cancelling headphones",
                "status": "CAPTURED",
            },
            # 4. Intra-batch duplicate of item 2
            {
                "idempotency_key": new_key_1,
                "amount": 12.00,
                "currency": "USD",
                "description": "Duplicate of snack bar in same upload",
                "status": "CAPTURED",
            },
        ],
    }

    response = await async_client.post(
        "/api/v1/payments/sync-offline-batch",
        json=batch_payload,
    )
    assert response.status_code == 200, response.text
    result = response.json()

    # Metrics assertions: Partial success handled gracefully
    assert result["total_received"] == 4
    assert result["processed"] == 2
    assert result["duplicates_skipped"] == 2
    assert result["errors"] == 0

    results_by_key = {r["idempotency_key"]: r for r in result["results"]}

    # Check item 1: Skipped because it was already in DB
    assert results_by_key[existing_key]["status"] == "DUPLICATE_SKIPPED"
    assert results_by_key[existing_key]["transaction_id"] == pre_seeded_tx.id

    # Check item 2: Synced
    assert results_by_key[new_key_1]["status"] in ("SYNCED", "DUPLICATE_SKIPPED")
    # Check item 3: Synced
    assert results_by_key[new_key_2]["status"] == "SYNCED"

    # Database state: exactly 3 total transactions (1 pre-seeded + 2 new)
    db_session.expire_all()
    all_db_txs = db_session.query(Transaction).all()
    assert len(all_db_txs) == 3, f"Expected 3 records in DB, found {len(all_db_txs)}"


@pytest.mark.asyncio
async def test_full_batch_resend_handling(
    async_client: AsyncClient,
    db_session: Session,
):
    """Simulate a flight terminal retrying the entire batch upload after a network interruption."""
    batch_payload = {
        "batch_id": f"batch-retry-{uuid.uuid4()}",
        "flight_id": "BA-189",
        "terminal_id": "POS-PAD-03",
        "transactions": [
            {
                "idempotency_key": f"ba-tx1-{uuid.uuid4()}",
                "amount": 29.00,
                "currency": "GBP",
                "status": "CAPTURED",
            },
            {
                "idempotency_key": f"ba-tx2-{uuid.uuid4()}",
                "amount": 42.00,
                "currency": "GBP",
                "status": "CAPTURED",
            },
        ],
    }

    # First attempt: Both succeed
    resp_1 = await async_client.post("/api/v1/payments/sync-offline-batch", json=batch_payload)
    assert resp_1.status_code == 200
    assert resp_1.json()["processed"] == 2
    assert resp_1.json()["duplicates_skipped"] == 0

    # Second attempt: Entire batch is recognized as duplicate without error
    resp_2 = await async_client.post("/api/v1/payments/sync-offline-batch", json=batch_payload)
    assert resp_2.status_code == 200
    data_2 = resp_2.json()
    assert data_2["total_received"] == 2
    assert data_2["processed"] == 0
    assert data_2["duplicates_skipped"] == 2
    assert data_2["errors"] == 0

    # DB records remain exactly 2
    db_session.expire_all()
    assert db_session.query(Transaction).count() == 2


@pytest.mark.asyncio
async def test_offline_timestamp_and_metadata_preservation(
    async_client: AsyncClient,
    db_session: Session,
):
    """Verify offline POS timestamp and flight attributes are preserved in the ledger."""
    recorded_offline_time = datetime(2026, 10, 27, 14, 30, 0, tzinfo=timezone.utc)
    idemp_key = f"clock-test-{uuid.uuid4()}"

    batch_payload = {
        "flight_id": "DL-77",
        "terminal_id": "CREW-IPAD-09",
        "transactions": [
            {
                "idempotency_key": idemp_key,
                "amount": 7.50,
                "currency": "USD",
                "description": "Bottled Water",
                "status": "CAPTURED",
                "offline_created_at": recorded_offline_time.isoformat(),
                "metadata": {"seat": "22D", "passenger_loyalty_tier": "PLATINUM"},
            }
        ],
    }

    resp = await async_client.post("/api/v1/payments/sync-offline-batch", json=batch_payload)
    assert resp.status_code == 200

    db_session.expire_all()
    saved_tx = db_session.query(Transaction).filter_by(idempotency_key=idemp_key).first()
    assert saved_tx is not None
    meta = saved_tx.travel_metadata
    assert meta["flight_id"] == "DL-77"
    assert meta["terminal_id"] == "CREW-IPAD-09"
    assert meta["passenger_loyalty_tier"] == "PLATINUM"
    assert meta["seat"] == "22D"
    assert meta["offline_created_at"] == recorded_offline_time.isoformat()
