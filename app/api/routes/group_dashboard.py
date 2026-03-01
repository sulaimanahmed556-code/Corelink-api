"""
CORELINK Group Owner Dashboard Routes

These endpoints are consumed by the group owner (group_admin) dashboard.
A group owner can see:
- Their group overview
- All members and their churn risk scores
- Message/interaction analytics
- Weekly AI-generated agenda + their custom agenda
- Conversation transcription/summary for the week
"""

from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ensure_group_access, get_current_admin
from app.database import get_db
from app.models import Group, Message, Subscription, User
from app.models.admin_account import AdminAccount
from app.services.churn import get_detailed_churn_analysis, UserActivity
from app.services.summarization import (
    generate_weekly_agenda,
    summarize_messages,
    summarize_topics,
)

router = APIRouter()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _parse_uuid(value: str, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field}")


async def _get_group_or_404(db: AsyncSession, group_id: UUID) -> Group:
    result = await db.execute(select(Group).where(Group.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


# ─── schemas ─────────────────────────────────────────────────────────────────

class AgendaUpsert(BaseModel):
    """Group owner's own planned agenda for the week."""
    week_start: str = Field(..., description="ISO date of Monday e.g. 2025-02-24")
    items: list[str] = Field(..., description="List of agenda items")
    notes: Optional[str] = None


# In-memory agenda store (replace with DB model in production)
_custom_agendas: dict[str, dict] = {}


# ─── endpoints ───────────────────────────────────────────────────────────────

@router.get("/{group_id}/overview")
async def get_group_overview(
    group_id: str,
    db: AsyncSession = Depends(get_db),
    current_admin: AdminAccount = Depends(get_current_admin),
) -> JSONResponse:
    """
    Group overview card — subscription, member count, message stats.
    """
    gid = _parse_uuid(group_id, "group_id")
    ensure_group_access(current_admin, gid)
    group = await _get_group_or_404(db, gid)

    # Member count (distinct users who sent a message in this group)
    member_result = await db.execute(
        select(func.count(func.distinct(Message.user_id))).where(
            Message.group_id == gid
        )
    )
    member_count = member_result.scalar() or 0

    # Total messages
    msg_count_result = await db.execute(
        select(func.count()).select_from(Message).where(Message.group_id == gid)
    )
    total_messages = msg_count_result.scalar() or 0

    # Messages last 7 days
    week_ago = datetime.utcnow() - timedelta(days=7)
    week_msg_result = await db.execute(
        select(func.count())
        .select_from(Message)
        .where(and_(Message.group_id == gid, Message.created_at >= week_ago))
    )
    weekly_messages = week_msg_result.scalar() or 0

    # Average sentiment last 7 days
    avg_sent_result = await db.execute(
        select(func.avg(Message.sentiment_score)).where(
            and_(
                Message.group_id == gid,
                Message.created_at >= week_ago,
                Message.sentiment_score.isnot(None),
            )
        )
    )
    avg_sentiment = avg_sent_result.scalar()

    # Subscription info
    sub = group.subscription

    return JSONResponse(
        content={
            "group_id": str(group.id),
            "name": group.name,
            "telegram_group_id": str(group.telegram_group_id),
            "is_active": group.is_active,
            "member_count": member_count,
            "total_messages": total_messages,
            "weekly_messages": weekly_messages,
            "avg_sentiment_7d": round(float(avg_sentiment), 3) if avg_sentiment else None,
            "subscription": {
                "status": sub.status.value if sub else "none",
                "provider": sub.provider.value if sub else None,
                "plan_id": str(sub.plan_id) if sub and sub.plan_id else None,
                "current_period_end": sub.current_period_end.isoformat() if sub and sub.current_period_end else None,
            } if sub else None,
        }
    )


@router.get("/{group_id}/members")
async def get_group_members(
    group_id: str,
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    current_admin: AdminAccount = Depends(get_current_admin),
) -> JSONResponse:
    """
    All members with churn risk scores and interaction stats.
    """
    gid = _parse_uuid(group_id, "group_id")
    ensure_group_access(current_admin, gid)
    await _get_group_or_404(db, gid)

    since = datetime.utcnow() - timedelta(days=days)

    # Get all users who have sent messages in this group
    user_msg_result = await db.execute(
        select(
            User.id,
            User.username,
            User.telegram_user_id,
            User.last_active,
            func.count(Message.id).label("msg_count"),
            func.avg(Message.sentiment_score).label("avg_sentiment"),
        )
        .join(Message, Message.user_id == User.id)
        .where(Message.group_id == gid)
        .group_by(User.id)
        .order_by(desc("msg_count"))
    )
    rows = user_msg_result.all()

    users_activity: list[UserActivity] = []
    for row in rows:
        # Get recent messages for behaviour analysis
        recent_msgs_result = await db.execute(
            select(Message.text)
            .where(and_(Message.group_id == gid, Message.user_id == row.id))
            .order_by(desc(Message.created_at))
            .limit(50)
        )
        recent_texts = [r[0] for r in recent_msgs_result.all()]

        freq = row.msg_count / max(days, 1)
        users_activity.append(
            UserActivity(
                user_id=str(row.id),
                username=row.username,
                telegram_user_id=row.telegram_user_id,
                last_active=row.last_active,
                sentiment_trend=float(row.avg_sentiment) if row.avg_sentiment else 0.0,
                message_frequency=freq,
                recent_messages=recent_texts,
            )
        )

    churn_analysis = await get_detailed_churn_analysis(users_activity)

    return JSONResponse(
        content={
            "group_id": group_id,
            "total_members": len(churn_analysis),
            "analysis_period_days": days,
            "members": churn_analysis,
        }
    )


@router.get("/{group_id}/member/{user_id}/interactions")
async def get_member_interactions(
    group_id: str,
    user_id: str,
    days: int = 7,
    db: AsyncSession = Depends(get_db),
    current_admin: AdminAccount = Depends(get_current_admin),
) -> JSONResponse:
    """
    Conversation timeline and interaction details for a single member.
    """
    gid = _parse_uuid(group_id, "group_id")
    ensure_group_access(current_admin, gid)
    uid = _parse_uuid(user_id, "user_id")

    since = datetime.utcnow() - timedelta(days=days)

    msgs_result = await db.execute(
        select(Message)
        .where(
            and_(
                Message.group_id == gid,
                Message.user_id == uid,
                Message.created_at >= since,
            )
        )
        .order_by(desc(Message.created_at))
        .limit(200)
    )
    messages = msgs_result.scalars().all()

    # Daily message counts
    daily: dict[str, int] = {}
    for msg in messages:
        day = msg.created_at.strftime("%Y-%m-%d")
        daily[day] = daily.get(day, 0) + 1

    # Sentiment timeline
    sentiment_timeline = [
        {
            "date": m.created_at.strftime("%Y-%m-%d %H:%M"),
            "score": round(m.sentiment_score, 3) if m.sentiment_score is not None else None,
            "text_preview": m.text[:80] + ("…" if len(m.text) > 80 else ""),
        }
        for m in messages
    ]

    return JSONResponse(
        content={
            "user_id": user_id,
            "group_id": group_id,
            "period_days": days,
            "message_count": len(messages),
            "daily_counts": daily,
            "sentiment_timeline": sentiment_timeline,
        }
    )


@router.get("/{group_id}/weekly-summary")
async def get_weekly_summary(
    group_id: str,
    db: AsyncSession = Depends(get_db),
    current_admin: AdminAccount = Depends(get_current_admin),
) -> JSONResponse:
    """
    AI-generated summary and agenda of the past 7 days of group activity.
    """
    gid = _parse_uuid(group_id, "group_id")
    ensure_group_access(current_admin, gid)
    group = await _get_group_or_404(db, gid)

    week_ago = datetime.utcnow() - timedelta(days=7)

    msgs_result = await db.execute(
        select(Message.text)
        .where(and_(Message.group_id == gid, Message.created_at >= week_ago))
        .order_by(Message.created_at)
        .limit(500)
    )
    texts = [r[0] for r in msgs_result.all()]

    if not texts:
        return JSONResponse(
            content={
                "group_id": group_id,
                "period": "last_7_days",
                "message_count": 0,
                "summary": "No messages this week.",
                "topics": [],
                "agenda": {
                    "summary": "No activity recorded this week.",
                    "topics": [],
                    "highlights": [],
                    "action_items": [],
                    "engagement_notes": "No messages found.",
                },
            }
        )

    # Run summary and agenda in parallel
    import asyncio
    summary, agenda = await asyncio.gather(
        summarize_messages(texts),
        generate_weekly_agenda(texts, group.name),
    )
    topics = await summarize_topics(texts)

    return JSONResponse(
        content={
            "group_id": group_id,
            "group_name": group.name,
            "period": "last_7_days",
            "message_count": len(texts),
            "summary": summary,
            "topics": topics,
            "agenda": agenda,
        }
    )


@router.get("/{group_id}/custom-agenda")
async def get_custom_agenda(
    group_id: str,
    week_start: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_admin: AdminAccount = Depends(get_current_admin),
) -> JSONResponse:
    """Get the group owner's manually set agenda for a given week."""
    gid = _parse_uuid(group_id, "group_id")
    ensure_group_access(current_admin, gid)
    await _get_group_or_404(db, gid)
    key = f"{group_id}:{week_start or _current_week_start()}"
    agenda = _custom_agendas.get(key)
    return JSONResponse(
        content={
            "group_id": group_id,
            "week_start": week_start or _current_week_start(),
            "agenda": agenda,
        }
    )


@router.put("/{group_id}/custom-agenda")
async def upsert_custom_agenda(
    group_id: str,
    payload: AgendaUpsert,
    db: AsyncSession = Depends(get_db),
    current_admin: AdminAccount = Depends(get_current_admin),
) -> JSONResponse:
    """Create or update the group owner's agenda for a week."""
    gid = _parse_uuid(group_id, "group_id")
    ensure_group_access(current_admin, gid)
    await _get_group_or_404(db, gid)
    key = f"{group_id}:{payload.week_start}"
    _custom_agendas[key] = {
        "week_start": payload.week_start,
        "items": payload.items,
        "notes": payload.notes,
        "updated_at": datetime.utcnow().isoformat(),
    }
    return JSONResponse(content={"status": "saved", "group_id": group_id, **_custom_agendas[key]})


@router.get("/{group_id}/analytics")
async def get_group_analytics(
    group_id: str,
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    current_admin: AdminAccount = Depends(get_current_admin),
) -> JSONResponse:
    """
    Message volume, sentiment trends, most active members, engagement rate.
    """
    gid = _parse_uuid(group_id, "group_id")
    ensure_group_access(current_admin, gid)
    await _get_group_or_404(db, gid)

    since = datetime.utcnow() - timedelta(days=days)

    # Daily message volume
    daily_result = await db.execute(
        select(
            func.date(Message.created_at).label("day"),
            func.count().label("count"),
            func.avg(Message.sentiment_score).label("avg_sent"),
        )
        .where(and_(Message.group_id == gid, Message.created_at >= since))
        .group_by(func.date(Message.created_at))
        .order_by("day")
    )
    daily_data = [
        {
            "date": str(row.day),
            "messages": row.count,
            "avg_sentiment": round(float(row.avg_sent), 3) if row.avg_sent else None,
        }
        for row in daily_result.all()
    ]

    # Top 10 most active members
    top_result = await db.execute(
        select(User.username, User.telegram_user_id, func.count(Message.id).label("cnt"))
        .join(Message, Message.user_id == User.id)
        .where(and_(Message.group_id == gid, Message.created_at >= since))
        .group_by(User.id)
        .order_by(desc("cnt"))
        .limit(10)
    )
    top_members = [
        {
            "username": row.username or str(row.telegram_user_id),
            "message_count": row.cnt,
        }
        for row in top_result.all()
    ]

    return JSONResponse(
        content={
            "group_id": group_id,
            "period_days": days,
            "daily_activity": daily_data,
            "top_members": top_members,
        }
    )


def _current_week_start() -> str:
    today = datetime.utcnow().date()
    monday = today - timedelta(days=today.weekday())
    return str(monday)
