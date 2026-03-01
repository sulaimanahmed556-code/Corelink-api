"""
CORELINK NLP Utilities

Text cleaning and preprocessing helpers for sentiment analysis and summarization.
"""

import re
from typing import Pattern


# Compiled regex patterns for performance
URL_PATTERN: Pattern = re.compile(
    r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
)
MENTION_PATTERN: Pattern = re.compile(r'@\w+')
HASHTAG_PATTERN: Pattern = re.compile(r'#\w+')
EMOJI_PATTERN: Pattern = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map symbols
    "\U0001F1E0-\U0001F1FF"  # flags (iOS)
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE
)
MULTIPLE_SPACES: Pattern = re.compile(r'\s+')
BOT_COMMAND_PATTERN: Pattern = re.compile(r'^/\w+')


def clean_text(text: str) -> str:
    """
    Clean and normalize text for NLP processing.
    
    Removes or normalizes:
    - Extra whitespace
    - URLs
    - HTML tags
    - Special characters (while preserving punctuation)
    - Leading/trailing whitespace
    
    Preserves:
    - Emojis (useful for sentiment)
    - Mentions (context)
    - Hashtags (context)
    - Punctuation (sentiment indicators)
    
    Args:
        text: Raw text to clean
        
    Returns:
        Cleaned text string
        
    Usage:
        from app.utils.nlp import clean_text
        
        raw = "Check this out!  https://example.com   @user"
        clean = clean_text(raw)
        # "Check this out! @user"
    """
    if not text or not isinstance(text, str):
        return ""
    
    # Remove leading/trailing whitespace
    text = text.strip()
    
    # Remove URLs (they don't add sentiment/context value)
    text = URL_PATTERN.sub('', text)
    
    # Remove HTML tags if any
    text = re.sub(r'<[^>]+>', '', text)
    
    # Normalize multiple spaces to single space
    text = MULTIPLE_SPACES.sub(' ', text)
    
    # Final cleanup
    text = text.strip()
    
    return text


def clean_text_aggressive(text: str) -> str:
    """
    Aggressively clean text for summarization (removes more elements).
    
    Removes:
    - URLs
    - Mentions
    - Hashtags
    - Emojis
    - HTML tags
    - Extra whitespace
    - Bot commands
    
    Args:
        text: Raw text to clean
        
    Returns:
        Heavily cleaned text
        
    Usage:
        text = clean_text_aggressive("Hey @user! 😊 #python https://...")
        # "Hey!"
    """
    if not text or not isinstance(text, str):
        return ""
    
    text = text.strip()
    
    # Remove bot commands
    text = BOT_COMMAND_PATTERN.sub('', text)
    
    # Remove URLs
    text = URL_PATTERN.sub('', text)
    
    # Remove mentions
    text = MENTION_PATTERN.sub('', text)
    
    # Remove hashtags
    text = HASHTAG_PATTERN.sub('', text)
    
    # Remove emojis (for summarization, not sentiment)
    text = EMOJI_PATTERN.sub('', text)
    
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    
    # Normalize whitespace
    text = MULTIPLE_SPACES.sub(' ', text)
    
    return text.strip()


def preprocess_messages(messages: list[str]) -> list[str]:
    """
    Preprocess list of messages for NLP analysis.
    
    - Cleans each message
    - Removes empty messages
    - Removes duplicates (preserves order)
    - Filters very short messages (< 3 chars)
    
    Args:
        messages: List of raw message texts
        
    Returns:
        List of cleaned, filtered messages
        
    Usage:
        from app.utils.nlp import preprocess_messages
        
        raw_messages = [
            "Hello world!  https://example.com",
            "",
            "Hi",
            "Hello world!  https://example.com",  # duplicate
            "Check out this amazing tutorial"
        ]
        
        clean_messages = preprocess_messages(raw_messages)
        # ["Hello world!", "Check out this amazing tutorial"]
    """
    if not messages:
        return []
    
    cleaned_messages = []
    seen = set()
    
    for message in messages:
        # Skip non-string types
        if not isinstance(message, str):
            continue
        
        # Clean the message
        cleaned = clean_text(message)
        
        # Skip empty or very short messages
        if len(cleaned) < 3:
            continue
        
        # Skip duplicates (case-insensitive)
        lower_msg = cleaned.lower()
        if lower_msg in seen:
            continue
        
        seen.add(lower_msg)
        cleaned_messages.append(cleaned)
    
    return cleaned_messages


def remove_bot_messages(messages: list[str]) -> list[str]:
    """
    Remove bot commands and automated messages from list.
    
    Filters out:
    - Messages starting with / (commands)
    - Common bot responses
    - System messages
    - Very repetitive messages
    
    Args:
        messages: List of message texts
        
    Returns:
        List with bot messages filtered out
        
    Usage:
        from app.utils.nlp import remove_bot_messages
        
        messages = [
            "/start",
            "/help me please",
            "Hello everyone!",
            "Bot: Command executed",
            "Great discussion today"
        ]
        
        human_messages = remove_bot_messages(messages)
        # ["Hello everyone!", "Great discussion today"]
    """
    if not messages:
        return []
    
    filtered = []
    
    # Common bot message patterns
    bot_patterns = [
        r'^/\w+',  # Commands starting with /
        r'^bot:',  # Messages starting with "bot:"
        r'^\[bot\]',  # Messages starting with "[bot]"
        r'command executed',
        r'system message',
        r'automated message',
    ]
    
    combined_pattern = re.compile('|'.join(bot_patterns), re.IGNORECASE)
    
    for message in messages:
        if not isinstance(message, str):
            continue
        
        # Check if message matches bot patterns
        if combined_pattern.search(message):
            continue
        
        filtered.append(message)
    
    return filtered


def truncate_text(text: str, max_length: int = 500, preserve_words: bool = True) -> str:
    """
    Truncate text to maximum length.
    
    Args:
        text: Text to truncate
        max_length: Maximum character length
        preserve_words: If True, truncate at word boundary
        
    Returns:
        Truncated text
        
    Usage:
        text = truncate_text("Long text here...", max_length=20)
    """
    if not text or len(text) <= max_length:
        return text
    
    if preserve_words:
        # Truncate at last space before max_length
        truncated = text[:max_length]
        last_space = truncated.rfind(' ')
        if last_space > 0:
            truncated = truncated[:last_space]
        return truncated + "..."
    
    return text[:max_length] + "..."


def count_words(text: str) -> int:
    """
    Count words in text.
    
    Args:
        text: Text to analyze
        
    Returns:
        Word count
    """
    if not text:
        return 0
    return len(text.split())


def extract_keywords(text: str, max_keywords: int = 10) -> list[str]:
    """
    Extract potential keywords from text (simple frequency-based).
    
    Args:
        text: Text to analyze
        max_keywords: Maximum keywords to return
        
    Returns:
        List of keywords
        
    Note:
        This is a simple implementation. For production, consider
        using libraries like RAKE, YAKE, or TF-IDF.
    """
    if not text:
        return []
    
    # Common stop words to filter out
    stop_words = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been',
        'be', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
        'could', 'should', 'may', 'might', 'must', 'can', 'this', 'that',
        'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they'
    }
    
    # Extract words
    words = re.findall(r'\b\w+\b', text.lower())
    
    # Filter and count
    word_freq = {}
    for word in words:
        if len(word) > 2 and word not in stop_words:
            word_freq[word] = word_freq.get(word, 0) + 1
    
    # Sort by frequency
    sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
    
    return [word for word, freq in sorted_words[:max_keywords]]


def normalize_whitespace(text: str) -> str:
    """
    Normalize all whitespace to single spaces.
    
    Args:
        text: Text with irregular whitespace
        
    Returns:
        Text with normalized whitespace
    """
    if not text:
        return ""
    return ' '.join(text.split())


def is_meaningful_text(text: str, min_words: int = 2) -> bool:
    """
    Check if text is meaningful (not just noise).
    
    Args:
        text: Text to check
        min_words: Minimum word count
        
    Returns:
        True if text is meaningful
        
    Usage:
        if is_meaningful_text(message):
            # Process message
    """
    if not text or not isinstance(text, str):
        return False
    
    # Check word count
    if count_words(text) < min_words:
        return False
    
    # Check if mostly punctuation
    alpha_chars = sum(c.isalpha() for c in text)
    if alpha_chars < len(text) * 0.5:
        return False
    
    return True


def batch_clean_text(texts: list[str], aggressive: bool = False) -> list[str]:
    """
    Clean multiple texts efficiently.
    
    Args:
        texts: List of texts to clean
        aggressive: Use aggressive cleaning
        
    Returns:
        List of cleaned texts
        
    Usage:
        cleaned = batch_clean_text(["text1", "text2"], aggressive=True)
    """
    if not texts:
        return []
    
    clean_func = clean_text_aggressive if aggressive else clean_text
    return [clean_func(text) for text in texts if text]


def deduplicate_messages(messages: list[str], case_sensitive: bool = False) -> list[str]:
    """
    Remove duplicate messages while preserving order.
    
    Args:
        messages: List of messages
        case_sensitive: Whether to consider case in duplicates
        
    Returns:
        Deduplicated list
    """
    if not messages:
        return []
    
    seen = set()
    unique = []
    
    for msg in messages:
        key = msg if case_sensitive else msg.lower()
        if key not in seen:
            seen.add(key)
            unique.append(msg)
    
    return unique
