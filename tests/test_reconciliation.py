"""QA Automation Test Suite: Back-Office Reconciliation Reporting."""

import uuid
from decimal import Decimal
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_reconciliation_totals_and_aggregations(async_client: AsyncClient):
    """
    Test reconciliation report accurately groups counts and monetary volumes
    by transaction status and currency breakdown.
    """
    # 1. USD Captured: $100.00
    res_1 = await async_client.post(
        "/api/v1/payments",
        json={"amount": 100.00, "currency": "USD", "auto_authorize": True},
        headers={"Idempotency-Key": f"rec-usd-1-{uuid.uuid4()}"},
    )
    tx1_id = res_1.json()["id"]
    await async_client.post(f"/api/v1/payments/{tx1_id}/capture")

    # 2. USD Authorized (uncaptured): $50.00
    await async_client.post(
        "/api/v1/payments",
        json={"amount": 50.00, "currency": "USD", "auto_authorize": True},
        headers={"Idempotency-Key": f"rec-usd-2-{uuid.uuid4()}"},
    )

    # 3. EUR Captured: €200.00
    res_3 = await async_client.post(
        "/api/v1/payments",
        json={"amount": 200.00, "currency": "EUR", "auto_authorize": True},
        headers={"Idempotency-Key": f"rec-eur-3-{uuid.uuid4()}"},
    )
    tx3_id = res_3.json()["id"]
    await async_client.post(f"/api/v1/payments/{tx3_id}/capture")

    # Fetch global reconciliation
    rec_resp = await async_client.get("/api/v1/reconciliation")
    assert rec_resp.status_code == 200
    data = rec_resp.json()

    assert data["total_transactions"] == 3
    assert Decimal(str(data["total_volume"])) == Decimal("350.00")

    # Check status breakdown
    status_map = {item["status"]: item for item in data["by_status"]}
    assert status_map["CAPTURED"]["count"] == 2
    assert Decimal(str(status_map["CAPTURED"]["total_amount"])) == Decimal("300.00")
    assert status_map["AUTHORIZED"]["count"] == 1
    assert Decimal(str(status_map["AUTHORIZED"]["total_amount"])) == Decimal("50.00")

    # Test currency filtering query parameter
    rec_eur = await async_client.get("/api/v1/reconciliation?currency=EUR")
    assert rec_eur.status_code == 200
    eur_data = rec_eur.json()
    assert eur_data["total_transactions"] == 1
    assert Decimal(str(eur_data["total_volume"])) == Decimal("200.00")
