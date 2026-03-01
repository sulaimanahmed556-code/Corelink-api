"""
Subscription Plan Model

Stores billing plans that admins can manage and users can select at checkout.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Plan(Base):
    """
    Represents a purchasable subscription plan across payment providers.

    Provider IDs are auto-created when a plan is created via the API.
    """

    __tablename__ = "plans"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="USD", nullable=False)

    # Features list e.g. ["churn_detection", "sentiment_analysis", "weekly_reports"]
    features: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    # Billing interval
    interval: Mapped[str] = mapped_column(String(20), default="month", nullable=False)
    interval_count: Mapped[int] = mapped_column(default=1, nullable=False)

    # Provider identifiers — populated automatically on plan creation
    stripe_plan_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    paypal_plan_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    paystack_plan_code: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<Plan(id={self.id}, name='{self.name}', price={self.price} {self.currency})>"
