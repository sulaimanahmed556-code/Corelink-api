"""
CORELINK Business Logic Services

Exports service modules for sentiment analysis, summarization, and more.
"""

from app.services.sentiment import (
    analyze_sentiment,
    get_vader_sentiment,
    categorize_sentiment,
    get_sentiment_emoji,
    get_sentiment_description,
    batch_analyze_sentiment,
)
from app.services.summarization import (
    summarize_messages,
    summarize_topics,
    generate_key_insights,
)
from app.services.churn import (
    detect_churn,
    get_churn_risk_level,
    get_detailed_churn_analysis,
    format_churn_report,
)

__all__ = [
    "analyze_sentiment",
    "get_vader_sentiment",
    "categorize_sentiment",
    "get_sentiment_emoji",
    "get_sentiment_description",
    "batch_analyze_sentiment",
    "summarize_messages",
    "summarize_topics",
    "generate_key_insights",
    "batch_summarize",
    "detect_churn",
    "get_churn_risk_level",
    "get_churn_emoji",
    "get_detailed_churn_analysis",
    "format_churn_report",
]
