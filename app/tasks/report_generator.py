"""
CORELINK Weekly Report Generator

Background task for generating detailed weekly analytics reports for groups.
"""

from typing import TypedDict
from datetime import datetime, timedelta
from loguru import logger
from sqlalchemy import select, func

from app.database import AsyncSessionLocal
from app.models import Group, Message, User
from app.services import summarize_messages, detect_churn


class WeeklyReportResult(TypedDict):
    """Weekly report result structure."""
    health_score: int
    top_churn_users: list[str]
    summary: str


async def generate_weekly_report(group_id: str) -> WeeklyReportResult:
    """
    Generate weekly analytics report for a group using NLP services.
    
    Analyzes group activity over the past 7 days and generates:
    - Health score (0-100) based on sentiment and activity
    - Top users at risk of churning using churn detection
    - Executive summary using AI-powered summarization
    
    Args:
        group_id: Group UUID string
        
    Returns:
        Dictionary with health_score, top_churn_users, and summary
        
    Raises:
        ValueError: If group not found or inactive
        Exception: For database or processing errors
        
    Usage:
        from app.tasks import enqueue_task
        from app.tasks.report_generator import generate_weekly_report
        
        await enqueue_task(
            generate_weekly_report,
            group_id="uuid-here",
            task_name="Weekly Report Generation"
        )
    """
    start_time = datetime.utcnow()
    logger.info(f"Starting weekly report generation for group: {group_id}")
    
    try:
        async with AsyncSessionLocal() as db:
            # 1. Fetch group and verify it's active
            result = await db.execute(
                select(Group).where(Group.id == group_id)
            )
            group = result.scalar_one_or_none()
            
            if not group:
                raise ValueError(f"Group not found: {group_id}")
            
            if not group.is_active:
                raise ValueError(f"Group is inactive: {group_id}")
            
            # 2. Define date range (last 7 days)
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=7)
            
            logger.info(f"Analyzing period: {start_date.date()} to {end_date.date()}")
            
            # 3. Query messages from last week
            result = await db.execute(
                select(Message)
                .where(Message.group_id == group_id)
                .where(Message.created_at >= start_date)
                .where(Message.created_at <= end_date)
                .order_by(Message.created_at.asc())
            )
            messages = result.scalars().all()
            
            if not messages:
                logger.warning(f"No messages found for group {group_id}")
                return WeeklyReportResult(
                    health_score=50,
                    top_churn_users=[],
                    summary="No activity recorded this week."
                )
            
            # 4. Extract message texts for summarization
            message_texts = [msg.text for msg in messages]
            
            # 5. Generate AI summary using NLP
            logger.info(f"Generating summary for {len(message_texts)} messages")
            summary = await summarize_messages(message_texts)
            
            # 6. Calculate metrics for health score
            total_messages = len(messages)
            unique_users = len(set(msg.user_id for msg in messages))
            
            # Calculate average sentiment (skip None values)
            sentiments = [msg.sentiment_score for msg in messages if msg.sentiment_score is not None]
            avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0
            
            logger.info(
                f"Metrics: messages={total_messages}, "
                f"users={unique_users}, "
                f"avg_sentiment={avg_sentiment:.3f}"
            )
            
            # 7. Prepare user activity data for churn detection
            user_activity = await prepare_user_activity(db, group_id, start_date, end_date)
            
            # 8. Detect churn risks using NLP
            logger.info(f"Analyzing churn risk for {len(user_activity)} users")
            top_churn_users = detect_churn(user_activity)
            
            # 9. Calculate health score (0-100)
            health_score = calculate_group_health_score(
                total_messages=total_messages,
                unique_users=unique_users,
                avg_sentiment=avg_sentiment
            )
            
            logger.success(
                f"Weekly report generated for group {group_id}: "
                f"health_score={health_score}, "
                f"churn_risks={len(top_churn_users)}, "
                f"summary_length={len(summary)}"
            )
            
            return WeeklyReportResult(
                health_score=health_score,
                top_churn_users=top_churn_users,
                summary=summary
            )
            
    except ValueError as e:
        logger.warning(f"Invalid group for report: {str(e)}")
        raise
        
    except Exception as e:
        logger.error(f"Failed to generate weekly report for {group_id}: {str(e)}")
        raise
        
    finally:
        duration = (datetime.utcnow() - start_time).total_seconds()
        logger.info(f"Weekly report generation finished in {duration:.2f}s")


async def identify_churn_risks(
    messages: list,
    threshold: int = 3
) -> list[str]:
    """
    Identify users at risk of churning based on activity patterns.
    
    Args:
        messages: List of message objects
        threshold: Number of top risk users to return
        
    Returns:
        List of user IDs at risk of churning
        
    TODO Implementation:
    - Group messages by user
    - Calculate activity trend for each user
    - Identify users with declining activity
    - Check sentiment patterns (negative = higher risk)
    - Score each user for churn risk
    - Return top N users by risk score
    """
    # Placeholder
    return []


async def prepare_user_activity(
    db,
    group_id: str,
    start_date: datetime,
    end_date: datetime
) -> list[dict]:
    """
    Prepare user activity data for churn detection.
    
    Args:
        db: Database session
        group_id: Group UUID
        start_date: Start of analysis period
        end_date: End of analysis period
        
    Returns:
        List of user activity dictionaries
    """
    # Get all messages in period
    result = await db.execute(
        select(Message)
        .where(Message.group_id == group_id)
        .where(Message.created_at >= start_date)
        .where(Message.created_at <= end_date)
    )
    messages = result.scalars().all()
    
    # Get unique user IDs
    user_ids = set(msg.user_id for msg in messages)
    
    # Fetch user details
    result = await db.execute(
        select(User).where(User.id.in_(user_ids))
    )
    users = result.scalars().all()
    
    # Calculate activity metrics for each user
    user_activity = []
    period_days = (end_date - start_date).days or 1
    
    for user in users:
        # Get user's messages
        user_messages = [msg for msg in messages if msg.user_id == user.id]
        
        # Calculate sentiment trend (average of user's messages)
        user_sentiments = [
            msg.sentiment_score 
            for msg in user_messages 
            if msg.sentiment_score is not None
        ]
        sentiment_trend = (
            sum(user_sentiments) / len(user_sentiments) 
            if user_sentiments else 0.0
        )
        
        # Calculate message frequency (messages per day)
        message_frequency = len(user_messages) / period_days
        
        user_activity.append({
            "user_id": str(user.id),
            "username": user.username,
            "telegram_user_id": user.telegram_user_id,
            "last_active": user.last_active,
            "sentiment_trend": sentiment_trend,
            "message_frequency": message_frequency
        })
    
    return user_activity


def calculate_group_health_score(
    total_messages: int,
    unique_users: int,
    avg_sentiment: float
) -> int:
    """
    Calculate overall group health score (0-100).
    
    Health score algorithm:
    - Message volume (30%): More messages = healthier group
    - User participation (30%): More active users = healthier
    - Sentiment (40%): Positive sentiment = healthier
    
    Args:
        total_messages: Total messages in period (7 days)
        unique_users: Number of active users
        avg_sentiment: Average sentiment score (-1.0 to 1.0)
        
    Returns:
        Health score between 0 and 100
    """
    # Base score starts at 0
    score = 0
    
    # Factor 1: Message volume (30 points max)
    # Scoring: 100+ msgs = 30pts, 50+ msgs = 20pts, 25+ msgs = 10pts
    if total_messages >= 100:
        score += 30
    elif total_messages >= 50:
        score += 20
    elif total_messages >= 25:
        score += 10
    elif total_messages >= 10:
        score += 5
    
    # Factor 2: User participation (30 points max)
    # Scoring: 20+ users = 30pts, 10+ users = 20pts, 5+ users = 10pts
    if unique_users >= 20:
        score += 30
    elif unique_users >= 10:
        score += 20
    elif unique_users >= 5:
        score += 10
    elif unique_users >= 2:
        score += 5
    
    # Factor 3: Sentiment (40 points max)
    # Normalize sentiment from [-1, 1] to [0, 40]
    # Formula: (sentiment + 1) / 2 * 40
    sentiment_score = int(((avg_sentiment + 1.0) / 2.0) * 40)
    score += max(0, min(40, sentiment_score))
    
    # Clamp final score to 0-100
    final_score = max(0, min(100, score))
    
    logger.debug(
        f"Health score calculated: {final_score} "
        f"(messages={total_messages}, users={unique_users}, sentiment={avg_sentiment:.2f})"
    )
    
    return final_score


def format_churn_risk_message(user_username: str, risk_score: float) -> str:
    """
    Format a churn risk warning message for a user.
    
    Args:
        user_username: Username to mention
        risk_score: Risk score (0.0 to 1.0)
        
    Returns:
        Formatted warning message
        
    TODO:
    - Create actionable message
    - Include re-engagement suggestions
    - Format for Telegram (HTML)
    """
    risk_level = "High" if risk_score > 0.7 else "Medium" if risk_score > 0.4 else "Low"
    
    return (
        f"⚠️ <b>{user_username}</b> - {risk_level} churn risk\n"
        f"Risk score: {risk_score:.2f}\n"
        f"Suggestion: Engage with recent content"
    )
