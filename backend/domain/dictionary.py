"""
domain/dictionary.py — локальный словарь ударений и функции поиска.

Словарь загружается один раз и кэшируется в памяти процесса.
"""

import json
import re
from functools import lru_cache
from typing import Optional

from backend.config import DATA_DIR, VOWELS
from backend.domain.cards import (
    fipi_key,
    normalize_word,
    get_card,
    get_all_cards,
    upsert_stress_card,
)


def add_accent(word: str, vowel_index: int) -> str:
    """Вставляет комбинирующий знак ударения (U+0301) после гласной."""
    if vowel_index < 0 or vowel_index >= len(word):
        return word
    # Не добавляем повторно, если знак уже стоит
    if len(word) > vowel_index + 1 and word[vowel_index + 1] == "\u0301":
        return word
    return word[: vowel_index + 1] + "\u0301" + word[vowel_index + 1 :]


# ── Ленивая загрузка данных ──────────────────────────────────────────────────
# lru_cache(1) — аналог singleton: функция вычисляется один раз,
# результат сохраняется навсегда (пока живёт процесс).

@lru_cache(maxsize=1)
def get_ege_words() -> frozenset:
    """Возвращает frozenset нормализованных слов из списка ФИПИ."""
    path = DATA_DIR / "ege_words.json"
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
        return frozenset(fipi_key(x) for x in items if x)
    except Exception:
        return frozenset()


@lru_cache(maxsize=1)
def get_dictionary() -> dict:
    """
    Возвращает dict {нормализованное_слово: «сло́во»}.

    В словарь попадают только слова, присутствующие в списке ФИПИ.
    Кроме того, добавляется несколько ручных исправлений для слов,
    у которых ФИПИ опускает «ё».
    """
    path = DATA_DIR / "local_dictionary.json"
    try:
        raw: dict = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raw = {}

    ege = get_ege_words()
    result: dict = {}

    for word, stressed in raw.items():
        if fipi_key(word) in ege:
            result[normalize_word(word)] = stressed
            result[fipi_key(word)] = stressed

    # Ручные алиасы для слов с «ё», которые ФИПИ пишет без неё
    manual_aliases = {
        "договоренность": "догово́ренность",
        "шофер":          "шофёр",
        "щелкать":        "щёлкать",
    }
    for key, value in manual_aliases.items():
        if key in ege:
            result[key] = value

    return result


# ── Определение ударения ─────────────────────────────────────────────────────

def resolve_stress(word: str) -> dict:
    """
    Ищет ударение для слова в следующем порядке:
    1. Точное совпадение в словаре ФИПИ.
    2. Совпадение после нормализации ё→е.
    3. Эвристика: ударение на первую/последнюю гласную (ненадёжно).
    """
    n = normalize_word(word)
    if not n:
        return {"word": word, "stressed": word, "source": "passthrough",
                "fallback": True, "note": ""}

    dictionary = get_dictionary()

    lookup_key = n if n in dictionary else fipi_key(n)
    if lookup_key in dictionary:
        return {
            "word": n, "stressed": dictionary[lookup_key],
            "source": "local_dictionary", "fallback": False,
            "note": "Из локального словаря ФИПИ.",
        }

    vowel_positions = [i for i, ch in enumerate(n) if ch in VOWELS]
    if not vowel_positions:
        return {"word": n, "stressed": n, "source": "fallback_passthrough",
                "fallback": True, "note": "Ударение не определено."}

    # Одна гласная → она ударная; несколько → последняя (грубая эвристика)
    pos = vowel_positions[0] if len(vowel_positions) == 1 else vowel_positions[-1]
    return {
        "word": n, "stressed": add_accent(n, pos),
        "source": "extension_fallback", "fallback": True,
        "note": "Ударение может стоять неправильно, обязательно перепроверяйте.",
    }


def search_stress(query: str, save: bool = False) -> dict:
    """
    Полный поиск ударения: карточки → словарь → эвристика.
    Если save=True, найденный результат сохраняется как карточка.
    """
    n = normalize_word(query)
    if not n:
        return {"normalizedQuery": "", "exact": None, "results": []}

    dictionary = get_dictionary()
    results: list = []
    exact: Optional[dict] = None

    # 1. Ищем в пользовательских карточках
    card = get_card(n)
    if card:
        exact = {**card, "location": "cards"}
        results.append(exact)

    # 2. Ищем в локальном словаре
    lookup_key = n if n in dictionary else fipi_key(n)
    if lookup_key in dictionary:
        item = {
            "id": n, "word": n, "stressed": dictionary[lookup_key],
            "source": "local_dictionary", "location": "dictionary",
            "fallback": False, "note": "Из локального словаря ФИПИ.",
        }
        if not exact:
            exact = item
        results.append(item)

    # 3. Если ничего не нашли — эвристика
    if not exact:
        resolved = resolve_stress(n)
        exact = {"id": n, **resolved, "location": "resolved"}
        results.append(exact)
        if save and resolved.get("stressed"):
            upsert_stress_card(n, resolved["stressed"], resolved["source"],
                               resolved.get("note", ""), False)

    # 4. Похожие карточки из БД
    with __import__("backend.db.connection", fromlist=["get_db"]).get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE kind = 'stress' AND id != ? AND id LIKE ? LIMIT 10",
            (n, f"%{n}%"),
        ).fetchall()

    for row in rows:
        d = dict(row)
        d["favorite"] = bool(d.get("favorite", 0))
        results.append({**d, "location": "cards"})

    return {"normalizedQuery": n, "exact": exact, "results": results[:20]}
