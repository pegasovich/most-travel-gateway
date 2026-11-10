"""End-to-End Back-Office Reconciliation & Bank Settlement Audit Tests.

Simulates multi-flight airline operations (AA-100, LH-402, BA-178), batch syncs from
cabin crew POS terminals, and audits gateway totals against external banking settlement files.
"""

from decimal import Decimal
from typing import Any, Dict, List
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_multi_flight_reconciliation_happy_path(async_client: AsyncClient):
    """
    Test End-to-End Back-Office Reconciliation across 3 distinct flights and currencies:
    - Flight AA-100 (USD): Inflight snacks, Wi-Fi, and Gate seat upgrade
    - Flight LH-402 (EUR): Duty-free perfume and meal refund
    - Flight BA-178 (GBP): Club World champagne purchase
    """
    # 1. Flight AA-100 offline batch sync (USD)
    aa100_batch = {
        "batch_id": "BATCH-AA100-20261110",
        "flight_id": "AA-100",
        "terminal_id": "CREW-PAD-AA-01",
        "transactions": [
            {
                "idempotency_key": "AA100-TX-SNACK-1",
                "amount": 25.00,
                "currency": "USD",
                "description": "Artisan Cheese & Wine",
                "status": "CAPTURED",
            },
            {
                "idempotency_key": "AA100-TX-WIFI-2",
                "amount": 15.50,
                "currency": "USD",
                "description": "Inflight Full-Flight Wi-Fi",
                "status": "CAPTURED",
            },
        ],
    }
    resp_aa = await async_client.post("/api/v1/payments/sync-offline-batch", json=aa100_batch)
    assert resp_aa.status_code == 200
    assert resp_aa.json()["processed"] == 2

    # 2. AA-100 Gate pre-authorization (USD)
    resp_gate = await async_client.post(
        "/api/v1/payments",
        json={
            "amount": 300.00,
            "currency": "USD",
            "description": "Gate Upgrade to First Class",
            "auto_authorize": True,
        },
        headers={"Idempotency-Key": "GATE-AA100-UPGRADE-03"},
    )
    assert resp_gate.status_code == 201
    assert resp_gate.json()["status"] == "AUTHORIZED"

    # 3. Flight LH-402 offline batch sync (EUR)
    lh402_batch = {
        "batch_id": "BATCH-LH402-20261110",
        "flight_id": "LH-402",
        "terminal_id": "CREW-PAD-LH-09",
        "transactions": [
            {
                "idempotency_key": "LH402-TX-DUTYFREE-1",
                "amount": 120.00,
                "currency": "EUR",
                "description": "Duty Free Fragrance",
                "status": "CAPTURED",
            },
            {
                "idempotency_key": "LH402-TX-REFUND-2",
                "amount": 20.00,
                "currency": "EUR",
                "description": "Defective Inflight Earbuds",
                "status": "REFUNDED",
            },
        ],
    }
    resp_lh = await async_client.post("/api/v1/payments/sync-offline-batch", json=lh402_batch)
    assert resp_lh.status_code == 200
    assert resp_lh.json()["processed"] == 2

    # 4. Flight BA-178 direct payment (GBP)
    resp_ba = await async_client.post(
        "/api/v1/payments",
        json={
            "amount": 45.00,
            "currency": "GBP",
            "description": "Club World Champagne",
            "auto_authorize": True,
        },
        headers={"Idempotency-Key": "BA178-TX-CHAMPAGNE-01"},
    )
    assert resp_ba.status_code == 201
    ba_id = resp_ba.json()["id"]

    # Capture the BA-178 payment
    resp_cap = await async_client.post(f"/api/v1/payments/{ba_id}/capture", json={"amount": 45.00})
    assert resp_cap.status_code == 200
    assert resp_cap.json()["status"] == "CAPTURED"

    # 5. Fetch Back-Office Financial Reconciliation Report
    resp_recon = await async_client.get("/api/v1/reconciliation")
    assert resp_recon.status_code == 200
    recon = resp_recon.json()

    # Total audit assertions
    assert recon["total_transactions"] == 6
    # Total volume: 25.00 + 15.50 + 300.00 + 120.00 + 20.00 + 45.00 = 525.50
    assert Decimal(recon["total_volume"]) == Decimal("525.50")

    # Status breakdown assertions
    status_map = {item["status"]: item for item in recon["by_status"]}
    assert status_map["CAPTURED"]["count"] == 4
    assert Decimal(status_map["CAPTURED"]["total_amount"]) == Decimal("205.50")  # 25 + 15.50 + 120 + 45
    assert status_map["AUTHORIZED"]["count"] == 1
    assert Decimal(status_map["AUTHORIZED"]["total_amount"]) == Decimal("300.00")
    assert status_map["REFUNDED"]["count"] == 1
    assert Decimal(status_map["REFUNDED"]["total_amount"]) == Decimal("20.00")

    # Currency breakdown assertions
    curr_map = {item["currency"]: item for item in recon["by_currency"]}
    assert "USD" in curr_map
    assert curr_map["USD"]["count"] == 3
    assert Decimal(curr_map["USD"]["total_amount"]) == Decimal("340.50")

    assert "EUR" in curr_map
    assert curr_map["EUR"]["count"] == 2
    assert Decimal(curr_map["EUR"]["total_amount"]) == Decimal("140.00")

    assert "GBP" in curr_map
    assert curr_map["GBP"]["count"] == 1
    assert Decimal(curr_map["GBP"]["total_amount"]) == Decimal("45.00")


@pytest.mark.asyncio
async def test_reconciliation_settlement_discrepancy_audit(async_client: AsyncClient):
    """
    Simulates a Bank Settlement Audit.
    Compares our internal Captured ledger against a mock bank settlement file
    and asserts that discrepancies (e.g. fee drift or dropped settlement records) are caught.
    """
    # Create and capture two transactions
    t1_resp = await async_client.post(
        "/api/v1/payments",
        json={"amount": 100.00, "currency": "USD", "description": "Duty Free Watch", "auto_authorize": True},
        headers={"Idempotency-Key": "DISCREPANCY-TEST-TX-1"},
    )
    t1_id = t1_resp.json()["id"]
    await async_client.post(f"/api/v1/payments/{t1_id}/capture", json={"amount": 100.00})

    t2_resp = await async_client.post(
        "/api/v1/payments",
        json={"amount": 50.00, "currency": "USD", "description": "Inflight Comfort Kit", "auto_authorize": True},
        headers={"Idempotency-Key": "DISCREPANCY-TEST-TX-2"},
    )
    t2_id = t2_resp.json()["id"]
    await async_client.post(f"/api/v1/payments/{t2_id}/capture", json={"amount": 50.00})

    # Fetch Gateway USD reconciliation
    resp = await async_client.get("/api/v1/reconciliation?currency=USD")
    recon_usd = resp.json()
    captured_usd_total = Decimal(recon_usd["by_currency"][0]["breakdown_by_status"]["CAPTURED"])
    assert captured_usd_total == Decimal("150.00")

    # Mock External Bank Settlement Report
    def audit_settlement(bank_records: List[Dict[str, Any]], expected_total: Decimal) -> Dict[str, Any]:
        bank_total = sum(Decimal(str(r["settled_amount"])) for r in bank_records)
        diff = bank_total - expected_total
        return {
            "is_balanced": diff == Decimal("0.00"),
            "difference": diff,
            "bank_total": bank_total,
            "gateway_total": expected_total,
        }

    # Case A: Perfectly balanced bank report
    balanced_bank_file = [
        {"tx_id": t1_id, "settled_amount": "100.00"},
        {"tx_id": t2_id, "settled_amount": "50.00"},
    ]
    audit_a = audit_settlement(balanced_bank_file, captured_usd_total)
    assert audit_a["is_balanced"] is True
    assert audit_a["difference"] == Decimal("0.00")

    # Case B: Discrepancy detected (Bank only settled $149.95 due to 5 cent fee drift)
    discrepant_bank_file = [
        {"tx_id": t1_id, "settled_amount": "100.00"},
        {"tx_id": t2_id, "settled_amount": "49.95"},  # 5 cents missing
    ]
    audit_b = audit_settlement(discrepant_bank_file, captured_usd_total)
    assert audit_b["is_balanced"] is False
    assert audit_b["difference"] == Decimal("-0.05")
