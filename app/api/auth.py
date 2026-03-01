from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.admin_account import AdminAccount, AdminRole
from app.services.admin_service import decode_access_token, get_admin_by_id

bearer_scheme = HTTPBearer(auto_error=False)


async def get_optional_current_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> AdminAccount | None:
    if not credentials:
        return None

    token = credentials.credentials
    payload = decode_access_token(token)
    if not payload:
        return None

    subject = payload.get("sub")
    if not subject:
        return None

    try:
        admin_id = UUID(subject)
    except ValueError:
        return None

    admin = await get_admin_by_id(db, admin_id)
    if not admin or not admin.is_active:
        return None

    return admin


async def get_current_admin(
    current_admin: AdminAccount | None = Depends(get_optional_current_admin),
) -> AdminAccount:
    if not current_admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return current_admin


async def require_super_admin(
    current_admin: AdminAccount = Depends(get_current_admin),
) -> AdminAccount:
    if current_admin.role != AdminRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required",
        )
    return current_admin


def ensure_group_access(current_admin: AdminAccount, group_id: UUID) -> None:
    if current_admin.role == AdminRole.SUPER_ADMIN:
        return

    if current_admin.role != AdminRole.GROUP_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Group access denied",
        )

    if not current_admin.group_id or current_admin.group_id != group_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only access your assigned group",
        )
