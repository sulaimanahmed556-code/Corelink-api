"""
Telegram Group Model

SQLAlchemy model for managing Telegram groups in CORELINK.
"""

from datetime import datetime
from uuid import uuid4, UUID
from typing import TYPE_CHECKING
from sqlalchemy import BigInteger, Boolean, DateTime, String, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.message import Message
    from app.models.subscription import Subscription


class Group(Base):
    """
    Represents a Telegram group connected to CORELINK.
    
    Tracks group metadata, activation status, and admin consent.
    """
    
    __tablename__ = "groups"
    
    # Primary key
    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
        doc="Unique identifier for the group"
    )
    
    # Telegram-specific ID (unique and indexed)
    telegram_group_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        index=True,
        nullable=False,
        doc="Telegram's unique group ID"
    )
    
    # Group information
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Group name from Telegram"
    )
    
    # Status tracking
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        doc="Whether the group is actively monitored"
    )
    
    
    # Payment tracking
    has_made_payment: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        doc="Whether the user has made payment"
    )
    
    # Admin consent timestamp
    admin_consented_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="When admin gave consent for monitoring"
    )
    
    # Audit timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        doc="When the group was first added"
    )
    
    # Relationships
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="group",
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    
    subscription: Mapped["Subscription | None"] = relationship(
        "Subscription",
        back_populates="group",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    
    def __repr__(self) -> str:
        """String representation of the Group."""
        return f"<Group(id={self.id}, telegram_group_id={self.telegram_group_id}, name='{self.name}')>"
