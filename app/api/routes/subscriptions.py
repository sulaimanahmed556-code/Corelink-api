"""
CORELINK Subscriptions Admin Routes

Read-only admin view of all subscriptions with plan and group info.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_super_admin
from app.database import get_db
from app.models import Subscription, Group, Plan
from app.models.subscription import PaymentProvider, SubscriptionStatus

router = APIRouter(dependencies=[Depends(require_super_admin)])


def _subscription_to_dict(sub: Subscription) -> dict:
    group = sub.group
    plan = sub.plan

    return {
        "id": str(sub.id),
        "group_id": str(sub.group_id),
        "group_name": group.name if group else None,
        "telegram_group_id": str(group.telegram_group_id) if group else None,
        "plan_id": str(sub.plan_id) if sub.plan_id else None,
        "plan_name": plan.name if plan else None,
        "plan_price": str(plan.price) if plan else None,
        "plan_currency": plan.currency if plan else None,
        "provider": sub.provider.value,
        "status": sub.status.value,
        "provider_subscription_id": sub.provider_subscription_id,
        "subscriber_email": sub.subscriber_email,
        "current_period_start": sub.current_period_start.isoformat() if sub.current_period_start else None,
        "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
        "created_at": sub.created_at.isoformat(),
        "updated_at": sub.updated_at.isoformat(),
    }


@router.get("/")
async def list_subscriptions(
    status_filter: Optional[SubscriptionStatus] = None,
    provider_filter: Optional[PaymentProvider] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """List all subscriptions with plan and group details."""
    try:
        query = select(Subscription)

        if status_filter:
            query = query.where(Subscription.status == status_filter)
        if provider_filter:
            query = query.where(Subscription.provider == provider_filter)

        query = query.order_by(Subscription.created_at.desc()).limit(limit).offset(offset)

        result = await db.execute(query)
        subs = result.scalars().all()

        return JSONResponse(
            content={
                "total": len(subs),
                "limit": limit,
                "offset": offset,
                "subscriptions": [_subscription_to_dict(s) for s in subs],
            }
        )
    except Exception as exc:
        logger.error(f"Error listing subscriptions: {exc}")
        raise HTTPException(status_code=500, detail="Failed to list subscriptions")


@router.get("/{subscription_id}")
async def get_subscription(
    subscription_id: str,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Get a single subscription by ID."""
    try:
        uuid = UUID(subscription_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid subscription_id")

    result = await db.execute(select(Subscription).where(Subscription.id == uuid))
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    return JSONResponse(content=_subscription_to_dict(sub))


@router.post("/{subscription_id}/cancel")
async def cancel_subscription(
    subscription_id: str,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Cancel a subscription."""
    try:
        uuid = UUID(subscription_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid subscription_id")

    result = await db.execute(select(Subscription).where(Subscription.id == uuid))
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    if sub.status == SubscriptionStatus.CANCELED:
        return JSONResponse(content={"status": "already_canceled", "subscription_id": subscription_id})

    sub.status = SubscriptionStatus.CANCELED
    await db.commit()

    return JSONResponse(content={"status": "canceled", "subscription_id": subscription_id})
