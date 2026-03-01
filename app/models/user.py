"""
Telegram User Model

SQLAlchemy model for managing Telegram users in CORELINK.
"""

from datetime import datetime
from uuid import uuid4, UUID
from typing import TYPE_CHECKING
from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.message import Message


class User(Base):
    """
    Represents a Telegram user interacting with CORELINK.
    
    Tracks user activity and metadata for analytics and engagement.
    """
    
    __tablename__ = "users"
    
    # Primary key
    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
        doc="Unique identifier for the user"
    )
    
    # Telegram-specific ID (indexed for fast lookups)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger,
        index=True,
        nullable=False,
        doc="Telegram's unique user ID"
    )
    
    # User information
    username: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Telegram username (without @, optional)"
    )
    
    # Activity tracking
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        doc="When the user was first seen"
    )
    
    last_active: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        doc="When the user was last active"
    )
    
    # Relationships
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    
    def __repr__(self) -> str:
        """String representation of the User."""
        username_str = f"@{self.username}" if self.username else "no username"
        return f"<User(id={self.id}, telegram_user_id={self.telegram_user_id}, username={username_str})>"
