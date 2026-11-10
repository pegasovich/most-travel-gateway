from fastapi import APIRouter
from app.api.payments import router as payments_router
from app.api.reconciliation import router as reconciliation_router

api_router = APIRouter()
api_router.include_router(payments_router)
api_router.include_router(reconciliation_router)
