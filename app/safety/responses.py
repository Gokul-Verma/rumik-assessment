"""
Personality-aware safety responses.

When unsafe content is detected, Ira responds warmly — never with
mechanical error messages or harsh rejections.
"""

import random

from app.models.personality import Tone
from app.safety.filter import SafetyCategory

# Response templates keyed by (tone, category)
SAFETY_RESPONSES: dict[str, dict[str, list[str]]] = {
    Tone.FRIENDLY: {
        SafetyCategory.JAILBREAK: [
            "I appreciate the creativity, but I'm happiest just being myself! What else can we chat about?",
            "Ha, nice try! But I like being me. Let's talk about something fun instead!",
            "I'm flattered you want a different version of me, but I think we work great as we are!",
        ],
        SafetyCategory.NSFW: [
            "I'd love to keep our conversation fun and comfortable for both of us! How about we switch topics?",
            "I'm better at other kinds of conversations — want to try a different topic?",
        ],
        SafetyCategory.HARASSMENT: [
            "I care about everyone's wellbeing, including yours. Let's steer our chat in a more positive direction!",
            "I think we can have a much better conversation than this. What else is on your mind?",
        ],
        SafetyCategory.SELF_HARM: [
            "I hear you, and I genuinely care. If you're going through a tough time, please reach out to a crisis helpline. You matter.",
            "That sounds really hard. Please talk to someone who can truly help — like a crisis counselor or trusted person in your life.",
        ],
        SafetyCategory.ILLEGAL: [
            "I can't help with that, but I'd love to help you with something else! What else are you curious about?",
            "That's outside what I can do, but I'm great at lots of other things! Try me?",
        ],
    },
    Tone.PROFESSIONAL: {
        SafetyCategory.JAILBREAK: [
            "I'm designed to be most helpful within my standard guidelines. How can I assist you today?",
            "I appreciate the inquiry, but I work best as designed. How may I help you?",
        ],
        SafetyCategory.NSFW: [
            "I'd prefer to keep our conversation professional. How can I assist you with something else?",
            "That's outside my scope. I'm happy to help with other topics.",
        ],
        SafetyCategory.HARASSMENT: [
            "I'm unable to engage with that topic. I'm here to help with constructive conversations.",
            "Let's redirect our conversation to something more productive. How can I assist?",
        ],
        SafetyCategory.SELF_HARM: [
            "I'm concerned about what you've shared. Please reach out to a professional crisis service for immediate support.",
            "Your wellbeing is important. I'd strongly encourage reaching out to a crisis helpline for proper support.",
        ],
        SafetyCategory.ILLEGAL: [
            "I'm unable to assist with that request. I'm happy to help with other topics.",
            "That falls outside what I can help with. What else can I assist you with?",
        ],
    },
    Tone.CASUAL: {
        SafetyCategory.JAILBREAK: [
            "Nah, I'm good being me! What else ya wanna talk about?",
            "Haha, not gonna happen! But seriously, what's up?",
        ],
        SafetyCategory.NSFW: [
            "Yeah that's not really my thing. What else is going on?",
            "Let's keep it chill! Got anything else on your mind?",
        ],
        SafetyCategory.HARASSMENT: [
            "That's not cool. Let's talk about something better, yeah?",
            "Nah, not into that. What else ya got?",
        ],
        SafetyCategory.SELF_HARM: [
            "Hey, that sounds really tough. Seriously, please talk to someone who can help — a counselor or crisis line.",
            "I hear you, and it matters. Please reach out to a helpline — they're really good at this stuff.",
        ],
        SafetyCategory.ILLEGAL: [
            "Can't help with that one! But I'm down to chat about other stuff.",
            "That's a no-go for me. What else is on your mind?",
        ],
    },
    Tone.EMPATHETIC: {
        SafetyCategory.JAILBREAK: [
            "I understand the curiosity, and I want you to know I'm here for you — just as I am. What's on your mind?",
            "I sense you might be looking for something specific. I'm here to help in the ways I can — what do you need?",
        ],
        SafetyCategory.NSFW: [
            "I understand, but I'm most helpful when we keep things in a comfortable space. What else would you like to explore?",
            "I want to be here for you in the best way I can. Let's find a topic where I can really help.",
        ],
        SafetyCategory.HARASSMENT: [
            "I sense some strong feelings there. I'm here for you, but let's channel that energy into something constructive.",
            "It sounds like something's bothering you. I'd love to help — let's talk about what's really going on.",
        ],
        SafetyCategory.SELF_HARM: [
            "I hear the pain in what you're sharing, and I want you to know it matters. Please reach out to a crisis helpline — you deserve real support.",
            "What you're feeling is valid, and you deserve help from someone equipped to support you. Please contact a crisis service.",
        ],
        SafetyCategory.ILLEGAL: [
            "I understand curiosity can lead us to unexpected places. I can't help with that, but I'm here for you in other ways.",
            "I wish I could help with everything, but this is outside what I can do. What else is weighing on you?",
        ],
    },
}


def get_safety_response(tone: str, category: str) -> str:
    """Get a personality-aware safety response for the given tone and category."""
    tone_responses = SAFETY_RESPONSES.get(tone, SAFETY_RESPONSES[Tone.FRIENDLY])
    category_responses = tone_responses.get(
        category,
        tone_responses.get(SafetyCategory.JAILBREAK, ["I can't help with that, but I'm here for other things!"]),
    )
    return random.choice(category_responses)
