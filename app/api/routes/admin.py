"""
CORELINK Admin Routes

Administrative endpoints for managing groups, users, and platform analytics.
"""

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_super_admin
from app.database import get_db
from app.models import Group, Plan, Subscription
from app.models.admin_account import AdminAccount
from app.models.subscription import SubscriptionStatus

router = APIRouter(dependencies=[Depends(require_super_admin)])


@router.get("/stats")
async def get_platform_stats(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """
    Platform-wide stats for the admin dashboard overview.

    Returns counts for plans, subscriptions, groups, and admin accounts.
    """
    try:
        # Plans
        total_plans_result = await db.execute(select(func.count()).select_from(Plan))
        total_plans = total_plans_result.scalar() or 0

        active_plans_result = await db.execute(
            select(func.count()).select_from(Plan).where(Plan.is_active.is_(True))
        )
        active_plans = active_plans_result.scalar() or 0

        # Subscriptions
        total_subs_result = await db.execute(select(func.count()).select_from(Subscription))
        total_subs = total_subs_result.scalar() or 0

        active_subs_result = await db.execute(
            select(func.count())
            .select_from(Subscription)
            .where(Subscription.status == SubscriptionStatus.ACTIVE)
        )
        active_subs = active_subs_result.scalar() or 0

        # Groups
        total_groups_result = await db.execute(select(func.count()).select_from(Group))
        total_groups = total_groups_result.scalar() or 0

        active_groups_result = await db.execute(
            select(func.count()).select_from(Group).where(Group.is_active.is_(True))
        )
        active_groups = active_groups_result.scalar() or 0

        # Admin accounts
        total_admins_result = await db.execute(select(func.count()).select_from(AdminAccount))
        total_admins = total_admins_result.scalar() or 0

        return JSONResponse(
            content={
                "total_plans": total_plans,
                "active_plans": active_plans,
                "total_subscriptions": total_subs,
                "active_subscriptions": active_subs,
                "total_groups": total_groups,
                "active_groups": active_groups,
                "total_admin_accounts": total_admins,
            }
        )

    except Exception as exc:
        logger.error(f"Error fetching platform stats: {exc}")
        return JSONResponse(
            content={
                "total_plans": 0,
                "active_plans": 0,
                "total_subscriptions": 0,
                "active_subscriptions": 0,
                "total_groups": 0,
                "active_groups": 0,
                "total_admin_accounts": 0,
            }
        )


@router.get("/groups")
async def list_groups(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """List all registered groups with pagination."""
    try:
        result = await db.execute(
            select(Group).order_by(Group.created_at.desc()).limit(limit).offset(skip)
        )
        groups = result.scalars().all()

        return JSONResponse(
            content={
                "total": len(groups),
                "groups": [
                    {
                        "id": str(g.id),
                        "telegram_group_id": str(g.telegram_group_id),
                        "name": g.name,
                        "is_active": g.is_active,
                        "has_made_payment": g.has_made_payment,
                        "admin_consented_at": g.admin_consented_at.isoformat() if g.admin_consented_at else None,
                        "created_at": g.created_at.isoformat(),
                    }
                    for g in groups
                ],
            }
        )
    except Exception as exc:
        logger.error(f"Error listing groups: {exc}")
        return JSONResponse(content={"status": "error"}, status_code=500)


@router.get("/groups/{group_id}")
async def get_group_details(group_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Get detailed information about a specific group."""
    from uuid import UUID

    try:
        uuid = UUID(group_id)
    except ValueError:
        return JSONResponse(content={"detail": "Invalid group_id"}, status_code=400)

    result = await db.execute(select(Group).where(Group.id == uuid))
    group = result.scalar_one_or_none()
    if not group:
        return JSONResponse(content={"detail": "Group not found"}, status_code=404)

    sub = group.subscription
    return JSONResponse(
        content={
            "id": str(group.id),
            "telegram_group_id": str(group.telegram_group_id),
            "name": group.name,
            "is_active": group.is_active,
            "has_made_payment": group.has_made_payment,
            "admin_consented_at": group.admin_consented_at.isoformat() if group.admin_consented_at else None,
            "created_at": group.created_at.isoformat(),
            "subscription": {
                "id": str(sub.id),
                "status": sub.status.value,
                "provider": sub.provider.value,
                "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
            } if sub else None,
        }
    )


@router.post("/groups/{group_id}/activate")
async def activate_group(group_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    from uuid import UUID
    try:
        uuid = UUID(group_id)
    except ValueError:
        return JSONResponse(content={"detail": "Invalid group_id"}, status_code=400)

    result = await db.execute(select(Group).where(Group.id == uuid))
    group = result.scalar_one_or_none()
    if not group:
        return JSONResponse(content={"detail": "Group not found"}, status_code=404)

    group.is_active = True
    await db.commit()
    return JSONResponse(content={"status": "activated", "group_id": group_id})


@router.post("/groups/{group_id}/deactivate")
async def deactivate_group(group_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    from uuid import UUID
    try:
        uuid = UUID(group_id)
    except ValueError:
        return JSONResponse(content={"detail": "Invalid group_id"}, status_code=400)

    result = await db.execute(select(Group).where(Group.id == uuid))
    group = result.scalar_one_or_none()
    if not group:
        return JSONResponse(content={"detail": "Group not found"}, status_code=404)

    group.is_active = False
    await db.commit()
    return JSONResponse(content={"status": "deactivated", "group_id": group_id})


@router.get("/users")
async def list_users(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    from app.models import User
    try:
        result = await db.execute(
            select(User).order_by(User.last_active.desc()).limit(limit).offset(skip)
        )
        users = result.scalars().all()
        return JSONResponse(
            content={
                "total": len(users),
                "users": [
                    {
                        "id": str(u.id),
                        "telegram_user_id": str(u.telegram_user_id),
                        "username": u.username,
                        "first_seen": u.first_seen.isoformat(),
                        "last_active": u.last_active.isoformat(),
                    }
                    for u in users
                ],
            }
        )
    except Exception as exc:
        logger.error(f"Error listing users: {exc}")
        return JSONResponse(content={"status": "error"}, status_code=500)
