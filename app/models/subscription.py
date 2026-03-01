"""
Subscription Model
"""

from datetime import datetime
from enum import Enum as PyEnum
from uuid import UUID, uuid4
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.group import Group
    from app.models.plan import Plan


class PaymentProvider(str, PyEnum):
    STRIPE = "stripe"
    PAYSTACK = "paystack"
    PAYPAL = "paypal"


class SubscriptionStatus(str, PyEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    CANCELED = "canceled"
    PAST_DUE = "past_due"


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    group_id: Mapped[UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    plan_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("plans.id", ondelete="SET NULL"),
        nullable=True,
    )

    provider: Mapped[PaymentProvider] = mapped_column(
        Enum(PaymentProvider, native_enum=False, length=50),
        nullable=False,
    )

    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, native_enum=False, length=50),
        nullable=False,
    )

    # Provider-specific subscription identifier (for cancellation etc.)
    provider_subscription_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    # Subscriber email
    subscriber_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    group: Mapped["Group"] = relationship("Group", back_populates="subscription", lazy="selectin")
    plan: Mapped["Plan | None"] = relationship("Plan", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<Subscription(id={self.id}, group_id={self.group_id}, "
            f"provider={self.provider.value}, status={self.status.value})>"
        )

    @property
    def is_active(self) -> bool:
        return self.status == SubscriptionStatus.ACTIVE

    @property
    def is_expired(self) -> bool:
        if not self.current_period_end:
            return False
        return datetime.utcnow() > self.current_period_end
