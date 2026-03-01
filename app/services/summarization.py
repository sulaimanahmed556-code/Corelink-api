"""
CORELINK Summarization Service

Uses the AI strategy pattern — defaults to Ollama (local Llama3),
upgrades to OpenAI if OPENAI_API_KEY is configured.
"""

import json
from loguru import logger

from app.services.ai.factory import ai


async def summarize_messages(messages: list[str]) -> str:
    """
    Summarize a list of group messages into a 150-200 word paragraph.

    Uses the configured AI provider (Ollama by default).
    """
    if not messages:
        raise ValueError("Messages list cannot be empty")

    cleaned = [m.strip() for m in messages if m.strip()]
    combined = "\n".join(cleaned)
    if len(combined) > 8000:
        combined = combined[:8000] + "..."

    try:
        result = await ai().complete(
            system=(
                "You are an expert at summarizing Telegram group conversations. "
                "Create a concise, engaging summary of the key topics, themes, and discussions. "
                "Keep it to 150-200 words. Focus on what matters most to the community. "
                "Use a friendly, professional tone."
            ),
            user=f"Summarize these group messages:\n\n{combined}\n\nSummary:",
            temperature=0.7,
            max_tokens=350,
        )
        summary = result.text
        if result.fallback_used:
            logger.info("Summary generated via fallback provider")
        return summary

    except Exception as exc:
        logger.error(f"Summarization failed: {exc}")
        return _fallback_summary(cleaned)


async def summarize_topics(messages: list[str], max_topics: int = 5) -> list[str]:
    """Extract main discussion topics from messages."""
    if not messages:
        return []

    cleaned = [m.strip() for m in messages if m.strip()]
    combined = "\n".join(cleaned)[:6000]

    try:
        result = await ai().complete(
            system=(
                f"Extract the top {max_topics} main topics discussed in these Telegram messages. "
                "Return ONLY a valid JSON array of short topic strings (2-5 words each). "
                'Example: ["Python tips", "Upcoming meetup", "Resource sharing"]'
            ),
            user=combined,
            temperature=0.4,
            max_tokens=150,
        )
        raw = result.text.strip()
        # Strip potential markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1].strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()
        topics = json.loads(raw)
        if isinstance(topics, list):
            return [str(t) for t in topics[:max_topics]]
        return []

    except Exception as exc:
        logger.warning(f"Topic extraction failed: {exc}")
        return []


async def generate_weekly_agenda(messages: list[str], group_name: str) -> dict:
    """
    Generate a structured weekly agenda based on group conversation activity.

    Returns:
        {
            "summary": str,
            "topics": list[str],
            "highlights": list[str],
            "action_items": list[str],
            "engagement_notes": str
        }
    """
    if not messages:
        return _empty_agenda()

    cleaned = [m.strip() for m in messages if m.strip()]
    combined = "\n".join(cleaned)[:7000]

    try:
        result = await ai().complete(
            system=(
                "You are a community manager AI analyzing a Telegram group's weekly conversations. "
                "Generate a structured weekly agenda/report based on what actually happened this week. "
                "Return ONLY valid JSON with these keys: "
                '"summary" (2-3 sentence overview), '
                '"topics" (list of up to 6 main topics discussed), '
                '"highlights" (list of up to 4 notable moments or conversations), '
                '"action_items" (list of up to 5 things the group should do next), '
                '"engagement_notes" (1 sentence about overall participation level).'
            ),
            user=(
                f"Group: {group_name}\n\n"
                f"This week's messages:\n{combined}\n\n"
                "Generate the weekly agenda JSON:"
            ),
            temperature=0.6,
            max_tokens=600,
        )

        raw = result.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()

        agenda = json.loads(raw)
        # Ensure all keys present
        return {
            "summary": agenda.get("summary", ""),
            "topics": agenda.get("topics", []),
            "highlights": agenda.get("highlights", []),
            "action_items": agenda.get("action_items", []),
            "engagement_notes": agenda.get("engagement_notes", ""),
        }

    except Exception as exc:
        logger.error(f"Agenda generation failed: {exc}")
        return _empty_agenda()


async def generate_key_insights(messages: list[str]) -> dict:
    """Generate key insights about group conversations."""
    if not messages:
        return {}

    cleaned = [m.strip() for m in messages if m.strip()]
    combined = "\n".join(cleaned)[:6000]

    try:
        result = await ai().complete(
            system=(
                "Analyze these group messages and provide key insights. "
                "Return a JSON object with: "
                '"most_discussed" (main topic, 1 sentence), '
                '"sentiment" (overall mood, 1 sentence), '
                '"action_items" (things to do, 1 sentence).'
            ),
            user=combined,
            temperature=0.5,
            max_tokens=200,
        )

        raw = result.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()

        insights = json.loads(raw)
        if isinstance(insights, dict):
            return insights
        return {}

    except Exception as exc:
        logger.warning(f"Key insights generation failed: {exc}")
        return {}


def _fallback_summary(messages: list[str]) -> str:
    total = len(messages)
    avg_len = int(sum(len(m) for m in messages) / max(total, 1))
    return (
        f"This period saw {total} messages exchanged in the group, "
        f"with an average message length of {avg_len} characters. "
        "Detailed AI-powered summary temporarily unavailable."
    )


def _empty_agenda() -> dict:
    return {
        "summary": "No messages found for this period.",
        "topics": [],
        "highlights": [],
        "action_items": [],
        "engagement_notes": "No activity recorded.",
    }
