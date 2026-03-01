"""
Telegram Message Model

SQLAlchemy model for storing Telegram messages in CORELINK.
"""

from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import DateTime, Float, ForeignKey, Text, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Message(Base):
    """
    Represents a Telegram message stored for analysis.
    
    Links messages to groups and users, tracks sentiment analysis results.
    """
    
    __tablename__ = "messages"
    
    # Primary key
    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
        doc="Unique identifier for the message"
    )
    
    # Foreign keys
    group_id: Mapped[UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        doc="Reference to the group where message was sent"
    )
    
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        doc="Reference to the user who sent the message"
    )
    
    # Message content
    text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Message text content"
    )
    
    # Analysis results
    sentiment_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Sentiment analysis score (-1.0 to 1.0, negative to positive)"
    )
    
    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        index=True,
        nullable=False,
        doc="When the message was created"
    )
    
    # Relationships (optional, for easier querying)
    group: Mapped["Group"] = relationship(
        "Group",
        back_populates="messages",
        lazy="selectin"
    )
    
    user: Mapped["User"] = relationship(
        "User",
        back_populates="messages",
        lazy="selectin"
    )
    
    # Composite index for common query pattern: messages in a group ordered by time
    __table_args__ = (
        Index("ix_messages_group_created", "group_id", "created_at"),
    )
    
    def __repr__(self) -> str:
        """String representation of the Message."""
        text_preview = self.text[:50] + "..." if len(self.text) > 50 else self.text
        return f"<Message(id={self.id}, group_id={self.group_id}, text='{text_preview}')>"
