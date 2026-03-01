"""
CORELINK Telegram Message Handlers

Captures and stores group messages for analysis.
Enforces subscription-based access control.
"""

from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message as TelegramMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.database import AsyncSessionLocal
from app.models import Group, User, Message
from app.services import analyze_sentiment
from app.utils.access import check_group_access, get_access_denial_reason

# Create router for message handlers
router = Router()

# Track groups that have been notified about subscription expiry
# (to avoid spamming admins)
_notified_groups = set()


async def get_or_create_user(
    db: AsyncSession,
    telegram_user_id: int,
    username: str | None
) -> User:
    """
    Get existing user or create new one.
    
    Args:
        db: Database session
        telegram_user_id: Telegram's user ID
        username: Telegram username (optional)
        
    Returns:
        User model instance
    """
    # Try to find existing user
    result = await db.execute(
        select(User).where(User.telegram_user_id == telegram_user_id)
    )
    user = result.scalar_one_or_none()
    
    if user:
        # Update username if changed
        if username and user.username != username:
            user.username = username
        
        # Update last_active
        user.last_active = datetime.utcnow()
        await db.commit()
        return user
    
    # Create new user
    user = User(
        telegram_user_id=telegram_user_id,
        username=username,
        first_seen=datetime.utcnow(),
        last_active=datetime.utcnow()
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    return user


async def get_active_group(db: AsyncSession, telegram_group_id: int) -> Group | None:
    """
    Get group from database if it's active.
    
    Args:
        db: Database session
        telegram_group_id: Telegram's group ID
        
    Returns:
        Group instance if active, None otherwise
    """
    result = await db.execute(
        select(Group).where(
            Group.telegram_group_id == telegram_group_id,
            Group.is_active == True
        )
    )
    return result.scalar_one_or_none()


@router.message(
    F.chat.type.in_({"group", "supergroup"}),
    F.text,
    ~F.from_user.is_bot
)
async def handle_group_message(message: TelegramMessage) -> None:
    """
    Handle incoming group messages with subscription enforcement.
    
    Filters:
    - Only group/supergroup messages
    - Only text messages
    - Ignore bot messages
    
    Process:
    1. Check if group is active (admin consent required)
    2. Check if group has active subscription (NEW)
    3. Get or create user
    4. Store message in database
    5. Analyze sentiment (async, non-blocking)
    6. Update sentiment score in database
    7. Update user's last_active timestamp
    
    Subscription Enforcement:
    - If no active subscription, ignore message
    - Send admin notice once per group (avoid spam)
    - Log access denial for monitoring
    """
    # Ignore if no text content
    if not message.text or message.text.strip() == "":
        return
    
    async with AsyncSessionLocal() as db:
        try:
            # Step 1: Check if group is active (existing admin consent check)
            group = await get_active_group(db, message.chat.id)
            
            if not group:
                # Group not tracked or inactive, ignore message
                logger.debug(
                    f"Message ignored: group {message.chat.id} not active "
                    f"(admin consent required)"
                )
                return
            
            # Step 2: Check subscription access (NEW)
            has_subscription_access = await check_group_access(group.id)
            
            if not has_subscription_access:
                # Access denied - log and ignore message
                logger.warning(
                    f"Message ignored: group {group.name} (id={group.id}) "
                    f"has no active subscription"
                )
                
                # Send admin notice once per group (optional)
                if group.id not in _notified_groups:
                    _notified_groups.add(group.id)
                    
                    # Get detailed reason for denial
                    reason = await get_access_denial_reason(group.id)
                    
                    try:
                        await message.answer(
                            f"⚠️ <b>CORELINK Subscription Required</b>\n\n"
                            f"{reason}\n\n"
                            f"🔒 Message tracking and analysis have been paused.\n\n"
                            f"To continue:\n"
                            f"• Renew your subscription\n"
                            f"• Contact an admin to subscribe\n\n"
                            f"Use /subscribe for more information.",
                            parse_mode="HTML"
                        )
                        
                        logger.info(
                            f"Subscription notice sent to group {group.name} "
                            f"(id={group.id})"
                        )
                    except Exception as notify_error:
                        logger.error(
                            f"Failed to send subscription notice to group "
                            f"{group.id}: {str(notify_error)}"
                        )
                
                # Ignore message (do not store or analyze)
                return
            
            # Access granted - proceed with message processing
            
            # Step 3: Get or create user
            user = await get_or_create_user(
                db=db,
                telegram_user_id=message.from_user.id,
                username=message.from_user.username
            )
            
            # Step 4: Store message with initial null sentiment
            new_message = Message(
                group_id=group.id,
                user_id=user.id,
                text=message.text,
                sentiment_score=None,  # Will be updated after analysis
                created_at=datetime.utcnow()
            )
            
            db.add(new_message)
            await db.commit()
            await db.refresh(new_message)
            
            logger.info(
                f"Message stored: group={group.name}, "
                f"user={user.username or user.telegram_user_id}, "
                f"message_id={new_message.id}"
            )
            
            # Step 5: Analyze sentiment (NLP integration)
            try:
                sentiment_score = await analyze_sentiment(message.text)
                
                # Step 6: Update message with sentiment score
                new_message.sentiment_score = sentiment_score
                await db.commit()
                
                logger.success(
                    f"Sentiment analyzed: message_id={new_message.id}, "
                    f"score={sentiment_score:.3f}"
                )
                
            except Exception as sentiment_error:
                # Log sentiment analysis errors but don't fail message storage
                logger.error(
                    f"Sentiment analysis failed for message {new_message.id}: "
                    f"{str(sentiment_error)}"
                )
                # Message is still stored, just without sentiment score
            
        except Exception as e:
            # Log error but don't interrupt bot operation
            logger.error(f"Error handling message: {str(e)}")
            await db.rollback()
