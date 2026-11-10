"""FastAPI Entrypoint: MOST Travel Payment Gateway and Back-Office Simulation."""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import api_router
from app.config import settings
from app.database import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Ensure tables exist
    Base.metadata.create_all(bind=engine)
    yield
    # Teardown logic if needed


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="""
### Travel Payment Gateway & Back-Office API (MOST Simulation)

A resilient, audit-compliant payment processing system engineered for high-mobility travel environments 
(commercial flights, rail, maritime) with offline store-and-forward capability.

#### Key Architectural Capabilities:
- **Financial Idempotency**: Safe payment submissions with `Idempotency-Key` header handling.
- **Workflow State Machine**: `PENDING -> AUTHORIZED -> CAPTURED -> REFUNDED` (or `VOIDED`).
- **Store-and-Forward Offline Batch Sync**: Handles inflight POS offline transaction ingestion and gracefully dedupes.
- **Back-Office Reconciliation**: Real-time aggregation of transactional volume and breakdown by status/currency.
    """,
    lifespan=lifespan,
)

# CORS setup for web-based back-office terminals
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register v1 routes
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/", tags=["Health"])
def root():
    return {
        "system": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "OPERATIONAL",
        "documentation": "/docs",
        "endpoints": {
            "create_payment": "POST /api/v1/payments",
            "capture_payment": "POST /api/v1/payments/{id}/capture",
            "sync_offline_batch": "POST /api/v1/payments/sync-offline-batch",
            "reconciliation": "GET /api/v1/reconciliation",
        },
    }


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "HEALTHY"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
