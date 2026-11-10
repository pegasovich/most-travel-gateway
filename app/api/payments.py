"""Payment gateway endpoints: creation, capture, state workflow, and offline batch sync."""

import uuid
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import InvalidStateTransitionError
from app.schemas import (
    OfflineBatchSyncRequest,
    OfflineBatchSyncResponse,
    PaymentCaptureRequest,
    PaymentCreate,
    TransactionResponse,
)
from app.services import PaymentService, TransactionNotFoundError

UNPROCESSABLE_STATUS = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422)


router = APIRouter(prefix="/payments", tags=["Payments"])


def _to_transaction_response(tx) -> TransactionResponse:
    """Helper to convert ORM model to Pydantic schema with parsed travel metadata."""
    return TransactionResponse(
        id=tx.id,
        idempotency_key=tx.idempotency_key,
        amount=tx.amount,
        currency=tx.currency,
        status=tx.status,
        description=tx.description,
        metadata=tx.travel_metadata,
        created_at=tx.created_at,
        updated_at=tx.updated_at,
    )


@router.post(
    "",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create or authorize a payment",
    description="Creates a transaction. Handles Idempotency-Key header to prevent duplicate charges.",
)
def create_payment(
    payload: PaymentCreate,
    response: Response,
    idempotency_key: Optional[str] = Header(
        None,
        alias="Idempotency-Key",
        description="Client-provided idempotency key (UUID or unique reference)",
    ),
    db: Session = Depends(get_db),
):
    key = idempotency_key or str(uuid.uuid4())
    tx, is_duplicate = PaymentService.create_or_get_payment(
        db=db,
        payload=payload,
        idempotency_key=key,
    )

    if is_duplicate:
        # Standard HTTP idempotent response behavior
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replay"] = "true"

    return _to_transaction_response(tx)


@router.post(
    "/{payment_id}/authorize",
    response_model=TransactionResponse,
    summary="Authorize a pending payment",
    description="Transitions payment status from PENDING to AUTHORIZED.",
)
def authorize_payment(
    payment_id: str,
    db: Session = Depends(get_db),
):
    try:
        tx = PaymentService.authorize_payment(
            db=db,
            transaction_id=payment_id,
        )
        return _to_transaction_response(tx)
    except TransactionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidStateTransitionError as exc:
        raise HTTPException(
            status_code=UNPROCESSABLE_STATUS,
            detail=str(exc),
        )


@router.post(
    "/{payment_id}/capture",
    response_model=TransactionResponse,
    summary="Capture an authorized payment",
    description="Transitions payment status from AUTHORIZED to CAPTURED.",
)
def capture_payment(
    payment_id: str,
    payload: Optional[PaymentCaptureRequest] = None,
    db: Session = Depends(get_db),
):
    try:
        tx = PaymentService.capture_payment(
            db=db,
            transaction_id=payment_id,
            capture_request=payload,
        )
        return _to_transaction_response(tx)
    except TransactionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidStateTransitionError as exc:
        raise HTTPException(
            status_code=UNPROCESSABLE_STATUS,
            detail=str(exc),
        )


@router.post(
    "/sync-offline-batch",
    response_model=OfflineBatchSyncResponse,
    status_code=status.HTTP_200_OK,
    summary="Ingest offline transaction batch",
    description="Accepts batch of inflight/offline transactions. Handles duplicates gracefully without failing the entire batch.",
)
def sync_offline_batch(
    payload: OfflineBatchSyncRequest,
    db: Session = Depends(get_db),
):
    batch_summary = PaymentService.sync_offline_batch(db=db, batch=payload)
    return batch_summary


@router.get(
    "/{payment_id}",
    response_model=TransactionResponse,
    summary="Retrieve payment details",
)
def get_payment(
    payment_id: str,
    db: Session = Depends(get_db),
):
    try:
        tx = PaymentService.get_payment(db=db, transaction_id=payment_id)
        return _to_transaction_response(tx)
    except TransactionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/{payment_id}/void",
    response_model=TransactionResponse,
    summary="Void an uncaptured payment",
    description="Transitions payment from PENDING or AUTHORIZED to VOIDED.",
)
def void_payment(
    payment_id: str,
    db: Session = Depends(get_db),
):
    try:
        tx = PaymentService.void_payment(db=db, transaction_id=payment_id)
        return _to_transaction_response(tx)
    except TransactionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidStateTransitionError as exc:
        raise HTTPException(
            status_code=UNPROCESSABLE_STATUS,
            detail=str(exc),
        )


@router.post(
    "/{payment_id}/refund",
    response_model=TransactionResponse,
    summary="Refund a captured payment",
    description="Transitions payment from CAPTURED to REFUNDED.",
)
def refund_payment(
    payment_id: str,
    db: Session = Depends(get_db),
):
    try:
        tx = PaymentService.refund_payment(db=db, transaction_id=payment_id)
        return _to_transaction_response(tx)
    except TransactionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidStateTransitionError as exc:
        raise HTTPException(
            status_code=UNPROCESSABLE_STATUS,
            detail=str(exc),
        )
