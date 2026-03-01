"""
CORELINK Churn Detection Service

Uses real ML to detect users at risk of being removed from a group.

Churn in CORELINK means a user is at risk of being kicked/removed
because their behaviour does not align with group rules.

Detection uses three signals:
1. Behavioural risk — toxic language, excessive link-posting, rule violations
   scored via a transformer-based toxicity classifier (Detoxify) with graceful
   VADER fallback.
2. Inactivity — time since last message.
3. Engagement decline — drop in message frequency over time.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import TypedDict

from loguru import logger


# ─── Types ───────────────────────────────────────────────────────────────────

class UserActivity(TypedDict):
    user_id: str
    username: str | None
    telegram_user_id: int
    last_active: datetime
    sentiment_trend: float        # -1 to 1 (from VADER on recent msgs)
    message_frequency: float      # messages per day over last 30 days
    recent_messages: list[str]    # last 20-50 messages for behaviour analysis


class ChurnScore(TypedDict):
    user_id: str
    username: str | None
    telegram_user_id: int
    risk_score: float             # 0.0–1.0
    risk_level: str               # Low / Medium / High
    factors: dict[str, float]
    behaviour_flags: list[str]    # human-readable flags e.g. ["excessive_links"]


# ─── Detoxify loader (lazy, cached) ──────────────────────────────────────────

_detoxify_model = None
_detoxify_available = None      # None = not checked yet


def _get_detoxify():
    """Lazy-load Detoxify multilingual model. Returns None if not installed."""
    global _detoxify_model, _detoxify_available

    if _detoxify_available is False:
        return None
    if _detoxify_model is not None:
        return _detoxify_model

    try:
        from detoxify import Detoxify
        _detoxify_model = Detoxify("multilingual")
        _detoxify_available = True
        logger.info("Detoxify multilingual model loaded for churn detection")
        return _detoxify_model
    except Exception as exc:
        _detoxify_available = False
        logger.warning(
            f"Detoxify not available ({exc}). "
            "Falling back to VADER + heuristic behaviour analysis. "
            "Install detoxify for more accurate churn scoring: pip install detoxify"
        )
        return None


# ─── VADER fallback ───────────────────────────────────────────────────────────

_vader = None

def _get_vader():
    global _vader
    if _vader is None:
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            _vader = SentimentIntensityAnalyzer()
        except Exception:
            pass
    return _vader


# ─── Behaviour analysis ───────────────────────────────────────────────────────

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_SPAM_RE = re.compile(
    r"(join|click|earn|free|win|prize|crypto|invest|binary|signal|pump|promo|ad\b|subscribe)",
    re.IGNORECASE,
)
_CAPS_RATIO_THRESHOLD = 0.6     # >60% uppercase = aggressive tone
_LINK_THRESHOLD = 0.4           # >40% of messages contain links = spam risk


def _analyse_behaviour(messages: list[str]) -> tuple[float, list[str]]:
    """
    Analyse a user's recent messages for rule-violating behaviours.

    Returns (behaviour_score 0.0–1.0, list of flags).
    """
    if not messages:
        return 0.0, []

    flags: list[str] = []
    scores: list[float] = []

    total = len(messages)
    link_count = 0
    caps_count = 0
    spam_count = 0

    detox = _get_detoxify()

    for msg in messages:
        text = msg.strip()
        if not text:
            continue

        # Link posting
        if _URL_RE.search(text):
            link_count += 1

        # ALL-CAPS aggression check
        letters = [c for c in text if c.isalpha()]
        if letters and sum(1 for c in letters if c.isupper()) / len(letters) > _CAPS_RATIO_THRESHOLD:
            caps_count += 1

        # Spam keywords
        if _SPAM_RE.search(text):
            spam_count += 1

        # Toxicity via Detoxify (preferred)
        if detox is not None:
            try:
                result = detox.predict(text)
                # result keys: toxicity, severe_toxicity, obscene, threat, insult, identity_attack, sexually_explicit
                tox = float(result.get("toxicity", 0.0))
                insult = float(result.get("insult", 0.0))
                threat = float(result.get("threat", 0.0))
                combined = max(tox, insult * 0.8, threat)
                scores.append(combined)
            except Exception:
                pass
        else:
            # Fallback: VADER compound used as a proxy (negative = mildly risky)
            vader = _get_vader()
            if vader:
                compound = vader.polarity_scores(text)["compound"]
                # Map very negative to small toxicity proxy
                tox_proxy = max(0.0, (-compound - 0.3) / 0.7)
                scores.append(tox_proxy)

    # Compute per-message averages
    link_ratio = link_count / total
    caps_ratio = caps_count / total
    spam_ratio = spam_count / total
    avg_toxicity = (sum(scores) / len(scores)) if scores else 0.0

    if link_ratio > _LINK_THRESHOLD:
        flags.append("excessive_links")
    if caps_ratio > 0.3:
        flags.append("aggressive_tone")
    if spam_ratio > 0.25:
        flags.append("spam_keywords")
    if avg_toxicity > 0.5:
        flags.append("toxic_language")
    elif avg_toxicity > 0.3:
        flags.append("borderline_language")

    # Weighted behaviour score
    behaviour_score = (
        link_ratio * 0.25
        + caps_ratio * 0.15
        + spam_ratio * 0.25
        + min(avg_toxicity, 1.0) * 0.35
    )
    return min(behaviour_score, 1.0), flags


# ─── Per-signal scorers ───────────────────────────────────────────────────────

def _inactivity_score(last_active: datetime) -> float:
    days = (datetime.utcnow() - last_active).days
    if days <= 3:
        return 0.0
    if days <= 7:
        return 0.3
    if days <= 14:
        return 0.55
    if days <= 30:
        return 0.8
    return 1.0


def _engagement_score(freq: float) -> float:
    """Lower frequency → higher risk."""
    if freq >= 5.0:
        return 0.0
    if freq >= 2.0:
        return 0.15
    if freq >= 1.0:
        return 0.4
    if freq >= 0.3:
        return 0.65
    return 1.0


def _sentiment_score(trend: float) -> float:
    """Very negative sentiment correlates with departure risk."""
    if trend > 0.2:
        return 0.0
    if trend > 0.0:
        return 0.1
    if trend > -0.3:
        return 0.35
    return 0.7


# ─── Public API ───────────────────────────────────────────────────────────────

def calculate_churn_score(user: UserActivity) -> ChurnScore:
    """
    Full churn risk score for a single user.

    Weights:
      - Behaviour (toxicity, links, spam): 45 %
      - Inactivity:                        30 %
      - Engagement decline:                15 %
      - Negative sentiment trend:          10 %
    """
    behaviour, flags = _analyse_behaviour(user.get("recent_messages", []))
    inactivity = _inactivity_score(user["last_active"])
    engagement = _engagement_score(user["message_frequency"])
    sentiment = _sentiment_score(user["sentiment_trend"])

    score = (
        behaviour * 0.45
        + inactivity * 0.30
        + engagement * 0.15
        + sentiment * 0.10
    )
    score = max(0.0, min(1.0, score))

    if score >= 0.65:
        level = "High Risk"
    elif score >= 0.35:
        level = "Medium Risk"
    else:
        level = "Low Risk"

    return ChurnScore(
        user_id=user["user_id"],
        username=user.get("username"),
        telegram_user_id=user["telegram_user_id"],
        risk_score=round(score, 3),
        risk_level=level,
        factors={
            "behaviour": round(behaviour, 3),
            "inactivity": round(inactivity, 3),
            "engagement": round(engagement, 3),
            "sentiment": round(sentiment, 3),
        },
        behaviour_flags=flags,
    )


def detect_churn(users: list[UserActivity]) -> list[str]:
    """Return identifiers for top-5 highest-risk users."""
    if not users:
        return []

    scored = sorted(
        [calculate_churn_score(u) for u in users],
        key=lambda s: s["risk_score"],
        reverse=True,
    )
    return [
        s["username"] if s["username"] else str(s["telegram_user_id"])
        for s in scored[:5]
    ]


async def get_detailed_churn_analysis(users: list[UserActivity]) -> list[dict]:
    """Full per-user churn analysis, sorted by descending risk."""
    results = []
    for user in users:
        score = calculate_churn_score(user)
        days_inactive = (datetime.utcnow() - user["last_active"]).days
        results.append({
            "user_id": user["user_id"],
            "username": user.get("username") or str(user["telegram_user_id"]),
            "telegram_user_id": user["telegram_user_id"],
            "risk_score": score["risk_score"],
            "risk_level": score["risk_level"],
            "factors": score["factors"],
            "behaviour_flags": score["behaviour_flags"],
            "days_inactive": days_inactive,
            "sentiment": user["sentiment_trend"],
            "frequency": user["message_frequency"],
            "explanation": _explanation(score, days_inactive),
        })
    results.sort(key=lambda x: x["risk_score"], reverse=True)
    return results


def _explanation(score: ChurnScore, days_inactive: int) -> str:
    parts = []
    if score["factors"]["behaviour"] > 0.3:
        flags = ", ".join(f.replace("_", " ") for f in score["behaviour_flags"])
        parts.append(f"behaviour issues ({flags})" if flags else "problematic behaviour")
    if days_inactive > 7:
        parts.append(f"inactive for {days_inactive} days")
    if score["factors"]["engagement"] > 0.4:
        parts.append("low message frequency")
    if score["factors"]["sentiment"] > 0.3:
        parts.append("negative sentiment trend")
    return ", ".join(parts) if parts else "active and compliant"


def get_churn_risk_level(risk_score: float) -> str:
    if risk_score >= 0.65:
        return "High Risk"
    if risk_score >= 0.35:
        return "Medium Risk"
    return "Low Risk"


def format_churn_report(at_risk_users: list[str]) -> str:
    if not at_risk_users:
        return "✅ No users at high risk."
    report = "⚠️ <b>Users at Risk:</b>\n\n"
    for i, u in enumerate(at_risk_users, 1):
        report += f"{i}. {'@' + u if not u.isdigit() else 'User ' + u}\n"
    report += "\n<i>Consider engaging these members.</i>"
    return report
