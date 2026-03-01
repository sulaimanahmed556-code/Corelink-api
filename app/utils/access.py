"""
CORELINK Group Access Control

Helper module for checking subscription-based access to group features.
"""

from typing import Optional
from uuid import UUID
from datetime import datetime
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import Subscription, Group
from app.models.subscription import SubscriptionStatus


async def check_group_access(group_id: UUID) -> bool:
    """
    Check if a group has active subscription access.
    
    Verifies that the group has an active, non-expired subscription
    before allowing access to premium features.
    
    Args:
        group_id: Group UUID to check access for
        
    Returns:
        True if subscription is active and valid, False otherwise
        
    Usage:
        from app.utils.access import check_group_access
        
        if await check_group_access(group_id):
            # Process premium feature
            pass
        else:
            await message.reply("⚠️ Subscription required")
    """
    try:
        async with AsyncSessionLocal() as db:
            # Fetch subscription
            result = await db.execute(
                select(Subscription).where(Subscription.group_id == group_id)
            )
            subscription = result.scalar_one_or_none()
            
            # No subscription found
            if not subscription:
                logger.warning(f"Access denied: No subscription for group {group_id}")
                return False
            
            # Check subscription status
            if subscription.status != SubscriptionStatus.ACTIVE:
                logger.warning(
                    f"Access denied: Subscription status={subscription.status.value} "
                    f"for group {group_id}"
                )
                return False
            
            # Check if subscription has expired
            if subscription.current_period_end:
                if subscription.current_period_end < datetime.utcnow():
                    logger.warning(
                        f"Access denied: Subscription expired on "
                        f"{subscription.current_period_end} for group {group_id}"
                    )
                    return False
            
            # Access granted
            logger.debug(
                f"Access granted: group {group_id}, "
                f"provider={subscription.provider.value}"
            )
            return True
            
    except Exception as e:
        logger.error(f"Error checking group access for {group_id}: {str(e)}")
        # Fail closed - deny access on error
        return False


async def check_group_access_with_db(group_id: UUID, db: AsyncSession) -> bool:
    """
    Check if a group has active subscription access (with existing DB session).
    
    Use this version when you already have a database session available
    to avoid creating a new connection.
    
    Args:
        group_id: Group UUID to check access for
        db: Existing database session
        
    Returns:
        True if subscription is active and valid, False otherwise
        
    Usage:
        from fastapi import Depends
        from app.database import get_db
        from app.utils.access import check_group_access_with_db
        
        @router.get("/premium-feature/{group_id}")
        async def premium_feature(
            group_id: UUID,
            db = Depends(get_db)
        ):
            if not await check_group_access_with_db(group_id, db):
                raise HTTPException(status_code=403, detail="Subscription required")
            
            # Process premium feature
    """
    try:
        # Fetch subscription
        result = await db.execute(
            select(Subscription).where(Subscription.group_id == group_id)
        )
        subscription = result.scalar_one_or_none()
        
        # No subscription found
        if not subscription:
            logger.warning(f"Access denied: No subscription for group {group_id}")
            return False
        
        # Check subscription status
        if subscription.status != SubscriptionStatus.ACTIVE:
            logger.warning(
                f"Access denied: Subscription status={subscription.status.value} "
                f"for group {group_id}"
            )
            return False
        
        # Check if subscription has expired
        if subscription.current_period_end:
            if subscription.current_period_end < datetime.utcnow():
                logger.warning(
                    f"Access denied: Subscription expired on "
                    f"{subscription.current_period_end} for group {group_id}"
                )
                return False
        
        # Access granted
        logger.debug(
            f"Access granted: group {group_id}, "
            f"provider={subscription.provider.value}"
        )
        return True
        
    except Exception as e:
        logger.error(f"Error checking group access for {group_id}: {str(e)}")
        # Fail closed - deny access on error
        return False


async def get_subscription_status(group_id: UUID) -> Optional[dict]:
    """
    Get detailed subscription status for a group.
    
    Returns subscription details including provider, status, and expiry.
    
    Args:
        group_id: Group UUID
        
    Returns:
        Dictionary with subscription details or None if no subscription exists:
        {
            "has_subscription": bool,
            "is_active": bool,
            "provider": str,
            "status": str,
            "expires_at": str,
            "days_remaining": int
        }
        
    Usage:
        from app.utils.access import get_subscription_status
        
        status = await get_subscription_status(group_id)
        if status and status["is_active"]:
            print(f"Days remaining: {status['days_remaining']}")
    """
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Subscription).where(Subscription.group_id == group_id)
            )
            subscription = result.scalar_one_or_none()
            
            if not subscription:
                return {
                    "has_subscription": False,
                    "is_active": False,
                    "provider": None,
                    "status": None,
                    "expires_at": None,
                    "days_remaining": 0
                }
            
            # Calculate days remaining
            days_remaining = 0
            if subscription.current_period_end:
                delta = subscription.current_period_end - datetime.utcnow()
                days_remaining = max(0, delta.days)
            
            is_active = (
                subscription.status == SubscriptionStatus.ACTIVE and
                (not subscription.current_period_end or 
                 subscription.current_period_end > datetime.utcnow())
            )
            
            return {
                "has_subscription": True,
                "is_active": is_active,
                "provider": subscription.provider.value,
                "status": subscription.status.value,
                "expires_at": subscription.current_period_end.isoformat() if subscription.current_period_end else None,
                "days_remaining": days_remaining
            }
            
    except Exception as e:
        logger.error(f"Error getting subscription status for {group_id}: {str(e)}")
        return None


async def require_active_subscription(group_id: UUID, feature_name: str = "this feature") -> bool:
    """
    Check group access and log detailed access denial.
    
    Enhanced version with feature-specific logging for monitoring
    and analytics.
    
    Args:
        group_id: Group UUID
        feature_name: Name of feature being accessed (for logging)
        
    Returns:
        True if access granted, False if denied
        
    Usage:
        from app.utils.access import require_active_subscription
        
        if not await require_active_subscription(group_id, "weekly reports"):
            await message.reply("⚠️ Weekly reports require an active subscription")
            return
        
        # Process weekly report
    """
    has_access = await check_group_access(group_id)
    
    if not has_access:
        logger.info(
            f"Feature access denied: group={group_id}, "
            f"feature='{feature_name}', reason='no_active_subscription'"
        )
    else:
        logger.debug(
            f"Feature access granted: group={group_id}, feature='{feature_name}'"
        )
    
    return has_access


async def get_access_denial_reason(group_id: UUID) -> str:
    """
    Get human-readable reason why access was denied.
    
    Provides detailed explanation for why subscription access failed,
    useful for user-facing error messages.
    
    Args:
        group_id: Group UUID
        
    Returns:
        String explaining why access was denied
        
    Usage:
        from app.utils.access import check_group_access, get_access_denial_reason
        
        if not await check_group_access(group_id):
            reason = await get_access_denial_reason(group_id)
            await message.reply(f"❌ Access denied: {reason}")
    """
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Subscription).where(Subscription.group_id == group_id)
            )
            subscription = result.scalar_one_or_none()
            
            if not subscription:
                return "No subscription found for this group. Please subscribe to access this feature."
            
            if subscription.status == SubscriptionStatus.CANCELED:
                return "Your subscription has been canceled. Please resubscribe to continue."
            
            if subscription.status == SubscriptionStatus.INACTIVE:
                return "Your subscription is inactive. Please check your payment method."
            
            if subscription.current_period_end and subscription.current_period_end < datetime.utcnow():
                return f"Your subscription expired on {subscription.current_period_end.strftime('%Y-%m-%d')}. Please renew to continue."
            
            return "Subscription status unknown. Please contact support."
            
    except Exception as e:
        logger.error(f"Error getting access denial reason for {group_id}: {str(e)}")
        return "Unable to verify subscription status. Please try again later."


async def get_groups_with_expiring_subscriptions(days_threshold: int = 7) -> list[UUID]:
    """
    Get list of groups with subscriptions expiring soon.
    
    Useful for sending renewal reminders to group admins.
    
    Args:
        days_threshold: Number of days before expiry to include (default: 7)
        
    Returns:
        List of group UUIDs with expiring subscriptions
        
    Usage:
        from app.utils.access import get_groups_with_expiring_subscriptions
        
        # Get groups expiring in next 3 days
        expiring_groups = await get_groups_with_expiring_subscriptions(days_threshold=3)
        
        for group_id in expiring_groups:
            # Send renewal reminder
            pass
    """
    try:
        async with AsyncSessionLocal() as db:
            # Calculate threshold date
            threshold_date = datetime.utcnow()
            from datetime import timedelta
            threshold_date += timedelta(days=days_threshold)
            
            # Query subscriptions expiring soon
            result = await db.execute(
                select(Subscription).where(
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.current_period_end.isnot(None),
                    Subscription.current_period_end <= threshold_date,
                    Subscription.current_period_end > datetime.utcnow()
                )
            )
            subscriptions = result.scalars().all()
            
            group_ids = [sub.group_id for sub in subscriptions]
            
            logger.info(
                f"Found {len(group_ids)} groups with subscriptions "
                f"expiring in next {days_threshold} days"
            )
            
            return group_ids
            
    except Exception as e:
        logger.error(f"Error getting expiring subscriptions: {str(e)}")
        return []


async def count_active_subscriptions_by_provider() -> dict[str, int]:
    """
    Count active subscriptions by payment provider.
    
    Useful for analytics and monitoring.
    
    Returns:
        Dictionary with provider names as keys and counts as values
        
    Usage:
        from app.utils.access import count_active_subscriptions_by_provider
        
        counts = await count_active_subscriptions_by_provider()
        print(f"Stripe: {counts.get('STRIPE', 0)}")
        print(f"Paystack: {counts.get('PAYSTACK', 0)}")
        print(f"PayPal: {counts.get('PAYPAL', 0)}")
    """
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Subscription).where(
                    Subscription.status == SubscriptionStatus.ACTIVE
                )
            )
            subscriptions = result.scalars().all()
            
            counts = {}
            for sub in subscriptions:
                provider = sub.provider.value
                counts[provider] = counts.get(provider, 0) + 1
            
            logger.debug(f"Active subscription counts: {counts}")
            
            return counts
            
    except Exception as e:
        logger.error(f"Error counting active subscriptions: {str(e)}")
        return {}


# =============================================================================
# FASTAPI DEPENDENCY HELPERS
# =============================================================================

async def require_subscription_dependency(group_id: UUID) -> bool:
    """
    FastAPI dependency for requiring active subscription.
    
    Raises HTTPException if subscription is not active.
    
    Usage:
        from fastapi import Depends
        from app.utils.access import require_subscription_dependency
        
        @router.get("/premium/{group_id}")
        async def premium_feature(
            group_id: UUID,
            has_access: bool = Depends(require_subscription_dependency)
        ):
            # This code only runs if subscription is active
            return {"message": "Premium feature accessed"}
    """
    from fastapi import HTTPException, status as http_status
    
    has_access = await check_group_access(group_id)
    
    if not has_access:
        reason = await get_access_denial_reason(group_id)
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail=f"Subscription required: {reason}"
        )
    
    return True
