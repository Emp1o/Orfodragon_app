"""
domain/cards.py — CRUD для карточек (ударения и запятые).

Вся работа с таблицами cards и review_history — здесь.
"""

import re
import time
from datetime import date, timedelta
from typing import Optional

from backend.db.connection import get_db, get_write_lock
from backend.domain.settings import get_settings


# ── Вспомогательные функции времени ─────────────────────────────────────────

def now_ms() -> int:
    return int(time.time() * 1000)


def day_key(ts_ms: Optional[int] = None) -> str:
    ts = (ts_ms / 1000) if ts_ms else time.time()
    return date.fromtimestamp(ts).isoformat()


def prev_day_key() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


# ── Нормализация текста ──────────────────────────────────────────────────────

def normalize_word(text) -> str:
    """Приводит слово к нижнему регистру, оставляет только буквы и дефис."""
    return re.sub(r"[^а-яё\-]", "", str(text or "").lower()).strip("-")


def fipi_key(text) -> str:
    """Нормализует слово и заменяет «ё» на «е» для сопоставления со списком ФИПИ."""
    return normalize_word(text).replace("ё", "е")


# ── Преобразование строки БД в dict ─────────────────────────────────────────

def _row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    d = dict(row)
    d["favorite"] = bool(d.get("favorite", 0))
    return d


# ── Чтение ───────────────────────────────────────────────────────────────────

def get_card(card_id: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    return _row_to_dict(row)


def get_all_cards(
    kind: Optional[str] = None,
    fav: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "created",
) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM cards").fetchall()

    cards = [_row_to_dict(r) for r in rows]

    if kind:
        cards = [c for c in cards if c.get("kind") == kind]

    if fav == "1":
        cards = [c for c in cards if c.get("favorite")]

    if search:
        s = search.lower()
        cards = [
            c for c in cards
            if s in (c.get("word") or "").lower()
            or s in (c.get("stressed") or "").lower()
            or s in (c.get("prompt") or "").lower()
        ]

    sort_keys = {
        "alpha":   lambda c: c.get("word", ""),
        "score":   lambda c: -c.get("score", 0),
        "updated": lambda c: -c.get("updated_at", 0),
    }
    cards.sort(key=sort_keys.get(sort, lambda c: -c.get("created_at", 0)))
    return cards


def get_due_cards() -> list[dict]:
    """Возвращает карточки, которые пора повторить (с учётом режима ЕГЭ)."""
    from backend.domain.dictionary import get_ege_words  # локальный импорт — избегаем цикла

    settings = get_settings()
    n = now_ms()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE due_at <= ? ORDER BY due_at", (n,)
        ).fetchall()

    cards = [_row_to_dict(r) for r in rows]

    if settings.get("examMode"):
        ege = get_ege_words()
        priority = [
            c for c in cards
            if c["kind"] == "punctuation" or normalize_word(c.get("word", "")) in ege
        ]
        if priority:
            return priority

    return cards


def get_favorites() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE favorite = 1 ORDER BY score DESC, updated_at DESC LIMIT 50"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ── Запись (ударения) ────────────────────────────────────────────────────────

def _build_stress_card_params(
    word: str,
    stressed: str,
    source: str,
    note: str,
    favorite: bool,
    existing: dict,
    settings: dict,
    override_note: Optional[str] = None,
) -> tuple:
    """Формирует кортеж параметров для INSERT OR REPLACE карточки ударения."""
    n = now_ms()
    return (
        word, "stress", word, stressed, word, stressed,
        existing.get("rule", ""),
        source,
        override_note if override_note is not None else (note or existing.get("note", "")),
        existing.get("created_at", n),
        n,
        existing.get("interval_minutes", settings["firstIntervalMinutes"]),
        existing.get("ease", settings["defaultEase"]),
        existing.get("reps", 0),
        existing.get("due_at", n + settings["firstIntervalMinutes"] * 60_000),
        existing.get("last_result"),
        1 if (favorite or existing.get("favorite")) else 0,
        existing.get("times_looked_up", 0) + 1,
        existing.get("score", 0),
    )


_STRESS_UPSERT_SQL = """
    INSERT OR REPLACE INTO cards (
        id, kind, word, stressed, prompt, answer, rule, source, note,
        created_at, updated_at, interval_minutes, ease, reps, due_at,
        last_result, favorite, times_looked_up, score
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def upsert_stress_card(
    word: str,
    stressed: str,
    source: str,
    note: str = "",
    favorite: bool = False,
) -> None:
    word = normalize_word(word)
    if not word or not stressed:
        return

    settings = get_settings()
    existing = get_card(word) or {}
    params = _build_stress_card_params(word, stressed, source, note, favorite, existing, settings)

    with get_write_lock():
        with get_db() as conn:
            conn.execute(_STRESS_UPSERT_SQL, params)

    _touch_daily_activity()


def save_stress_override(word: str, stressed: str) -> None:
    """Ручное исправление ударения пользователем (всегда помечается как избранное)."""
    word = normalize_word(word)
    if not word or not stressed:
        return

    settings = get_settings()
    existing = get_card(word) or {}
    params = _build_stress_card_params(
        word, stressed,
        source="manual_override",
        note="",
        favorite=True,
        existing=existing,
        settings=settings,
        override_note="Исправлено вручную.",
    )
    # Перезаписываем favorite на 1 (индекс 16 в кортеже)
    params = params[:16] + (1,) + params[17:]

    with get_write_lock():
        with get_db() as conn:
            conn.execute(_STRESS_UPSERT_SQL, params)


# ── Запись (пунктуация) ──────────────────────────────────────────────────────

_PUNCT_UPSERT_SQL = """
    INSERT OR REPLACE INTO cards (
        id, kind, word, prompt, answer, rule, source, note,
        created_at, updated_at, interval_minutes, ease, reps, due_at,
        last_result, favorite, times_looked_up, score
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def save_punctuation_card(
    original: str,
    corrected: str,
    explanations: list,
    note: str = "",
    favorite: bool = True,
    manual_rule: str = "",
) -> str:
    n = now_ms()
    card_id = f"punct:{original[:100]}"
    manual_rule = (manual_rule or "").strip()
    rule = manual_rule or (" · ".join(explanations) if explanations else (note or "Исправлено вручную"))
    existing = get_card(card_id) or {}
    settings = get_settings()

    params = (
        card_id, "punctuation", original[:80], original, corrected,
        rule, "manual_punctuation", note or "",
        existing.get("created_at", n), n,
        existing.get("interval_minutes", settings["firstIntervalMinutes"]),
        existing.get("ease", settings["defaultEase"]),
        existing.get("reps", 0),
        existing.get("due_at", n + settings["firstIntervalMinutes"] * 60_000),
        existing.get("last_result"),
        1 if (favorite or existing.get("favorite")) else 0,
        existing.get("times_looked_up", 0) + 1,
        existing.get("score", 0),
    )

    with get_write_lock():
        with get_db() as conn:
            conn.execute(_PUNCT_UPSERT_SQL, params)

    _touch_daily_activity()
    return card_id


def search_punctuation_card(text: str) -> Optional[dict]:
    """Ищет сохранённую карточку пунктуации по (примерному) совпадению текста."""
    if not text:
        return None

    def _norm(s: str) -> str:
        s = re.sub(r"[.,!?;:]", "", str(s or "")).strip().lower()
        return re.sub(r"\s+", " ", s)

    needle = _norm(text)
    for card in get_all_cards(kind="punctuation"):
        haystack = _norm(card.get("prompt", ""))
        if haystack == needle or (len(needle) > 5 and (needle in haystack or haystack in needle)):
            return card
    return None


# ── Удаление ─────────────────────────────────────────────────────────────────

def delete_card(card_id: str) -> bool:
    with get_write_lock():
        with get_db() as conn:
            deleted = conn.execute("DELETE FROM cards WHERE id = ?", (card_id,)).rowcount
            conn.execute("DELETE FROM review_history WHERE card_id = ?", (card_id,))
    return deleted > 0


def delete_cards_many(ids: list) -> int:
    return sum(1 for cid in ids if delete_card(cid))


def toggle_favorite(card_id: str, value: bool) -> None:
    with get_write_lock():
        with get_db() as conn:
            conn.execute(
                "UPDATE cards SET favorite = ?, updated_at = ? WHERE id = ?",
                (1 if value else 0, now_ms(), card_id),
            )


# ── Оценка карточки (SRS) ────────────────────────────────────────────────────

def grade_card(card_id: str, result: str) -> dict:
    """Обновляет интервал повторения по упрощённому алгоритму SRS."""
    from backend.domain.gamification import add_xp, damage_boss, touch_daily_activity  # циклический импорт

    settings = get_settings()
    card = get_card(card_id)
    if not card:
        return {"ok": False, "reason": "card_not_found"}

    n = now_ms()
    is_punctuation = card["kind"] == "punctuation"

    if result == "remember":
        reps = card["reps"] + 1
        if reps == 1:
            interval = settings["secondIntervalMinutes"]
        elif reps == 2:
            interval = settings["thirdIntervalMinutes"]
        else:
            interval = round(card["interval_minutes"] * max(1.8, card["ease"]))

        ease  = min(card["ease"] + 0.15, settings["maxEase"])
        score = card["score"] + 3
        fav   = 0
        last  = "remember"
        xp    = 8 if is_punctuation else 6
        dmg   = 55 if is_punctuation else 45
    else:
        reps     = 0
        interval = max(10, round(settings["hardResetMinutes"] / 2))
        ease     = max(card["ease"] - 0.25, settings["minEase"])
        score    = max(0, card["score"] - 2)
        fav      = 1
        last     = "forget"
        xp       = 1
        dmg      = 0

    with get_write_lock():
        with get_db() as conn:
            conn.execute(
                """UPDATE cards
                   SET reps = ?, interval_minutes = ?, ease = ?, last_result = ?,
                       score = ?, favorite = ?, updated_at = ?, due_at = ?
                   WHERE id = ?""",
                (reps, interval, ease, last, score, fav, n, n + interval * 60_000, card_id),
            )
            conn.execute(
                "INSERT INTO review_history (card_id, result, at) VALUES (?, ?, ?)",
                (card_id, result, n),
            )

    add_xp(xp)
    touch_daily_activity()

    # Обновляем статистику профиля
    from backend.domain.gamification import _update_profile_after_grade  # noqa: PLC0415
    _update_profile_after_grade(result, settings)

    boss_variant = "punctuation" if is_punctuation else "stress"
    boss = damage_boss(dmg, boss_variant) if dmg else None
    return {"ok": True, "boss": boss, "damage": dmg}


# ── Вспомогательная функция (избегаем циклических импортов) ──────────────────

def _touch_daily_activity() -> None:
    """Обёртка-прокси, чтобы не импортировать gamification на уровне модуля."""
    from backend.domain.gamification import touch_daily_activity  # noqa: PLC0415
    touch_daily_activity()
