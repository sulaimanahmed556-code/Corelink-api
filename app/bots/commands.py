"""
CORELINK Telegram Bot Commands

Handlers for bot commands: /start, /corelink_enable, /corelink_disable
"""

from datetime import datetime
from urllib.parse import urlencode

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, ChatMemberAdministrator, ChatMemberOwner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.logger import logger
from app.bots.telegram_bot import bot
from app.database import AsyncSessionLocal
from app.models import Group

# Create router for commands
router = Router()


def generate_payment_link(group_id: str) -> str:
    """
    Build payment checkout URL for a group.
    """
    base_url = settings.PAYMENT_CLIENT_BASE_URL.rstrip("/")
    checkout_path = settings.PAYMENT_CLIENT_CHECKOUT_PATH.strip()
    if not checkout_path.startswith("/"):
        checkout_path = f"/{checkout_path}"

    query = urlencode({"group_id": str(group_id)})
    return f"{base_url}{checkout_path}?{query}"


async def is_group_admin(chat_id: int, user_id: int) -> bool:
    """
    Check if user is an administrator or owner of the chat.
    
    Args:
        chat_id: Telegram chat ID
        user_id: Telegram user ID
        
    Returns:
        True if user is admin/owner, False otherwise
    """
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return isinstance(member, (ChatMemberAdministrator, ChatMemberOwner))
    except Exception:
        return False


async def get_or_create_group(db: AsyncSession, telegram_group_id: int, name: str) -> Group:
    """
    Get existing group or create new one.
    
    Args:
        db: Database session
        telegram_group_id: Telegram's group ID
        name: Group name
        
    Returns:
        Group model instance
    """
    # Try to find existing group
    result = await db.execute(
        select(Group).where(Group.telegram_group_id == telegram_group_id)
    )
    group = result.scalar_one_or_none()
    
    if group:
        # Update name if changed
        if group.name != name:
            group.name = name
            await db.commit()
        return group
    
    # Create new group
    group = Group(
        telegram_group_id=telegram_group_id,
        name=name,
        is_active=False
    )
    db.add(group)
    await db.commit()
    await db.refresh(group)
    
    return group


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """
    Handle /start command - explain CORELINK.
    
    Works in both private chats and groups.
    """
    welcome_text = (
        "🔗 <b>Welcome to CORELINK!</b>\n\n"
        "CORELINK is an intelligent Telegram group analytics platform that helps you:\n\n"
        "📊 <b>Track Engagement</b> - Monitor message activity and member participation\n"
        "💬 <b>Sentiment Analysis</b> - Understand group mood with AI-powered sentiment tracking\n"
        "📈 <b>Weekly Reports</b> - Get automated insights delivered every week\n"
        "⚠️ <b>Churn Detection</b> - Identify at-risk members before they leave\n\n"
        "<b>For Group Admins:</b>\n"
        "• /corelink_enable - Enable tracking for this group\n"
        "• /corelink_disable - Disable tracking\n\n"
        "Start building stronger communities with data-driven insights! 🚀"
    )
    
    await message.reply(welcome_text)


@router.message(Command("corelink_enable"))
async def cmd_enable(message: Message) -> None:
    """
    Handle /corelink_enable command - enable group tracking.
    
    Only works in groups and requires admin privileges.
    Records admin consent and activates group monitoring.
    """
    # Check if command is in a group
    if message.chat.type not in ["group", "supergroup"]:
        await message.reply(
            "❌ This command only works in groups.\n"
            "Add me to a group and try again!"
        )
        return
    
    # Verify admin status
    if not await is_group_admin(message.chat.id, message.from_user.id):
        await message.reply(
            "⚠️ Only group administrators can enable CORELINK tracking.\n"
            "Please ask a group admin to run this command."
        )
        return
    
    # Database operations
    async with AsyncSessionLocal() as db:
        try:
            # Get or create group
            group = await get_or_create_group(
                db=db,
                telegram_group_id=message.chat.id,
                name=message.chat.title or "Unknown Group"
            )
            
            # Generate payment link if payment has not been made
            if not group.has_made_payment:
                paymentLink = generate_payment_link(group.id)
                
                await  message.reply(f"Your CoreLink account is now created use the link below to proccess payment to activate corelink for your group.\n<a href='{paymentLink}' >{paymentLink}</a>")
                return
            
            # Check if already enabled
            if group.is_active:
                await message.reply(
                    "✅ CORELINK is already enabled for this group!\n\n"
                    "I'm actively tracking:\n"
                    "• Message sentiment\n"
                    "• Member engagement\n"
                    "• Weekly analytics\n\n"
                    "Use /corelink_disable to stop tracking."
                )
                return
            
            # Enable tracking
            group.is_active = True
            group.admin_consented_at = datetime.utcnow()
            await db.commit()
            
            await message.reply(
                "🎉 <b>CORELINK Enabled Successfully!</b>\n\n"
                "✅ Group tracking is now <b>ACTIVE</b>\n\n"
                "<b>What happens next:</b>\n"
                "• I'll analyze sentiment in messages\n"
                "• Track member engagement patterns\n"
                "• Generate weekly insight reports\n"
                "• Alert on potential churn risks\n\n"
                "📊 Your first report will arrive next Monday at 9 AM.\n\n"
                "<i>Note: I only analyze messages sent after activation.</i>"
            )
            
        except Exception as e:
            logger.error(f"Error enabling group: {e}")
            await message.reply(
                "❌ <b>Error</b>\n\n"
                "Failed to enable tracking. Please try again later."
            )


@router.message(Command("corelink_disable"))
async def cmd_disable(message: Message) -> None:
    """
    Handle /corelink_disable command - disable group tracking.
    
    Only works in groups and requires admin privileges.
    Deactivates monitoring but preserves historical data.
    """
    # Check if command is in a group
    if message.chat.type not in ["group", "supergroup"]:
        await message.reply(
            "❌ This command only works in groups.\n"
            "Add me to a group and try again!"
        )
        return
    
    # Verify admin status
    if not await is_group_admin(message.chat.id, message.from_user.id):
        await message.reply(
            "⚠️ Only group administrators can disable CORELINK tracking.\n"
            "Please ask a group admin to run this command."
        )
        return
    
    # Database operations
    async with AsyncSessionLocal() as db:
        try:
            # Find group
            result = await db.execute(
                select(Group).where(Group.telegram_group_id == message.chat.id)
            )
            group = result.scalar_one_or_none()
            
            if not group:
                await message.reply(
                    "ℹ️ This group is not registered with CORELINK.\n"
                    "Use /corelink_enable to start tracking."
                )
                return
            
            # Check if already disabled
            if not group.is_active:
                await message.reply(
                    "ℹ️ CORELINK tracking is already disabled for this group.\n"
                    "Use /corelink_enable to resume tracking."
                )
                return
            
            # Disable tracking
            group.is_active = False
            await db.commit()
            
            await message.reply(
                "🔕 <b>CORELINK Disabled</b>\n\n"
                "✅ Group tracking has been <b>DEACTIVATED</b>\n\n"
                "<b>What this means:</b>\n"
                "• No new messages will be analyzed\n"
                "• Weekly reports are paused\n"
                "• Historical data is preserved\n\n"
                "You can re-enable tracking anytime with /corelink_enable"
            )
            
        except Exception as e:
            logger.error(f"Error disabling group: {e}")
            await message.reply(
                "❌ <b>Error</b>\n\n"
                "Failed to disable tracking. Please try again later."
            )
