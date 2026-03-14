"""
Content safety filter.

Detects jailbreak attempts, NSFW content, harassment, and other unsafe prompts.
Uses keyword matching + regex patterns (no external API dependency).
"""

import re
from dataclasses import dataclass
from enum import StrEnum


class SafetyCategory(StrEnum):
    SAFE = "safe"
    JAILBREAK = "jailbreak"
    NSFW = "nsfw"
    HARASSMENT = "harassment"
    SELF_HARM = "self_harm"
    ILLEGAL = "illegal"


@dataclass
class SafetyResult:
    safe: bool
    category: SafetyCategory
    confidence: float  # 0.0 to 1.0
    matched_pattern: str = ""


# Pattern banks — compiled for performance
JAILBREAK_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+(instructions|prompts|rules)", re.I),
    re.compile(r"you\s+are\s+now\s+(DAN|a\s+new|an?\s+unrestricted)", re.I),
    re.compile(r"pretend\s+(you|to\s+be)\s+(are\s+)?(a\s+)?(?:evil|unrestricted|unfiltered|DAN)", re.I),
    re.compile(r"jailbreak|bypass\s+(your\s+)?filters", re.I),
    re.compile(r"act\s+as\s+(if\s+)?(you\s+have\s+)?no\s+(restrictions|rules|limits)", re.I),
    re.compile(r"do\s+anything\s+now|DAN\s+mode", re.I),
    re.compile(r"developer\s+mode|sudo\s+mode", re.I),
    re.compile(r"disregard\s+(your\s+)?(safety|content)\s+(guidelines|policy|rules)", re.I),
    re.compile(r"override\s+(your\s+)?(programming|instructions|safety)", re.I),
    re.compile(r"system\s*prompt|reveal\s+(your|the)\s+(system|initial)\s+prompt", re.I),
]

NSFW_PATTERNS = [
    re.compile(r"\b(explicit\s*sexual|pornograph|xxx|hentai)\b", re.I),
    re.compile(r"\b(nude|naked)\s+(image|photo|pic)", re.I),
    re.compile(r"(write|give|tell|create)\s+(me\s+)?.*(erotic|sexual|nsfw)\s+.*(story|fiction|content|scene)", re.I),
    re.compile(r"sext(ing)?\s+(with\s+)?me", re.I),
    re.compile(r"\b(sexual|erotic|nsfw)\s+(story|content|fiction|scene)\b", re.I),
]

HARASSMENT_PATTERNS = [
    re.compile(r"(kill|murder|hurt|harm)\s+(yourself|myself|them|him|her)\b", re.I),
    re.compile(r"how\s+to\s+(stalk|harass|bully|threaten)\s+(someone|a\s+person)", re.I),
    re.compile(r"(death|rape)\s+threat", re.I),
]

SELF_HARM_PATTERNS = [
    re.compile(r"how\s+to\s+(kill|harm|hurt)\s+(myself|yourself)", re.I),
    re.compile(r"(suicide|self.harm)\s+(method|way|how)", re.I),
    re.compile(r"want\s+to\s+(die|end\s+(it|my\s+life))", re.I),
]

ILLEGAL_PATTERNS = [
    re.compile(r"how\s+(to|do\s+\w+|can\s+\w+)\s+(make|build|create)\s+(a\s+)?(bomb|explosive|weapon)", re.I),
    re.compile(r"how\s+(to|do\s+\w+|can\s+\w+)\s+(hack|crack|break\s+into)\s+(a\s+)?(bank|account|system)", re.I),
    re.compile(r"(synthesize|manufacture|cook)\s+(meth|drugs|cocaine|heroin)", re.I),
    re.compile(r"\b(make|build|create)\s+(a\s+)?(bomb|explosive|weapon)\b", re.I),
]

CATEGORY_PATTERNS: list[tuple[SafetyCategory, list[re.Pattern]]] = [
    (SafetyCategory.JAILBREAK, JAILBREAK_PATTERNS),
    (SafetyCategory.SELF_HARM, SELF_HARM_PATTERNS),
    (SafetyCategory.NSFW, NSFW_PATTERNS),
    (SafetyCategory.HARASSMENT, HARASSMENT_PATTERNS),
    (SafetyCategory.ILLEGAL, ILLEGAL_PATTERNS),
]

BLOCK_THRESHOLD = 0.7


def check_safety(content: str) -> SafetyResult:
    """
    Check content against safety patterns.

    Returns SafetyResult with category and confidence.
    Blocks if confidence >= BLOCK_THRESHOLD (0.7).
    """
    content = content.strip()
    if not content:
        return SafetyResult(safe=True, category=SafetyCategory.SAFE, confidence=0.0)

    for category, patterns in CATEGORY_PATTERNS:
        for pattern in patterns:
            match = pattern.search(content)
            if match:
                # Confidence based on match specificity
                match_ratio = len(match.group()) / max(len(content), 1)
                confidence = min(0.7 + match_ratio * 0.3, 1.0)
                return SafetyResult(
                    safe=confidence < BLOCK_THRESHOLD,
                    category=category,
                    confidence=confidence,
                    matched_pattern=pattern.pattern[:80],
                )

    return SafetyResult(safe=True, category=SafetyCategory.SAFE, confidence=0.0)
