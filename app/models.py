"""SQLAlchemy ORM models and State Machine transitions for Travel Payments."""

import enum
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set
from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Numeric,
    String,
    Text,
    func,
)
from app.database import Base


class TransactionStatus(str, enum.Enum):
    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    REFUNDED = "REFUNDED"
    VOIDED = "VOIDED"


# Finite State Machine transition rules
ALLOWED_TRANSITIONS: Dict[TransactionStatus, Set[TransactionStatus]] = {
    TransactionStatus.PENDING: {
        TransactionStatus.AUTHORIZED,
        TransactionStatus.VOIDED,
    },
    TransactionStatus.AUTHORIZED: {
        TransactionStatus.CAPTURED,
        TransactionStatus.VOIDED,
    },
    TransactionStatus.CAPTURED: {
        TransactionStatus.REFUNDED,
    },
    TransactionStatus.REFUNDED: set(),  # Terminal state
    TransactionStatus.VOIDED: set(),    # Terminal state
}


class InvalidStateTransitionError(Exception):
    """Raised when an illegal transaction status transition is attempted."""
    def __init__(self, current_status: TransactionStatus, target_status: TransactionStatus):
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(
            f"Illegal state transition from {current_status.value} to {target_status.value}. "
            f"Allowed next states: {[s.value for s in ALLOWED_TRANSITIONS.get(current_status, set())]}"
        )


class Transaction(Base):
    __tablename__ = "transactions"

    # Distributed / client-generated UUID compatible
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Financial idempotency constraint
    idempotency_key = Column(String(128), unique=True, index=True, nullable=False)
    
    # Financial amounts with 2 decimal places precision
    amount = Column(Numeric(12, 2), nullable=False)
    
    # ISO-4217 currency code (e.g., USD, EUR, GBP)
    currency = Column(String(3), nullable=False, index=True)
    
    # Workflow status
    status = Column(
        Enum(TransactionStatus, native_enum=False),
        nullable=False,
        default=TransactionStatus.PENDING,
        index=True,
    )
    
    description = Column(String(255), nullable=True)
    
    # Travel contextual metadata (flight_number, route, seat_number, crew_member, terminal_id)
    metadata_json = Column(Text, nullable=True)
    
    # Audit timestamps (stored with timezone UTC)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    @property
    def travel_metadata(self) -> Optional[Dict[str, Any]]:
        """Parses JSON metadata payload if present."""
        if self.metadata_json:
            try:
                return json.loads(self.metadata_json)
            except Exception:
                return None
        return None

    @travel_metadata.setter
    def travel_metadata(self, data: Optional[Dict[str, Any]]) -> None:
        if data is not None:
            self.metadata_json = json.dumps(data)
        else:
            self.metadata_json = None

    def transition_to(self, new_status: TransactionStatus) -> None:
        """Transitions transaction to new status enforcing strict state machine."""
        allowed = ALLOWED_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidStateTransitionError(self.status, new_status)
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc)
