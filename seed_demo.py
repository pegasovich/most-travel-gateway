"""Demo seeder to populate sample flight and gate transactions."""

from decimal import Decimal
from app.database import Base, SessionLocal, engine
from app.models import Transaction, TransactionStatus

def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # Check if already seeded
        if db.query(Transaction).first():
            print("Database already contains transactions.")
            return

        demo_txs = [
            Transaction(
                idempotency_key="DEMO-SEAT-12A-01",
                amount=Decimal("12.50"),
                currency="USD",
                status=TransactionStatus.CAPTURED,
                description="Inflight Coffee & Croissant",
                travel_metadata={"flight_id": "AA-100", "seat": "12A", "channel": "INFLIGHT_POS"},
            ),
            Transaction(
                idempotency_key="DEMO-DUTY-FREE-02",
                amount=Decimal("185.00"),
                currency="USD",
                status=TransactionStatus.CAPTURED,
                description="Duty Free Designer Fragrance",
                travel_metadata={"flight_id": "AA-100", "seat": "04F", "channel": "INFLIGHT_POS"},
            ),
            Transaction(
                idempotency_key="DEMO-GATE-UPGRADE-03",
                amount=Decimal("350.00"),
                currency="USD",
                status=TransactionStatus.AUTHORIZED,
                description="Gate Business Class Upgrade",
                travel_metadata={"flight_id": "AA-100", "seat": "02B", "channel": "GATE_KIOSK"},
            ),
            Transaction(
                idempotency_key="DEMO-EUR-SNACK-04",
                amount=Decimal("45.00"),
                currency="EUR",
                status=TransactionStatus.CAPTURED,
                description="Tapas & Wine selection",
                travel_metadata={"flight_id": "IB-315", "seat": "18C", "channel": "INFLIGHT_POS"},
            ),
            Transaction(
                idempotency_key="DEMO-EUR-REFUND-05",
                amount=Decimal("20.00"),
                currency="EUR",
                status=TransactionStatus.REFUNDED,
                description="Defective inflight headphones",
                travel_metadata={"flight_id": "IB-315", "seat": "21A", "channel": "BACKOFFICE"},
            ),
        ]

        db.add_all(demo_txs)
        db.commit()
        print(f"Successfully seeded {len(demo_txs)} demo transactions.")
    finally:
        db.close()

if __name__ == "__main__":
    seed()
