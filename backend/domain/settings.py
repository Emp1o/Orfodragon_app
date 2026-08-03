"""
domain/settings.py — настройки приложения и профиль пользователя.

Defaults вынесены в константы, чтобы их было легко найти и изменить.
"""

from backend.db import kv_store

# ── Настройки по умолчанию ───────────────────────────────────────────────────
SETTINGS_DEFAULTS: dict = {
    "apiBaseUrl":              "",
    "reviewCheckMinutes":      60,
    "firstIntervalMinutes":    15,
    "hardResetMinutes":        20,
    "secondIntervalMinutes":   1440,
    "thirdIntervalMinutes":    4320,
    "defaultEase":             2.2,
    "maxEase":                 3.0,
    "minEase":                 1.3,
    "autoSaveProcessedWords":  1,
    "dailyGoal":               10,
    "examMode":                1,
    "reviewBatchSize":         10,
    "textScale":               1,
}

# ── Профиль по умолчанию ─────────────────────────────────────────────────────
PROFILE_DEFAULTS: dict = {
    "xp":                    0,
    "level":                 1,
    "currentStreak":         0,
    "bestStreak":            0,
    "lastActiveDay":         "",
    "totalReviews":          0,
    "totalCorrect":          0,
    "rememberedToday":       0,
    "dailyProgressDay":      "",
    "dailyMissionDay":       "",
    "dailyGoalHits":         0,
    "dailyGoalRewardedDay":  "",
    "chestClaimedDay":       "",
    "missionsCompleted":     0,
    "unlockedMascotSkins":   ["dragon_king", "dragon_boss", "dragon_sage"],
    "selectedMascotSkin":    "dragon_king",
    "bossStage":             1,
    "bossHp":                1200,
    "bossMaxHp":             1200,
    "bossSkin":              "dragon_boss",
    "bossVariant":           "mixed",
    "bossLastDamage":        0,
}


def get_settings() -> dict:
    return kv_store.load("settings", SETTINGS_DEFAULTS)


def save_settings(updates: dict) -> None:
    """Применяет только известные ключи (защита от случайного мусора)."""
    current = get_settings()
    current.update({k: v for k, v in (updates or {}).items() if k in SETTINGS_DEFAULTS})
    kv_store.save("settings", current)


def get_profile() -> dict:
    return kv_store.load("profile", PROFILE_DEFAULTS)


def save_profile(data: dict) -> None:
    kv_store.save("profile", data)


def get_app_state(key: str, default=None):
    return kv_store.get_one("app_state", key, default)


def set_app_state(key: str, value) -> None:
    kv_store.set_one("app_state", key, value)
