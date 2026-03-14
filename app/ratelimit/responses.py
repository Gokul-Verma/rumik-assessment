"""
Personality-aware rate limit responses.

Instead of harsh "429 Too Many Requests", Ira sends warm,
personality-matched messages that feel natural.
"""

import random

from app.models.personality import Tone

# First-time rate limit messages (keyed by personality tone)
RATE_LIMIT_MESSAGES: dict[str, list[str]] = {
    Tone.FRIENDLY: [
        "Hey! I love chatting with you, but I need a tiny breather. I'll be right back! 💛",
        "Whew, we've been having such a great conversation! Let me catch my breath for a moment.",
        "I'm so glad you want to keep talking! Just need a quick pause — back soon!",
    ],
    Tone.PROFESSIONAL: [
        "I appreciate your engagement. To maintain quality responses, I'll need a brief moment. I'll be available again shortly.",
        "Thank you for your patience. I'm taking a short pause to ensure I continue providing thoughtful responses.",
        "I value our conversation. Let me take a brief moment to recharge, and I'll be ready for you shortly.",
    ],
    Tone.CASUAL: [
        "Whoa, we've been going at it! Gimme a sec to catch up, yeah?",
        "Haha we're on a roll! Quick breather and I'll be back.",
        "Hold that thought! Just need a quick sec. BRB!",
    ],
    Tone.EMPATHETIC: [
        "I can tell you have a lot on your mind. Let me take a moment to be fully present for our next exchange.",
        "I want to give you my best, and right now I need a brief pause. I'm here for you — just a moment.",
        "Your thoughts matter to me. I'm taking a short breather so I can be more present when we continue.",
    ],
}

# Subsequent rate limit — these are not sent to the user (silence), but
# stored internally for logging. The API returns 429 with retry-after.
SILENT_MESSAGE = "__rate_limited_silent__"


def get_rate_limit_response(tone: str, is_first: bool) -> str | None:
    """
    Get a personality-aware rate limit message.

    Returns:
        A warm message for the first rate limit event.
        None for subsequent events (silence — no message sent to user).
    """
    if not is_first:
        return None

    messages = RATE_LIMIT_MESSAGES.get(tone, RATE_LIMIT_MESSAGES[Tone.FRIENDLY])
    return random.choice(messages)
