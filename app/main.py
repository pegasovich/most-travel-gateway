"""FastAPI Main Entrypoint for MOST Travel Retail Payment Gateway."""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.config import settings
from app.database import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure tables exist
    Base.metadata.create_all(bind=engine)
    yield
    # Shutdown logic if required


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=(
        "Simulated MOST Onboard Store & Payment Gateway for high-mobility, "
        "low-connectivity airline and rail retail."
    ),
    lifespan=lifespan,
)

# CORS middleware for back-office web portals & crew dashboard integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routers
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/", tags=["Root"])
def root_info():
    """Service overview & API versioning info."""
    return {
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs_url": "/docs",
        "endpoints": {
            "create_payment": "POST /api/v1/payments",
            "capture_payment": "POST /api/v1/payments/{id}/capture",
            "void_payment": "POST /api/v1/payments/{id}/void",
            "refund_payment": "POST /api/v1/payments/{id}/refund",
            "sync_offline_batch": "POST /api/v1/payments/sync-offline-batch",
            "reconciliation": "GET /api/v1/reconciliation",
        },
    }


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "HEALTHY"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)  # nosec B104
