"""
CORELINK Sentiment Analysis Service

Hybrid sentiment analysis combining VADER (fast) and OpenAI (contextual).
"""

import httpx
from typing import Literal
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from loguru import logger

from app.config import settings


# Initialize VADER analyzer (cached globally)
_vader_analyzer = SentimentIntensityAnalyzer()


async def analyze_sentiment(text: str) -> float:
    """
    Analyze sentiment of text using hybrid approach.
    
    Combines VADER (rule-based, fast) with OpenAI (contextual, accurate)
    for balanced sentiment scoring.
    
    Args:
        text: Text to analyze
        
    Returns:
        Sentiment score from -1.0 (negative) to 1.0 (positive)
        
    Raises:
        ValueError: If text is empty
        
    Usage:
        score = await analyze_sentiment("I love this group!")
        # score = 0.85 (positive)
        
        score = await analyze_sentiment("This is terrible")
        # score = -0.72 (negative)
        
        # From message handler
        from app.services.sentiment import analyze_sentiment
        
        @router.message()
        async def handle_message(message: Message):
            score = await analyze_sentiment(message.text)
            # Store in database
            await save_sentiment_score(message.message_id, score)
    """
    if not text or not text.strip():
        raise ValueError("Text cannot be empty")
    
    try:
        # Get VADER score (fast, local)
        vader_score = get_vader_sentiment(text)
        logger.debug(f"VADER score: {vader_score:.3f}")
        
        # Get OpenAI score (slower, contextual)
        openai_score = await get_openai_sentiment(text)
        logger.debug(f"OpenAI score: {openai_score:.3f}")
        
        # Combine scores: 60% VADER + 40% OpenAI
        final_score = (vader_score * 0.6) + (openai_score * 0.4)
        
        # Clamp to [-1.0, 1.0]
        final_score = max(-1.0, min(1.0, final_score))
        
        logger.info(
            f"Sentiment analyzed: {final_score:.3f} "
            f"(VADER: {vader_score:.3f}, OpenAI: {openai_score:.3f})"
        )
        
        return final_score
        
    except Exception as e:
        logger.error(f"Sentiment analysis failed: {str(e)}")
        # Fallback to VADER only
        return get_vader_sentiment(text)


def get_vader_sentiment(text: str) -> float:
    """
    Get sentiment score using VADER (Valence Aware Dictionary and sEntiment Reasoner).
    
    VADER is optimized for social media text and handles:
    - Punctuation (!!!, ???)
    - Capitalization (LOVE vs love)
    - Emojis
    - Slang
    
    Args:
        text: Text to analyze
        
    Returns:
        Compound sentiment score from -1.0 to 1.0
        
    Example:
        score = get_vader_sentiment("Great! 😊")
        # score ~ 0.6 (positive)
    """
    try:
        scores = _vader_analyzer.polarity_scores(text)
        # Use compound score (normalized sum of all lexicon ratings)
        return scores['compound']
        
    except Exception as e:
        logger.warning(f"VADER analysis failed: {str(e)}")
        return 0.0  # Neutral fallback


async def get_openai_sentiment(text: str) -> float:
    """
    Get sentiment score using OpenAI API for contextual understanding.
    
    Uses GPT-4 to understand nuanced sentiment including:
    - Sarcasm
    - Context-dependent meaning
    - Cultural references
    - Complex emotions
    
    Args:
        text: Text to analyze
        
    Returns:
        Sentiment score from -1.0 to 1.0
        
    Note:
        Falls back to neutral (0.0) if API fails or is unavailable.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": settings.OPENAI_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a sentiment analysis expert. "
                                "Analyze the sentiment of the given text and respond "
                                "with ONLY a single number between -1.0 and 1.0, where: "
                                "-1.0 = extremely negative, "
                                "0.0 = neutral, "
                                "1.0 = extremely positive. "
                                "Consider context, sarcasm, and nuance."
                            )
                        },
                        {
                            "role": "user",
                            "content": text[:500]  # Limit to 500 chars
                        }
                    ],
                    "temperature": 0.3,  # Low temperature for consistent scoring
                    "max_tokens": 10
                }
            )
            
            if response.status_code != 200:
                logger.warning(f"OpenAI API error: {response.status_code}")
                return 0.0
            
            data = response.json()
            score_text = data['choices'][0]['message']['content'].strip()
            
            # Parse score
            score = float(score_text)
            
            # Clamp to valid range
            return max(-1.0, min(1.0, score))
            
    except httpx.TimeoutException:
        logger.warning("OpenAI API timeout, using fallback")
        return 0.0
        
    except (ValueError, KeyError, IndexError) as e:
        logger.warning(f"Failed to parse OpenAI response: {str(e)}")
        return 0.0
        
    except Exception as e:
        logger.error(f"OpenAI sentiment analysis error: {str(e)}")
        return 0.0


def categorize_sentiment(score: float) -> Literal["positive", "neutral", "negative"]:
    """
    Categorize sentiment score into discrete labels.
    
    Args:
        score: Sentiment score from -1.0 to 1.0
        
    Returns:
        Category label
        
    Thresholds:
    - Positive: score >= 0.1
    - Negative: score <= -0.1
    - Neutral: -0.1 < score < 0.1
    
    Usage:
        category = categorize_sentiment(0.75)
        # category = "positive"
    """
    if score >= 0.1:
        return "positive"
    elif score <= -0.1:
        return "negative"
    else:
        return "neutral"


def get_sentiment_emoji(score: float) -> str:
    """
    Get emoji representation of sentiment score.
    
    Args:
        score: Sentiment score from -1.0 to 1.0
        
    Returns:
        Emoji string
        
    Usage:
        emoji = get_sentiment_emoji(0.8)
        # emoji = "😊"
    """
    if score >= 0.6:
        return "😊"  # Very positive
    elif score >= 0.2:
        return "🙂"  # Positive
    elif score >= -0.2:
        return "😐"  # Neutral
    elif score >= -0.6:
        return "😟"  # Negative
    else:
        return "😢"  # Very negative


async def batch_analyze_sentiment(texts: list[str]) -> list[float]:
    """
    Analyze sentiment of multiple texts in batch.
    
    More efficient than analyzing individually when processing
    multiple messages at once.
    
    Args:
        texts: List of texts to analyze
        
    Returns:
        List of sentiment scores (same order as input)
        
    Usage:
        messages = ["Great!", "Terrible", "Okay I guess"]
        scores = await batch_analyze_sentiment(messages)
        # scores = [0.8, -0.7, 0.1]
    """
    import asyncio
    
    # Analyze all texts concurrently
    tasks = [analyze_sentiment(text) for text in texts]
    scores = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Replace exceptions with neutral score
    return [
        score if isinstance(score, float) else 0.0
        for score in scores
    ]


def get_sentiment_description(score: float) -> str:
    """
    Get human-readable description of sentiment score.
    
    Args:
        score: Sentiment score from -1.0 to 1.0
        
    Returns:
        Description string
        
    Usage:
        desc = get_sentiment_description(0.75)
        # desc = "Very Positive"
    """
    if score >= 0.6:
        return "Very Positive"
    elif score >= 0.2:
        return "Positive"
    elif score >= -0.2:
        return "Neutral"
    elif score >= -0.6:
        return "Negative"
    else:
        return "Very Negative"
