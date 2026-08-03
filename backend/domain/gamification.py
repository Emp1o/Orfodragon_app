"""
domain/gamification.py — геймификация: XP, стрики, босс, миссии, лиги.

Модуль отвечает исключительно за игровую механику; работа с карточками —
в domain/cards.py.
"""

import random
from typing import Optional

from backend.db.connection import get_db
from backend.domain.cards import (
    day_key,
    get_all_cards,
    now_ms,
    prev_day_key,
)
from backend.domain.settings import (
    get_profile,
    get_settings,
    save_profile,
)


# ── Скины дракона ────────────────────────────────────────────────────────────

DRAGON_SKINS: list[str] = [
    "dragon_academy", "dragon_ege",  "dragon_gold",
    "dragon_forest",  "dragon_storm", "dragon_crystal",
    "dragon_shadow",
]

_DEFAULT_SKINS: list[str] = ["dragon_king", "dragon_boss", "dragon_sage"]

# ── Лиги ─────────────────────────────────────────────────────────────────────

_LEAGUES: list[str] = [
    "Бронзовый Орфомастер",
    "Серебряный Орфомастер",
    "Золотой Орфомастер",
    "Платиновый Орфомастер",
    "Изумрудный Орфомастер",
]


def league_for_level(level: int) -> str:
    level = max(1, int(level or 1))
    idx = (level - 1) // 7
    if idx < len(_LEAGUES) - 1:
        return _LEAGUES[idx]
    return f"Алмазный Орфомастер {idx - len(_LEAGUES) + 2}"


def next_league_level(level: int) -> int:
    return ((max(1, int(level or 1)) - 1) // 7 + 1) * 7 + 1


# ── XP и уровень ─────────────────────────────────────────────────────────────

def add_xp(points: int) -> None:
    profile = get_profile()
    profile["xp"] = profile.get("xp", 0) + points
    profile["level"] = profile["xp"] // 100 + 1
    save_profile({"xp": profile["xp"], "level": profile["level"]})


# ── Ежедневная цель ───────────────────────────────────────────────────────────

def daily_goal_target(settings: Optional[dict] = None, profile: Optional[dict] = None) -> int:
    if settings is None:
        settings = get_settings()
    if profile is None:
        profile = get_profile()
    base  = int(settings.get("dailyGoal", 10))
    bonus = min(12, int(profile.get("missionsCompleted", 0)) // 2)
    return base + bonus


def roll_daily_progress(profile: dict) -> dict:
    """Сбрасывает дневной прогресс, если наступил новый день."""
    today = day_key()
    if not profile.get("dailyProgressDay"):
        profile["dailyProgressDay"] = today
        profile["dailyMissionDay"]  = today
    if profile["dailyProgressDay"] != today:
        profile["dailyProgressDay"]     = today
        profile["dailyMissionDay"]      = today
        profile["rememberedToday"]      = 0
        profile["dailyGoalRewardedDay"] = ""
    return profile


def touch_daily_activity() -> None:
    """Обновляет стрик и дату последней активности."""
    profile = get_profile()
    roll_daily_progress(profile)
    today = day_key()
    last  = profile.get("lastActiveDay", "")

    if not last:
        profile["lastActiveDay"]  = today
        profile["currentStreak"] = 1
    elif last != today:
        profile["currentStreak"] = (
            profile.get("currentStreak", 0) + 1
            if last == prev_day_key()
            else 1
        )
        profile["lastActiveDay"] = today

    profile["bestStreak"] = max(
        profile.get("bestStreak", 0),
        profile.get("currentStreak", 1),
    )
    save_profile(profile)


def _update_profile_after_grade(result: str, settings: dict) -> None:
    """Вызывается из cards.grade_card после оценки карточки."""
    profile = get_profile()
    roll_daily_progress(profile)
    profile["totalReviews"] = profile.get("totalReviews", 0) + 1

    if result == "remember":
        profile["totalCorrect"]    = profile.get("totalCorrect", 0) + 1
        profile["rememberedToday"] = profile.get("rememberedToday", 0) + 1

        goal = daily_goal_target(settings, profile)
        today = day_key()
        if (
            profile["rememberedToday"] >= goal
            and profile.get("dailyGoalRewardedDay") != today
        ):
            profile["dailyGoalHits"]         = profile.get("dailyGoalHits", 0) + 1
            profile["dailyGoalRewardedDay"]  = today
            profile["xp"]                    = profile.get("xp", 0) + 20
            profile["level"]                 = profile["xp"] // 100 + 1

    save_profile(profile)


# ── Босс ──────────────────────────────────────────────────────────────────────

def _boss_max_hp(stage: int) -> int:
    return 1200 if stage <= 1 else 3500 + (stage - 2) * 1800


def _normalize_boss(profile: dict, variant: str = "mixed") -> None:
    """Приводит состояние босса к консистентному виду in-place."""
    stage  = max(1, int(profile.get("bossStage", 1)))
    max_hp = _boss_max_hp(stage)
    profile["bossStage"]      = stage
    profile["bossMaxHp"]      = max_hp
    profile["bossHp"]         = max(0, min(max_hp, int(profile.get("bossHp", max_hp))))
    profile["bossSkin"]       = profile.get("bossSkin", "dragon_boss")
    profile["bossVariant"]    = variant
    profile["bossLastDamage"] = int(profile.get("bossLastDamage", 0))


def boss_variant_from_weak_spots(weak_spots: list) -> str:
    """Определяет тип босса по слабым местам пользователя."""
    kinds = [w.get("kind", "stress") for w in (weak_spots or [])]
    if not kinds:
        return "mixed"
    stress_count = sum(1 for k in kinds if k == "stress")
    punct_count  = sum(1 for k in kinds if k == "punctuation")
    if stress_count == 0 and punct_count > 0:
        return "punctuation"
    if punct_count == 0 and stress_count > 0:
        return "stress"
    return "mixed"


def damage_boss(amount: int, variant: str = "mixed") -> dict:
    profile = get_profile()
    _normalize_boss(profile, variant)
    profile["bossHp"]         = max(0, profile["bossHp"] - amount)
    profile["bossLastDamage"] = amount

    if profile["bossHp"] == 0:
        profile["bossStage"] += 1
        new_max = _boss_max_hp(profile["bossStage"])
        profile["bossMaxHp"] = new_max
        profile["bossHp"]    = new_max
        # Разблокируем случайный скин при победе над боссом
        unlocked = profile.get("unlockedMascotSkins", [])
        locked   = [s for s in DRAGON_SKINS if s not in unlocked]
        if locked:
            profile["bossSkin"] = random.choice(locked)

    save_profile(profile)
    return {
        "bossHp":      profile["bossHp"],
        "bossMaxHp":   profile["bossMaxHp"],
        "bossVariant": variant,
        "bossSkin":    profile["bossSkin"],
        "bossStage":   profile["bossStage"],
    }


# ── Сундук и скины ───────────────────────────────────────────────────────────

def claim_daily_chest() -> dict:
    profile  = get_profile()
    roll_daily_progress(profile)
    settings = get_settings()
    today    = day_key()

    goal = daily_goal_target(settings, profile)
    rem  = profile.get("rememberedToday", 0)

    if rem < goal:
        return {"ok": False, "reason": "goal_not_reached", "progress": rem, "target": goal}
    if profile.get("chestClaimedDay") == today:
        return {"ok": False, "reason": "already_claimed"}

    profile["chestClaimedDay"]   = today
    profile["missionsCompleted"] = profile.get("missionsCompleted", 0) + 1

    unlocked = list(profile.get("unlockedMascotSkins", _DEFAULT_SKINS))
    locked   = [s for s in DRAGON_SKINS if s not in unlocked]
    new_skin = None
    if locked and random.random() < 0.35:
        new_skin = random.choice(locked)
        unlocked.append(new_skin)

    profile["unlockedMascotSkins"] = unlocked
    save_profile(profile)
    add_xp(30)

    fresh = get_profile()
    return {
        "ok":                  True,
        "xp":                  fresh.get("xp", 0),
        "level":               fresh.get("level", 1),
        "skinUnlocked":        new_skin,
        "unlockedMascotSkins": unlocked,
    }


def set_mascot_skin(skin: str) -> dict:
    profile  = get_profile()
    unlocked = set(profile.get("unlockedMascotSkins", [])) | set(_DEFAULT_SKINS)
    if skin not in unlocked:
        return {"ok": False, "reason": "skin_locked"}
    profile["selectedMascotSkin"] = skin
    save_profile(profile)
    return {"ok": True}


# ── Слабые места ─────────────────────────────────────────────────────────────

def get_weak_spots() -> list[dict]:
    """Возвращает карточки с наибольшим числом ошибок за последние 7 дней."""
    week_ago = now_ms() - 7 * 24 * 3_600_000
    with get_db() as conn:
        rows = conn.execute(
            """SELECT ca.*, COUNT(rh.id) AS forgets
               FROM cards ca
               JOIN review_history rh ON ca.id = rh.card_id
               WHERE rh.result = 'forget' AND rh.at >= ?
               GROUP BY ca.id
               ORDER BY forgets DESC
               LIMIT 10""",
            (week_ago,),
        ).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        d["favorite"] = bool(d.get("favorite", 0))
        d["forgets"]  = d["forgets"]
        d["label"]    = d.get("prompt") or d.get("word") or "?"
        result.append(d)
    return result


# ── Миссии ───────────────────────────────────────────────────────────────────

def _today_history_count(kind: Optional[str] = None, result: Optional[str] = None) -> int:
    today = day_key()
    sql    = (
        "SELECT COUNT(*) FROM review_history rh "
        "JOIN cards ca ON rh.card_id = ca.id "
        "WHERE strftime('%Y-%m-%d', rh.at / 1000, 'unixepoch') = ?"
    )
    params: list = [today]
    if kind:
        sql    += " AND ca.kind = ?"
        params.append(kind)
    if result:
        sql    += " AND rh.result = ?"
        params.append(result)
    with get_db() as conn:
        return conn.execute(sql, params).fetchone()[0]


def _mission_target(base: int, profile: dict) -> int:
    bonus = min(12, int(profile.get("missionsCompleted", 0)) // 2)
    return base + bonus


def build_missions(profile: dict, settings: dict) -> list[dict]:
    roll_daily_progress(profile)
    remembered_today = profile.get("rememberedToday", 0)
    base_goal        = int(settings.get("dailyGoal", 10))
    goal             = daily_goal_target(settings, profile)
    today            = day_key()

    # Детерминированный выбор фокусной миссии по дате
    seed_str = profile.get("dailyMissionDay") or today
    seed     = sum(ord(ch) * (i + 1) for i, ch in enumerate(seed_str))

    stress_remembered  = _today_history_count(kind="stress",       result="remember")
    punct_remembered   = _today_history_count(kind="punctuation",  result="remember")
    all_remembered     = _today_history_count(result="remember")
    all_forgotten      = _today_history_count(result="forget")
    all_reviewed       = _today_history_count()

    focus_variants = [
        {
            "id": "stress_today", "title": "Тренировка дня: ударения",
            "progress": stress_remembered,
            "target":   _mission_target(min(5, max(3, base_goal)), profile),
        },
        {
            "id": "punct_today", "title": "Тренировка дня: запятые",
            "progress": punct_remembered,
            "target":   _mission_target(min(5, max(3, base_goal)), profile),
        },
        {
            "id": "acc_today", "title": "Тренировка дня: без ошибок",
            "progress": max(0, all_remembered - all_forgotten),
            "target":   _mission_target(min(7, max(4, base_goal)), profile),
        },
    ]
    focus = focus_variants[seed % len(focus_variants)]

    chest_claimed = profile.get("chestClaimedDay") == today
    missions = [
        {
            "id": "daily_goal", "title": "Дневная цель",
            "progress": min(remembered_today, goal),
            "target":   goal, "type": "daily",
        },
        {**focus, "type": "focus"},
        {
            "id": "review_today", "title": "Разогрев: повтори карточки",
            "progress": all_reviewed,
            "target":   _mission_target(min(8, max(4, base_goal)), profile),
            "type":     "review",
        },
        {
            "id":       "daily_chest",
            "title":    "Сундук дня открыт" if chest_claimed else "Открой сундук дня",
            "progress": goal if chest_claimed else min(remembered_today, goal),
            "target":   goal,
            "type":     "chest",
        },
    ]

    # Прогресс не может превышать цель
    for m in missions:
        m["progress"] = min(int(m.get("progress", 0)), int(m.get("target", 1)))

    return missions
