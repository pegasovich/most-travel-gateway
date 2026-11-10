"""Reconciliation and Back-Office Financial Reporting Endpoints."""

from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import ReconciliationResponse
from app.services import PaymentService

router = APIRouter(prefix="/reconciliation", tags=["Reconciliation"])


@router.get(
    "",
    response_model=ReconciliationResponse,
    summary="Get reconciliation summary",
    description="Returns aggregate summary of transactions by status and totals, with currency breakdown.",
)
def get_reconciliation(
    currency: Optional[str] = Query(
        None,
        min_length=3,
        max_length=3,
        description="Optional ISO currency code filter (e.g. USD, EUR)",
    ),
    db: Session = Depends(get_db),
):
    return PaymentService.get_reconciliation(db=db, currency_filter=currency)
