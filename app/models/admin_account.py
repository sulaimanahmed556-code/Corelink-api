"""
Admin Account Model

Stores dashboard accounts for super-admins (created by developer requests)
and group admins (auto-created after successful payment).
"""

from datetime import datetime
from enum import Enum as PyEnum
from uuid import UUID, uuid4
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.group import Group


class AdminRole(str, PyEnum):
    SUPER_ADMIN = "super_admin"   # Created by developer request
    GROUP_ADMIN = "group_admin"   # Auto-created after payment


class AdminAccount(Base):
    """
    Dashboard account for admins.

    super_admin: Created via developer request, can manage plans,
                 subscriptions and the whole platform.
    group_admin: Auto-created after a group makes a successful payment.
                 Can only view details for their own group.
    """

    __tablename__ = "admin_accounts"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )

    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    full_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    role: Mapped[AdminRole] = mapped_column(
        Enum(AdminRole, native_enum=False, length=50),
        nullable=False,
        default=AdminRole.GROUP_ADMIN,
    )

    # Only relevant for group_admin role
    group_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("groups.id", ondelete="SET NULL"),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

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

    group: Mapped["Group | None"] = relationship(
        "Group",
        foreign_keys=[group_id],
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<AdminAccount(id={self.id}, email='{self.email}', role={self.role.value})>"
