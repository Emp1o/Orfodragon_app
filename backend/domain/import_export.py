"""
domain/import_export.py — экспорт и импорт карточек.

Поддерживаемые форматы импорта (TXT):
  - слово|сло́во
  - слово — сло́во
  - исходный текст|текст с запятыми|правило
  - блоки == УДАРЕНИЯ == / == ЗАПЯТЫЕ == (формат нашего экспорта)
"""

import json
from typing import Optional

from backend.db.connection import get_db, get_write_lock
from backend.domain.cards import (
    get_all_cards,
    normalize_word,
    save_punctuation_card,
    upsert_stress_card,
)
from backend.domain.settings import get_app_state, get_settings, set_app_state
from backend.domain.dictionary import get_dictionary, get_ege_words, fipi_key


# ── Экспорт ───────────────────────────────────────────────────────────────────

def export_txt() -> str:
    lines = ["# ОрфоДракон — все карточки", ""]

    stress = get_all_cards(kind="stress")
    punct  = get_all_cards(kind="punctuation")

    if stress:
        lines.append("== УДАРЕНИЯ ==")
        for c in stress:
            lines.append(f"{c.get('word', '')} — {c.get('stressed', '')}")
        lines.append("")

    if punct:
        lines.append("== ЗАПЯТЫЕ ==")
        for c in punct:
            lines.append(f"Исходное:    {c.get('prompt', '')}")
            lines.append(f"С запятыми:  {c.get('answer', '')}")
            if c.get("rule"):
                lines.append(f"Правило:     {c.get('rule', '')}")
            lines.append("")

    return "\n".join(lines)


# ── Импорт TXT ────────────────────────────────────────────────────────────────

def import_txt(text: str) -> int:
    count = 0
    mode: Optional[str] = None

    pending_original: Optional[str] = None
    pending_answer:   Optional[str] = None
    pending_rule:     Optional[str] = None

    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        upper = line.upper()
        if upper.startswith("== УДАРЕНИЯ"):
            mode = "stress"
            continue
        if upper.startswith("== ЗАПЯТЫЕ"):
            mode = "punct"
            continue

        # Многострочный блок пунктуации (формат нашего экспорта)
        if line.lower().startswith("исходное:"):
            pending_original = line.split(":", 1)[1].strip()
            continue
        if line.lower().startswith("с запятыми:"):
            pending_answer = line.split(":", 1)[1].strip()
            continue
        if line.lower().startswith("правило:"):
            pending_rule = line.split(":", 1)[1].strip()
            if pending_original and pending_answer:
                save_punctuation_card(
                    pending_original, pending_answer,
                    [pending_rule or "Импорт TXT"], "Импорт TXT", True,
                )
                count += 1
            pending_original = pending_answer = pending_rule = None
            continue

        parts = [p.strip() for p in line.split("|")]

        if len(parts) >= 3:
            save_punctuation_card(parts[0], parts[1], [parts[2]], "Импорт TXT", True)
            count += 1
            continue

        if len(parts) == 2:
            is_sentence = " " in parts[0] or " " in parts[1] or "," in parts[1]
            if is_sentence:
                save_punctuation_card(parts[0], parts[1], ["Импорт TXT"], "Импорт TXT", True)
            else:
                upsert_stress_card(parts[0], parts[1], "import", "Импорт TXT", False)
            count += 1
            continue

        if " — " in line:
            word, stressed = line.split(" — ", 1)
            upsert_stress_card(word, stressed, "import", "Импорт TXT", False)
            count += 1

    return count


# ── Импорт JSON (обратная совместимость) ─────────────────────────────────────

def import_json_legacy(data_str: str) -> int:
    try:
        data  = json.loads(data_str)
        cards = data.get("cards", data) if isinstance(data, dict) else data
        if isinstance(cards, dict):
            cards = list(cards.values())
        if not isinstance(cards, list):
            return 0
    except (json.JSONDecodeError, TypeError):
        return 0

    settings = get_settings()
    from backend.domain.cards import now_ms  # noqa: PLC0415
    n = now_ms()

    sql = """
        INSERT OR REPLACE INTO cards (
            id, kind, word, stressed, prompt, answer, rule, source, note,
            created_at, updated_at, interval_minutes, ease, reps, due_at,
            last_result, favorite, times_looked_up, score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    count = 0
    with get_write_lock():
        with get_db() as conn:
            for item in cards:
                if not isinstance(item, dict):
                    continue
                cid = str(item.get("id", item.get("word", f"imp-{n}-{count}")))
                conn.execute(sql, (
                    cid,
                    item.get("kind", "stress"),
                    item.get("word", cid),
                    item.get("stressed", item.get("answer", "")),
                    item.get("prompt",  item.get("word", "")),
                    item.get("answer",  item.get("stressed", "")),
                    item.get("rule",    ""),
                    item.get("source",  "import"),
                    item.get("note",    ""),
                    item.get("createdAt", n),
                    n,
                    int(item.get("intervalMinutes", settings["firstIntervalMinutes"])),
                    float(item.get("ease", settings["defaultEase"])),
                    int(item.get("reps", 0)),
                    n,
                    item.get("lastResult"),
                    1 if item.get("favorite") else 0,
                    int(item.get("timesLookedUp", 0)),
                    int(item.get("score", 0)),
                ))
                count += 1
    return count


# ── Инициализация стартовых карточек ─────────────────────────────────────────

def initialize_default_cards() -> int:
    """
    Создаёт карточки для всех слов из списка ФИПИ при первом запуске.
    Повторная инициализация пропускается через флаг в app_state.
    """
    if get_app_state("defaultCardsInitializedV2"):
        return 0

    dictionary = get_dictionary()
    ege        = sorted(get_ege_words())
    settings   = get_settings()
    from backend.domain.cards import now_ms as _now  # noqa: PLC0415
    n = _now()

    sql = """
        INSERT INTO cards (
            id, kind, word, stressed, prompt, answer, source, note,
            created_at, updated_at, interval_minutes, ease, reps, due_at,
            last_result, favorite, times_looked_up, score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    added = 0
    with get_write_lock():
        with get_db() as conn:
            for key in ege:
                stressed = dictionary.get(key)
                if not stressed:
                    continue
                if conn.execute("SELECT id FROM cards WHERE id = ?", (key,)).fetchone():
                    continue
                conn.execute(sql, (
                    key, "stress", key, stressed, key, stressed,
                    "fipi_dictionary", "Слово из списка ФИПИ.",
                    n, n,
                    settings["firstIntervalMinutes"],
                    settings["defaultEase"],
                    0,
                    n + settings["firstIntervalMinutes"] * 60_000,
                    None, 1, 0, 0,
                ))
                added += 1

    set_app_state("defaultCardsInitializedV2", True)
    return added
