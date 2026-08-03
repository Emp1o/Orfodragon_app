"""
domain/state.py — снимок состояния приложения для фронтенда.

Функция get_snapshot() собирает данные из всех доменных модулей
и возвращает единый объект, который фронтенд получает при загрузке.
"""

from backend.config import APP_VERSION
from backend.domain.cards import get_all_cards, get_due_cards, now_ms, day_key
from backend.domain.gamification import (
    boss_variant_from_weak_spots,
    build_missions,
    daily_goal_target,
    get_weak_spots,
    league_for_level,
    next_league_level,
    roll_daily_progress,
    _normalize_boss,
)
from backend.domain.settings import get_profile, get_settings, save_profile


def get_snapshot() -> dict:
    settings = get_settings()
    profile  = get_profile()
    roll_daily_progress(profile)

    all_cards = get_all_cards(sort="updated")
    n         = now_ms()
    due       = [c for c in all_cards if c.get("due_at", 0) <= n]
    favorites = [c for c in all_cards if c.get("favorite")]
    weak      = get_weak_spots()

    # Карточки с приоритетом для повторения
    priority_review = (weak if weak else due)[:50]

    goal      = daily_goal_target(settings, profile)
    remembered = profile.get("rememberedToday", 0)
    today      = day_key()

    total_reviews  = profile.get("totalReviews", 1) or 1
    accuracy       = round(profile.get("totalCorrect", 0) / total_reviews * 100)
    rating         = round(profile.get("xp", 0) + profile.get("totalCorrect", 0) * 5)
    level          = profile.get("level", 1)
    league         = league_for_level(level)

    stats = {
        "total":         len(all_cards),
        "due":           len(due),
        "favorites":     len(favorites),
        "weakCount":     len(weak),
        "remembered":    sum(1 for c in all_cards if c.get("last_result") == "remember"),
        "forgotten":     sum(1 for c in all_cards if c.get("last_result") == "forget"),
        "accuracy":      accuracy,
        "currentStreak": profile.get("currentStreak", 0),
        "bestStreak":    profile.get("bestStreak", 0),
        "xp":            profile.get("xp", 0),
        "level":         level,
        "rating":        rating,
        "goalProgress":  f"{min(remembered, goal)}/{goal}",
        "league":        league,
        "nextLeague":    next_league_level(level),
    }

    variant = boss_variant_from_weak_spots(weak)
    _normalize_boss(profile, variant)
    save_profile(profile)

    missions    = build_missions(profile, settings)
    chest_prog  = goal if profile.get("chestClaimedDay") == today else min(remembered, goal)
    chest_avail = chest_prog >= goal and profile.get("chestClaimedDay") != today

    return {
        "ok":                 True,
        "appVersion":         APP_VERSION,
        "settings":           settings,
        "cards":              all_cards[:100],
        "favorites":          sorted(favorites, key=lambda c: -c.get("score", 0))[:50],
        "dueCards":           due[:50],
        "priorityReviewCards": priority_review,
        "profile":            profile,
        "stats":              stats,
        "gamification": {
            "league":            league,
            "nextLeague":        stats["nextLeague"],
            "chestAvailable":    chest_avail,
            "weakSpots":         weak,
            "bossVariant":       variant,
            "bossHp":            profile["bossHp"],
            "bossMaxHp":         profile["bossMaxHp"],
            "bossSkin":          profile["bossSkin"],
            "bossStage":         profile["bossStage"],
            "bossLastDamage":    profile["bossLastDamage"],
            "missions":          missions,
            "dailyGoal":         goal,
            "rememberedToday":   min(remembered, goal * 2),
            "dailyProgressDay":  profile.get("dailyProgressDay", today),
            "chestProgress": {
                "progress": chest_prog,
                "target":   goal,
                "claimed":  profile.get("chestClaimedDay") == today,
            },
            "chestClaimedDay": profile.get("chestClaimedDay", ""),
        },
    }
