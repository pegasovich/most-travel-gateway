"""Fintech Business Logic: Payment Lifecycle, Idempotency, Offline Sync, and Reconciliation."""

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    Transaction,
    TransactionStatus,
    InvalidStateTransitionError,
)
from app.schemas import (
    OfflineBatchItemResult,
    OfflineBatchSyncRequest,
    OfflineBatchSyncResponse,
    PaymentCaptureRequest,
    PaymentCreate,
    ReconciliationCurrencySummary,
    ReconciliationResponse,
    ReconciliationStatusSummary,
)

logger = logging.getLogger("payments.service")


class TransactionNotFoundError(Exception):
    """Raised when a requested transaction ID does not exist."""
    def __init__(self, transaction_id: str):
        super().__init__(f"Transaction '{transaction_id}' not found.")
        self.transaction_id = transaction_id


class PaymentService:
    @staticmethod
    def create_or_get_payment(
        db: Session,
        payload: PaymentCreate,
        idempotency_key: str,
    ) -> Tuple[Transaction, bool]:
        """
        Idempotent payment creation.
        If a transaction with the same idempotency_key already exists, returns (existing_tx, True).
        Otherwise creates a new transaction and returns (new_tx, False).
        """
        existing = (
            db.query(Transaction)
            .filter(Transaction.idempotency_key == idempotency_key)
            .first()
        )
        if existing:
            logger.info("Idempotent request match found for key: %s (tx: %s)", idempotency_key, existing.id)
            return existing, True

        # Determine initial status
        initial_status = (
            TransactionStatus.AUTHORIZED
            if payload.auto_authorize
            else TransactionStatus.PENDING
        )

        tx = Transaction(
            id=str(uuid.uuid4()),
            idempotency_key=idempotency_key,
            amount=payload.amount,
            currency=payload.currency,
            status=initial_status,
            description=payload.description,
        )
        tx.travel_metadata = payload.metadata or {}

        db.add(tx)
        db.commit()
        db.refresh(tx)
        logger.info("Created new payment transaction: %s [status: %s]", tx.id, tx.status.value)
        return tx, False

    @staticmethod
    def authorize_payment(db: Session, transaction_id: str) -> Transaction:
        """Moves transaction from PENDING to AUTHORIZED."""
        tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
        if not tx:
            raise TransactionNotFoundError(transaction_id)

        tx.transition_to(TransactionStatus.AUTHORIZED)
        db.commit()
        db.refresh(tx)
        logger.info("Authorized payment transaction: %s", tx.id)
        return tx

    @staticmethod
    def capture_payment(
        db: Session,
        transaction_id: str,
        capture_request: Optional[PaymentCaptureRequest] = None,
    ) -> Transaction:
        """
        Moves transaction from AUTHORIZED to CAPTURED.
        Strictly enforces state machine transition.
        """
        tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
        if not tx:
            raise TransactionNotFoundError(transaction_id)

        # Enforce state machine transition
        tx.transition_to(TransactionStatus.CAPTURED)

        if capture_request and capture_request.amount:
            # Partial or specified capture amount
            tx.amount = capture_request.amount

        if capture_request and capture_request.reason:
            meta = tx.travel_metadata or {}
            meta["capture_reason"] = capture_request.reason
            tx.travel_metadata = meta

        db.commit()
        db.refresh(tx)
        logger.info("Captured payment transaction: %s", tx.id)
        return tx

    @staticmethod
    def void_payment(db: Session, transaction_id: str) -> Transaction:
        """Voids an uncaptured transaction (PENDING or AUTHORIZED)."""
        tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
        if not tx:
            raise TransactionNotFoundError(transaction_id)

        tx.transition_to(TransactionStatus.VOIDED)
        db.commit()
        db.refresh(tx)
        logger.info("Voided payment transaction: %s", tx.id)
        return tx

    @staticmethod
    def refund_payment(db: Session, transaction_id: str) -> Transaction:
        """Refunds a settled (CAPTURED) transaction."""
        tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
        if not tx:
            raise TransactionNotFoundError(transaction_id)

        tx.transition_to(TransactionStatus.REFUNDED)
        db.commit()
        db.refresh(tx)
        logger.info("Refunded payment transaction: %s", tx.id)
        return tx

    @staticmethod
    def get_payment(db: Session, transaction_id: str) -> Transaction:
        """Retrieves a single transaction by ID."""
        tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
        if not tx:
            raise TransactionNotFoundError(transaction_id)
        return tx

    @staticmethod
    def sync_offline_batch(
        db: Session,
        batch: OfflineBatchSyncRequest,
    ) -> OfflineBatchSyncResponse:
        """
        Synchronizes offline transactions collected during low/no connectivity flights.
        Handles duplicates gracefully without rolling back non-duplicate records.
        """
        batch_id = batch.batch_id or str(uuid.uuid4())
        results: List[OfflineBatchItemResult] = []
        processed_count = 0
        duplicates_skipped = 0
        errors_count = 0

        # Maintain in-memory set to prevent within-batch duplicates
        seen_keys_in_batch = set()

        for item in batch.transactions:
            if item.idempotency_key in seen_keys_in_batch:
                duplicates_skipped += 1
                results.append(
                    OfflineBatchItemResult(
                        idempotency_key=item.idempotency_key,
                        status="DUPLICATE_SKIPPED",
                        message="Duplicate idempotency key within the same batch payload.",
                    )
                )
                continue

            seen_keys_in_batch.add(item.idempotency_key)

            # Check if idempotency key already exists in the central ledger
            existing = (
                db.query(Transaction)
                .filter(Transaction.idempotency_key == item.idempotency_key)
                .first()
            )
            if existing:
                duplicates_skipped += 1
                results.append(
                    OfflineBatchItemResult(
                        idempotency_key=item.idempotency_key,
                        transaction_id=existing.id,
                        status="DUPLICATE_SKIPPED",
                        message="Transaction already ingested in earlier sync or online channel.",
                    )
                )
                continue

            try:
                # Merge travel metadata
                item_metadata = item.metadata or {}
                if batch.flight_id:
                    item_metadata["flight_id"] = batch.flight_id
                if batch.terminal_id:
                    item_metadata["terminal_id"] = batch.terminal_id
                if item.offline_created_at:
                    item_metadata["offline_created_at"] = item.offline_created_at.isoformat()
                item_metadata["synced_batch_id"] = batch_id

                tx_id = item.id if item.id else str(uuid.uuid4())
                tx = Transaction(
                    id=tx_id,
                    idempotency_key=item.idempotency_key,
                    amount=item.amount,
                    currency=item.currency,
                    status=item.status,
                    description=item.description,
                )
                tx.travel_metadata = item_metadata

                db.add(tx)
                db.commit()
                db.refresh(tx)

                processed_count += 1
                results.append(
                    OfflineBatchItemResult(
                        idempotency_key=item.idempotency_key,
                        transaction_id=tx.id,
                        status="SYNCED",
                        message="Transaction persisted successfully.",
                    )
                )
            except Exception as e:
                db.rollback()
                errors_count += 1
                logger.exception("Error saving offline transaction %s: %s", item.idempotency_key, e)
                results.append(
                    OfflineBatchItemResult(
                        idempotency_key=item.idempotency_key,
                        status="ERROR",
                        message=str(e),
                    )
                )

        return OfflineBatchSyncResponse(
            batch_id=batch_id,
            total_received=len(batch.transactions),
            processed=processed_count,
            duplicates_skipped=duplicates_skipped,
            errors=errors_count,
            results=results,
        )

    @staticmethod
    def get_reconciliation(
        db: Session,
        currency_filter: Optional[str] = None,
    ) -> ReconciliationResponse:
        """
        Financial reconciliation report:
        Aggregates transaction counts and volume broken down by status and currency.
        """
        query = db.query(
            Transaction.status,
            Transaction.currency,
            func.count(Transaction.id).label("count"),
            func.sum(Transaction.amount).label("total_amount"),
        )

        if currency_filter:
            query = query.filter(Transaction.currency == currency_filter.upper())

        rows = query.group_by(Transaction.status, Transaction.currency).all()

        status_aggregates: Dict[TransactionStatus, Dict[str, Any]] = {
            s: {"count": 0, "total_amount": Decimal("0.00")}
            for s in TransactionStatus
        }
        currency_aggregates: Dict[str, Dict[str, Any]] = {}
        total_tx_count = 0
        total_volume = Decimal("0.00")

        for status, curr, count, total_amt in rows:
            amt = Decimal(str(total_amt or "0.00"))
            
            # Status rollups
            status_aggregates[status]["count"] += count
            status_aggregates[status]["total_amount"] += amt

            # Currency rollups
            if curr not in currency_aggregates:
                currency_aggregates[curr] = {
                    "count": 0,
                    "total_amount": Decimal("0.00"),
                    "status_totals": {},
                }
            currency_aggregates[curr]["count"] += count
            currency_aggregates[curr]["total_amount"] += amt
            currency_aggregates[curr]["status_totals"][status.value] = amt

            total_tx_count += count
            total_volume += amt

        by_status = [
            ReconciliationStatusSummary(
                status=st,
                count=data["count"],
                total_amount=data["total_amount"],
            )
            for st, data in status_aggregates.items()
        ]

        by_currency = [
            ReconciliationCurrencySummary(
                currency=curr,
                count=data["count"],
                total_amount=data["total_amount"],
                breakdown_by_status=data["status_totals"],
            )
            for curr, data in currency_aggregates.items()
        ]

        return ReconciliationResponse(
            generated_at=datetime.now(timezone.utc),
            total_transactions=total_tx_count,
            total_volume=total_volume,
            by_status=by_status,
            by_currency=by_currency,
        )
