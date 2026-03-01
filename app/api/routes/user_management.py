"""
CORELINK User Management Routes

- Developer can create super_admin accounts
- Group admin accounts are auto-created after payment
- Login / auth endpoints
"""

import secrets
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import (
    get_current_admin,
    get_optional_current_admin,
    require_super_admin,
)
from app.database import get_db
from app.models import AdminAccount
from app.models.admin_account import AdminRole
from app.services.admin_service import (
    authenticate_admin,
    create_admin_account,
    create_access_token,
    get_admin_by_email,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CreateAdminRequest(BaseModel):
    """Used by developer to create a super_admin account."""
    email: str = Field(..., description="Admin email address")
    full_name: Optional[str] = None
    password: Optional[str] = Field(
        None,
        description="Leave blank to auto-generate a password",
    )
    role: AdminRole = AdminRole.SUPER_ADMIN

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.strip().lower()


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.strip().lower()


class AdminResponse(BaseModel):
    id: str
    email: str
    full_name: Optional[str]
    role: str
    group_id: Optional[str]
    is_active: bool
    created_at: str


def _admin_to_dict(admin: AdminAccount) -> dict:
    return {
        "id": str(admin.id),
        "email": admin.email,
        "full_name": admin.full_name,
        "role": admin.role.value,
        "group_id": str(admin.group_id) if admin.group_id else None,
        "is_active": admin.is_active,
        "created_at": admin.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/create", status_code=status.HTTP_201_CREATED)
async def create_admin(
    req: CreateAdminRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: AdminAccount | None = Depends(get_optional_current_admin),
) -> JSONResponse:
    """
    Developer endpoint: create a super_admin account.
    Returns the generated password if none was provided.
    """
    try:
        total_admins_result = await db.execute(select(func.count()).select_from(AdminAccount))
        total_admins = total_admins_result.scalar() or 0

        if total_admins > 0:
            if not current_admin or current_admin.role != AdminRole.SUPER_ADMIN:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Super admin access required",
                )

        existing = await get_admin_by_email(db, req.email)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An account with this email already exists",
            )

        generated_password = None
        password = req.password
        if not password:
            generated_password = secrets.token_urlsafe(12)
            password = generated_password

        admin = await create_admin_account(
            db=db,
            email=req.email,
            password=password,
            full_name=req.full_name,
            role=req.role,
        )

        response_data = {
            "status": "created",
            "admin": _admin_to_dict(admin),
        }
        if generated_password:
            response_data["generated_password"] = generated_password
            response_data["notice"] = "Save this password — it will not be shown again."

        return JSONResponse(content=response_data, status_code=status.HTTP_201_CREATED)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error creating admin: {exc}")
        raise HTTPException(status_code=500, detail="Failed to create admin account")


@router.post("/login")
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Authenticate admin and return JWT access token."""
    admin = await authenticate_admin(db, req.email, req.password)
    if not admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials or account is inactive",
        )

    token = create_access_token(
        data={
            "sub": str(admin.id),
            "email": admin.email,
            "role": admin.role.value,
            "group_id": str(admin.group_id) if admin.group_id else None,
        }
    )

    return JSONResponse(
        content={
            "access_token": token,
            "token_type": "bearer",
            "admin": _admin_to_dict(admin),
        }
    )


@router.get("/me")
async def get_me(current_admin: AdminAccount = Depends(get_current_admin)) -> JSONResponse:
    return JSONResponse(content=_admin_to_dict(current_admin))


@router.get("/")
async def list_admins(
    role: Optional[AdminRole] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _: AdminAccount = Depends(require_super_admin),
) -> JSONResponse:
    """List all admin accounts."""
    try:
        query = select(AdminAccount)
        if role:
            query = query.where(AdminAccount.role == role)
        query = query.order_by(AdminAccount.created_at.desc()).limit(limit).offset(offset)

        result = await db.execute(query)
        admins = result.scalars().all()

        return JSONResponse(
            content={
                "total": len(admins),
                "admins": [_admin_to_dict(a) for a in admins],
            }
        )
    except Exception as exc:
        logger.error(f"Error listing admins: {exc}")
        raise HTTPException(status_code=500, detail="Failed to list admins")


@router.get("/{admin_id}")
async def get_admin(
    admin_id: str,
    db: AsyncSession = Depends(get_db),
    _: AdminAccount = Depends(require_super_admin),
) -> JSONResponse:
    try:
        uuid = UUID(admin_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid admin_id")

    result = await db.execute(select(AdminAccount).where(AdminAccount.id == uuid))
    admin = result.scalar_one_or_none()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    return JSONResponse(content=_admin_to_dict(admin))


@router.patch("/{admin_id}/toggle-active")
async def toggle_admin_active(
    admin_id: str,
    db: AsyncSession = Depends(get_db),
    _: AdminAccount = Depends(require_super_admin),
) -> JSONResponse:
    """Activate or deactivate an admin account."""
    try:
        uuid = UUID(admin_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid admin_id")

    result = await db.execute(select(AdminAccount).where(AdminAccount.id == uuid))
    admin = result.scalar_one_or_none()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    admin.is_active = not admin.is_active
    await db.commit()

    return JSONResponse(
        content={
            "status": "active" if admin.is_active else "deactivated",
            "admin_id": str(admin.id),
            "is_active": admin.is_active,
        }
    )
