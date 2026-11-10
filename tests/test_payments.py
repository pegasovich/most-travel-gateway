"""QA Automation Test Suite: Payment Lifecycle and State Machine Validation.

Tests cover:
- Happy path payment workflow: PENDING -> AUTHORIZED -> CAPTURED
- Extended transitions: VOID and REFUND
- Negative/boundary testing: Invalid transitions, 404 non-existent resources, schema validation
"""

import uuid
from decimal import Decimal
import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models import Transaction, TransactionStatus


@pytest.mark.asyncio
async def test_happy_path_full_lifecycle(async_client: AsyncClient, db_session: Session):
    """
    Test the complete travel payment state machine lifecycle:
    1. POS initiates transaction with auto_authorize=False -> Status: PENDING
    2. Bank/card network authorization received -> Status: AUTHORIZED
    3. Manifest/flight arrival settlement capture -> Status: CAPTURED
    """
    idempotency_key = f"flow-happy-{uuid.uuid4()}"
    create_payload = {
        "amount": 95.50,
        "currency": "USD",
        "description": "Inflight Premium Seat Upgrade",
        "metadata": {"flight_id": "AA-404", "seat": "03A"},
        "auto_authorize": False,
    }

    # Step 1: Create transaction in PENDING status
    resp_create = await async_client.post(
        "/api/v1/payments",
        json=create_payload,
        headers={"Idempotency-Key": idempotency_key},
    )
    assert resp_create.status_code == 201, resp_create.text
    tx_data = resp_create.json()
    payment_id = tx_data["id"]
    assert tx_data["status"] == TransactionStatus.PENDING.value
    assert Decimal(str(tx_data["amount"])) == Decimal("95.50")
    assert tx_data["currency"] == "USD"

    # Step 2: Authorize transaction -> moves to AUTHORIZED
    resp_auth = await async_client.post(f"/api/v1/payments/{payment_id}/authorize")
    assert resp_auth.status_code == 200, resp_auth.text
    auth_data = resp_auth.json()
    assert auth_data["id"] == payment_id
    assert auth_data["status"] == TransactionStatus.AUTHORIZED.value

    # Step 3: Capture transaction -> moves to CAPTURED
    capture_payload = {"reason": "Flight safely landed, batch settled"}
    resp_capture = await async_client.post(
        f"/api/v1/payments/{payment_id}/capture",
        json=capture_payload,
    )
    assert resp_capture.status_code == 200, resp_capture.text
    captured_data = resp_capture.json()
    assert captured_data["id"] == payment_id
    assert captured_data["status"] == TransactionStatus.CAPTURED.value
    assert captured_data["metadata"]["capture_reason"] == "Flight safely landed, batch settled"

    # Verify directly in SQLite ledger
    db_tx = db_session.query(Transaction).filter_by(id=payment_id).first()
    assert db_tx is not None
    assert db_tx.status == TransactionStatus.CAPTURED
    assert db_tx.amount == Decimal("95.50")


@pytest.mark.asyncio
async def test_direct_authorization_and_capture(async_client: AsyncClient):
    """Test standard payment creation with auto_authorize=True (online POS default)."""
    idempotency_key = f"flow-direct-{uuid.uuid4()}"
    payload = {
        "amount": 25.00,
        "currency": "EUR",
        "description": "Duty Free Snack Box",
        "auto_authorize": True,
    }

    # Created directly as AUTHORIZED
    res = await async_client.post(
        "/api/v1/payments",
        json=payload,
        headers={"Idempotency-Key": idempotency_key},
    )
    assert res.status_code == 201
    tx_id = res.json()["id"]
    assert res.json()["status"] == TransactionStatus.AUTHORIZED.value

    # Capture directly
    cap_res = await async_client.post(f"/api/v1/payments/{tx_id}/capture")
    assert cap_res.status_code == 200
    assert cap_res.json()["status"] == TransactionStatus.CAPTURED.value


@pytest.mark.asyncio
async def test_void_lifecycle_from_pending_and_authorized(async_client: AsyncClient):
    """Verify VOID transition from both PENDING and AUTHORIZED states."""
    # 1. Void from PENDING
    res_pending = await async_client.post(
        "/api/v1/payments",
        json={"amount": 10.00, "currency": "USD", "auto_authorize": False},
        headers={"Idempotency-Key": f"void-pending-{uuid.uuid4()}"},
    )
    tx_pending_id = res_pending.json()["id"]

    void_res1 = await async_client.post(f"/api/v1/payments/{tx_pending_id}/void")
    assert void_res1.status_code == 200
    assert void_res1.json()["status"] == TransactionStatus.VOIDED.value

    # 2. Void from AUTHORIZED
    res_auth = await async_client.post(
        "/api/v1/payments",
        json={"amount": 20.00, "currency": "USD", "auto_authorize": True},
        headers={"Idempotency-Key": f"void-auth-{uuid.uuid4()}"},
    )
    tx_auth_id = res_auth.json()["id"]

    void_res2 = await async_client.post(f"/api/v1/payments/{tx_auth_id}/void")
    assert void_res2.status_code == 200
    assert void_res2.json()["status"] == TransactionStatus.VOIDED.value


@pytest.mark.asyncio
async def test_refund_lifecycle_from_captured(async_client: AsyncClient):
    """Verify REFUND transition from CAPTURED state."""
    res = await async_client.post(
        "/api/v1/payments",
        json={"amount": 150.00, "currency": "GBP", "auto_authorize": True},
        headers={"Idempotency-Key": f"refund-{uuid.uuid4()}"},
    )
    tx_id = res.json()["id"]

    # Must be captured first
    await async_client.post(f"/api/v1/payments/{tx_id}/capture")

    # Issue refund
    refund_res = await async_client.post(f"/api/v1/payments/{tx_id}/refund")
    assert refund_res.status_code == 200
    assert refund_res.json()["status"] == TransactionStatus.REFUNDED.value


# =========================================================================
# Negative & State Machine Enforcement Test Cases
# =========================================================================

@pytest.mark.asyncio
async def test_capture_non_existent_payment_returns_404(async_client: AsyncClient):
    """Verify that capturing a non-existent transaction returns 404 Not Found."""
    fake_id = str(uuid.uuid4())
    res = await async_client.post(f"/api/v1/payments/{fake_id}/capture")
    assert res.status_code == 404
    assert f"Transaction '{fake_id}' not found" in res.json()["detail"]


@pytest.mark.asyncio
async def test_illegal_state_transition_capture_pending_payment(async_client: AsyncClient):
    """Attempting to capture a PENDING payment without authorization must fail with 422."""
    res = await async_client.post(
        "/api/v1/payments",
        json={"amount": 40.00, "currency": "USD", "auto_authorize": False},
        headers={"Idempotency-Key": f"illegal-cap-pending-{uuid.uuid4()}"},
    )
    tx_id = res.json()["id"]
    assert res.json()["status"] == TransactionStatus.PENDING.value

    # Attempt capture
    capture_res = await async_client.post(f"/api/v1/payments/{tx_id}/capture")
    assert capture_res.status_code == 422
    assert "Illegal state transition from PENDING to CAPTURED" in capture_res.json()["detail"]


@pytest.mark.asyncio
async def test_illegal_state_transition_double_capture(async_client: AsyncClient):
    """Attempting to capture an already CAPTURED payment must fail with 422."""
    res = await async_client.post(
        "/api/v1/payments",
        json={"amount": 55.00, "currency": "USD", "auto_authorize": True},
        headers={"Idempotency-Key": f"double-cap-{uuid.uuid4()}"},
    )
    tx_id = res.json()["id"]

    # First capture succeeds
    cap1 = await async_client.post(f"/api/v1/payments/{tx_id}/capture")
    assert cap1.status_code == 200

    # Second capture fails
    cap2 = await async_client.post(f"/api/v1/payments/{tx_id}/capture")
    assert cap2.status_code == 422
    assert "Illegal state transition from CAPTURED to CAPTURED" in cap2.json()["detail"]


@pytest.mark.asyncio
async def test_illegal_state_transition_void_captured_payment(async_client: AsyncClient):
    """Settled/CAPTURED transactions cannot be voided; they must be refunded."""
    res = await async_client.post(
        "/api/v1/payments",
        json={"amount": 100.00, "currency": "USD", "auto_authorize": True},
        headers={"Idempotency-Key": f"void-captured-{uuid.uuid4()}"},
    )
    tx_id = res.json()["id"]
    await async_client.post(f"/api/v1/payments/{tx_id}/capture")

    void_res = await async_client.post(f"/api/v1/payments/{tx_id}/void")
    assert void_res.status_code == 422
    assert "Illegal state transition from CAPTURED to VOIDED" in void_res.json()["detail"]


@pytest.mark.asyncio
async def test_validation_errors_zero_negative_amounts_and_currency(async_client: AsyncClient):
    """Verify input validation guards: zero/negative amounts and bad ISO currencies."""
    # Negative amount
    res1 = await async_client.post(
        "/api/v1/payments",
        json={"amount": -10.00, "currency": "USD"},
    )
    assert res1.status_code == 422

    # Zero amount
    res2 = await async_client.post(
        "/api/v1/payments",
        json={"amount": 0.00, "currency": "USD"},
    )
    assert res2.status_code == 422

    # Invalid currency code length
    res3 = await async_client.post(
        "/api/v1/payments",
        json={"amount": 25.00, "currency": "US"},
    )
    assert res3.status_code == 422
