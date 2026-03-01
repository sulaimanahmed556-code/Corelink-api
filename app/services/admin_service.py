"""
Admin Account Service

Handles account creation, authentication, and JWT for dashboard admins.
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from jose import jwt
from loguru import logger
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.admin_account import AdminAccount, AdminRole


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def create_access_token(data: dict[str, Any], expires_minutes: int | None = None) -> str:
    expire = datetime.utcnow() + timedelta(
        minutes=expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {**data, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

async def get_admin_by_email(db: AsyncSession, email: str) -> AdminAccount | None:
    result = await db.execute(select(AdminAccount).where(AdminAccount.email == email))
    return result.scalar_one_or_none()


async def get_admin_by_id(db: AsyncSession, admin_id: UUID) -> AdminAccount | None:
    result = await db.execute(select(AdminAccount).where(AdminAccount.id == admin_id))
    return result.scalar_one_or_none()


async def create_admin_account(
    db: AsyncSession,
    email: str,
    password: str,
    full_name: str | None = None,
    role: AdminRole = AdminRole.SUPER_ADMIN,
    group_id: UUID | None = None,
) -> AdminAccount:
    """Create a new admin account."""
    admin = AdminAccount(
        email=email,
        hashed_password=hash_password(password),
        full_name=full_name,
        role=role,
        group_id=group_id,
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    logger.info(f"Admin account created: {email} ({role.value})")
    return admin


async def authenticate_admin(db: AsyncSession, email: str, password: str) -> AdminAccount | None:
    """Verify credentials and return admin if valid."""
    admin = await get_admin_by_email(db, email)
    if not admin:
        return None
    if not verify_password(password, admin.hashed_password):
        return None
    if not admin.is_active:
        return None
    return admin
