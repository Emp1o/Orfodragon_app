"""
domain/training.py — построение тренировочных сессий.
"""

import random
from typing import Optional

from backend.domain.cards import get_all_cards, now_ms
from backend.domain.gamification import get_weak_spots


_active_session: Optional[dict] = None


def build_session(mode: str = "all", count: int = 10) -> dict:
    global _active_session

    cards = get_all_cards()

    if mode == "stress":
        cards = [c for c in cards if c.get("kind") == "stress"]
    elif mode == "punctuation":
        cards = [c for c in cards if c.get("kind") == "punctuation"]
    elif mode == "weak":
        cards = get_weak_spots()

    n   = now_ms()
    due = [c for c in cards if c.get("due_at", 0) <= n]
    source = due if due else cards

    random.shuffle(source)
    picked = source[: max(1, count)]

    _active_session = {
        "cards":     picked,
        "mode":      mode,
        "count":     len(picked),
        "createdAt": n,
    }
    return _active_session
