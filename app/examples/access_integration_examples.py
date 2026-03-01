"""
CORELINK Access Control Integration Examples

This file demonstrates how to integrate subscription access checks
into Telegram bot handlers and FastAPI routes.

DO NOT import this file - it's for reference only.
"""

# =============================================================================
# EXAMPLE 1: Telegram Bot Message Handler with Access Check
# =============================================================================

from aiogram import Router, F
from aiogram.types import Message
from uuid import UUID

from app.utils.access import check_group_access, get_access_denial_reason

router = Router()


@router.message(F.text.startswith("/premium_feature"))
async def handle_premium_feature(message: Message):
    """
    Example: Telegram bot command that requires active subscription.
    """
    # Get group_id from message
    if not message.chat or message.chat.type not in ["group", "supergroup"]:
        await message.reply("❌ This command only works in groups")
        return
    
    telegram_group_id = message.chat.id
    
    # TODO: Convert telegram_group_id to UUID (fetch from database)
    # For this example, assume we have the UUID
    group_id = UUID("your-group-uuid-here")
    
    # Check subscription access
    if not await check_group_access(group_id):
        # Get detailed reason
        reason = await get_access_denial_reason(group_id)
        await message.reply(
            f"⚠️ <b>Subscription Required</b>\n\n"
            f"{reason}\n\n"
            f"Use /subscribe to get started!",
            parse_mode="HTML"
        )
        return
    
    # Access granted - process premium feature
    await message.reply("✅ Premium feature accessed successfully!")


# =============================================================================
# EXAMPLE 2: Using require_active_subscription for Better Logging
# =============================================================================

from app.utils.access import require_active_subscription


@router.message(F.text == "/weekly_report")
async def handle_weekly_report(message: Message):
    """
    Example: Generate weekly report (premium feature).
    """
    if not message.chat or message.chat.type not in ["group", "supergroup"]:
        return
    
    telegram_group_id = message.chat.id
    group_id = UUID("your-group-uuid-here")
    
    # Check access with feature-specific logging
    if not await require_active_subscription(group_id, "weekly reports"):
        await message.reply(
            "📊 <b>Weekly Reports</b>\n\n"
            "This premium feature requires an active subscription.\n"
            "Subscribe now to unlock:\n"
            "• Weekly health scores\n"
            "• Churn detection\n"
            "• Activity summaries\n\n"
            "Use /subscribe to get started!",
            parse_mode="HTML"
        )
        return
    
    # Generate report
    await message.reply("📊 Generating weekly report...")


# =============================================================================
# EXAMPLE 3: FastAPI Route with Access Check
# =============================================================================

from fastapi import APIRouter, HTTPException, status, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.utils.access import check_group_access_with_db

api_router = APIRouter()


@api_router.get("/groups/{group_id}/analytics")
async def get_group_analytics(
    group_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """
    Example: API endpoint that requires subscription.
    """
    # Check subscription access (with existing DB session)
    if not await check_group_access_with_db(group_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active subscription required to access analytics"
        )
    
    # Access granted - return analytics
    return {
        "group_id": str(group_id),
        "analytics": {
            "active_users": 150,
            "messages_today": 450,
            "sentiment_score": 0.75
        }
    }


# =============================================================================
# EXAMPLE 4: FastAPI Route with Dependency Injection
# =============================================================================

from app.utils.access import require_subscription_dependency


@api_router.get("/groups/{group_id}/premium-data")
async def get_premium_data(
    group_id: UUID,
    has_access: bool = Depends(require_subscription_dependency)
):
    """
    Example: Using FastAPI dependency for automatic access check.
    
    If subscription is not active, HTTPException is raised automatically.
    This code only runs if subscription is valid.
    """
    return {
        "group_id": str(group_id),
        "premium_data": {
            "churn_risk_users": ["user1", "user2"],
            "health_score": 85
        }
    }


# =============================================================================
# EXAMPLE 5: Check Subscription Status for Custom Logic
# =============================================================================

from app.utils.access import get_subscription_status


@router.message(F.text == "/subscription_info")
async def show_subscription_info(message: Message):
    """
    Example: Show subscription status to user.
    """
    if not message.chat or message.chat.type not in ["group", "supergroup"]:
        return
    
    telegram_group_id = message.chat.id
    group_id = UUID("your-group-uuid-here")
    
    # Get detailed status
    status = await get_subscription_status(group_id)
    
    if not status or not status["has_subscription"]:
        await message.reply(
            "📋 <b>Subscription Status</b>\n\n"
            "❌ No active subscription\n\n"
            "Use /subscribe to unlock premium features!",
            parse_mode="HTML"
        )
        return
    
    if status["is_active"]:
        await message.reply(
            f"📋 <b>Subscription Status</b>\n\n"
            f"✅ Active ({status['provider']})\n"
            f"📅 Expires: {status['expires_at'][:10] if status['expires_at'] else 'Never'}\n"
            f"⏱️ Days remaining: {status['days_remaining']}\n",
            parse_mode="HTML"
        )
    else:
        await message.reply(
            f"📋 <b>Subscription Status</b>\n\n"
            f"⚠️ Status: {status['status']}\n"
            f"Provider: {status['provider']}\n\n"
            f"Please renew your subscription to continue.",
            parse_mode="HTML"
        )


# =============================================================================
# EXAMPLE 6: Background Task to Send Renewal Reminders
# =============================================================================

from app.utils.access import get_groups_with_expiring_subscriptions
from app.bots import bot


async def send_renewal_reminders():
    """
    Example: Background task to remind groups about expiring subscriptions.
    
    Schedule this to run daily.
    """
    # Get groups expiring in next 3 days
    expiring_groups = await get_groups_with_expiring_subscriptions(days_threshold=3)
    
    for group_id in expiring_groups:
        # Get group's telegram_group_id from database
        # TODO: Implement database lookup
        telegram_group_id = -1001234567890  # Example
        
        # Get subscription details
        status = await get_subscription_status(group_id)
        
        if status and status["is_active"]:
            try:
                await bot.send_message(
                    chat_id=telegram_group_id,
                    text=(
                        f"⏰ <b>Subscription Reminder</b>\n\n"
                        f"Your subscription expires in <b>{status['days_remaining']} days</b>.\n\n"
                        f"Renew now to continue enjoying premium features!"
                    ),
                    parse_mode="HTML"
                )
            except Exception as e:
                print(f"Failed to send reminder to group {group_id}: {e}")


# =============================================================================
# EXAMPLE 7: Admin Analytics Endpoint
# =============================================================================

from app.utils.access import count_active_subscriptions_by_provider


@api_router.get("/admin/subscription-stats")
async def get_subscription_stats():
    """
    Example: Admin endpoint to view subscription statistics.
    """
    # Count subscriptions by provider
    counts = await count_active_subscriptions_by_provider()
    
    total = sum(counts.values())
    
    return {
        "total_active_subscriptions": total,
        "by_provider": counts,
        "providers": list(counts.keys())
    }


# =============================================================================
# EXAMPLE 8: Middleware-Style Access Check
# =============================================================================

async def check_subscription_middleware(group_id: UUID, feature_name: str) -> None:
    """
    Example: Reusable access check function that raises exception.
    
    Use this in multiple handlers for consistent behavior.
    """
    if not await require_active_subscription(group_id, feature_name):
        reason = await get_access_denial_reason(group_id)
        raise ValueError(f"Subscription required: {reason}")


@router.message(F.text == "/sentiment_analysis")
async def handle_sentiment_analysis(message: Message):
    """
    Example: Using middleware-style access check.
    """
    if not message.chat:
        return
    
    group_id = UUID("your-group-uuid-here")
    
    try:
        # Check subscription
        await check_subscription_middleware(group_id, "sentiment analysis")
        
        # Process feature
        await message.reply("📊 Analyzing sentiment...")
        
    except ValueError as e:
        await message.reply(f"⚠️ {str(e)}")


# =============================================================================
# EXAMPLE 9: Graceful Feature Degradation
# =============================================================================

@router.message(F.text == "/summary")
async def handle_summary(message: Message):
    """
    Example: Provide limited features for free users, full features for subscribers.
    """
    if not message.chat:
        return
    
    group_id = UUID("your-group-uuid-here")
    has_access = await check_group_access(group_id)
    
    if has_access:
        # Premium: Full summary with AI
        await message.reply(
            "📝 <b>AI-Powered Summary (Premium)</b>\n\n"
            "• Last 7 days of activity\n"
            "• Sentiment analysis\n"
            "• Top contributors\n"
            "• Engagement metrics",
            parse_mode="HTML"
        )
    else:
        # Free: Basic summary
        await message.reply(
            "📝 <b>Basic Summary</b>\n\n"
            "• Last 24 hours only\n"
            "• Message count: 50\n\n"
            "⭐ Upgrade to Premium for:\n"
            "• AI-powered insights\n"
            "• 7-day history\n"
            "• Sentiment analysis\n\n"
            "Use /subscribe to upgrade!",
            parse_mode="HTML"
        )
