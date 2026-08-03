"""
domain/ege.py — режим пробного экзамена ЕГЭ.

Сессия и результат хранятся в памяти (не в БД): экзамен временный,
при перезапуске приложения он сбрасывается — это ожидаемое поведение.
"""

import re
import random
from typing import Optional

from backend.config import VOWELS
from backend.domain.cards import get_all_cards, now_ms
from backend.domain.dictionary import add_accent
from backend.domain.gamification import add_xp, damage_boss


# ── Состояние сессии (module-level singleton) ────────────────────────────────

_active_session: Optional[dict] = None
_last_result:    Optional[dict] = None


# ── Построение вариантов ответа ──────────────────────────────────────────────

def _build_stress_options(stressed: str) -> list[str]:
    """Генерирует 2–4 варианта ударения (правильный + неправильные)."""
    clean     = re.sub(r"\u0301", "", stressed)
    positions = [i for i, ch in enumerate(clean) if ch in VOWELS]
    variants  = {stressed} | {add_accent(clean, p) for p in positions}
    options   = [v for v in variants if v]
    random.shuffle(options)
    return options[: min(4, max(2, len(options)))]


def _build_punct_options(correct: str, prompt: str) -> list[str]:
    """Генерирует 2–4 варианта расстановки запятых."""

    def normalize(s: str) -> str:
        s = re.sub(r"\s+,", ",",  str(s or "").strip())
        s = re.sub(r",\s*", ", ", s)
        s = re.sub(r"\s{2,}", " ", s).strip()
        return re.sub(r"[.!?]+$", "", s).strip()

    def strip_commas(text: str) -> str:
        return normalize(re.sub(r",", "", str(text or "")))

    def inject_comma_at(text: str, pos: int) -> str:
        words = strip_commas(text).split()
        if len(words) < 3 or pos <= 0 or pos >= len(words):
            return ""
        words[pos - 1] += ","
        return normalize(" ".join(words))

    def comma_before_word(text: str, word: str) -> str:
        clean = strip_commas(text)
        pat   = re.compile(r"\s+(" + re.escape(word) + r")\b", re.IGNORECASE)
        return normalize(pat.sub(r", \1", clean)) if pat.search(clean) else ""

    right = str(correct or "").strip()
    base  = str(prompt or "").strip()
    rn    = normalize(right)

    candidates: set[str] = set()
    candidates.add(strip_commas(right))  # вариант без запятых

    conjunctions = ["что", "чтобы", "если", "когда", "хотя", "но", "а",
                    "потому что", "так как"]
    for w in conjunctions:
        v = comma_before_word(base, w)
        if v and v != rn:
            candidates.add(v)

    words = strip_commas(base).split()
    for i in range(1, min(len(words), 6)):
        v = inject_comma_at(base, i)
        if v and v != rn:
            candidates.add(v)

    candidates.discard("")
    candidates.discard(rn)

    wrong   = list(candidates)
    random.shuffle(wrong)
    options = [rn] + wrong[:3]
    random.shuffle(options)
    return options[:4] if len(options) >= 2 else (options or [rn])


def _infer_topic(question: dict) -> str:
    if question.get("kind") == "stress":
        return "Ударения"
    prompt = (question.get("prompt") or "").lower()
    if re.search(r"потому что|так как|чтобы|если|когда|хотя|который", prompt):
        return "Сложноподчинённые предложения"
    if re.search(r"\bно\b|\bа\b|однако|зато", prompt):
        return "Противительные союзы"
    return "Пунктуация"


# ── Создание / получение сессии ───────────────────────────────────────────────

def create_exam() -> dict:
    global _active_session

    cards   = get_all_cards()
    stress  = [c for c in cards if c.get("kind") == "stress"       and c.get("stressed")]
    punct   = [c for c in cards if c.get("kind") == "punctuation"  and c.get("answer")]

    random.shuffle(stress)
    random.shuffle(punct)

    questions: list[dict] = []

    for card in stress[:5]:
        options = _build_stress_options(card["stressed"])
        if len(options) < 2:
            continue
        questions.append({
            "id":          f"s:{card['id']}",
            "kind":        "stress",
            "prompt":      card["word"],
            "question":    "Укажите правильный вариант ударения",
            "options":     options,
            "correct":     card["stressed"],
            "explanation": card.get("note", "Словарное ударение."),
            "topic":       "Ударения",
        })

    for card in punct[:5]:
        options = _build_punct_options(card["answer"], card.get("prompt", ""))
        if len(options) < 2:
            continue
        q = {
            "id":          f"p:{card['id']}",
            "kind":        "punctuation",
            "prompt":      card.get("prompt", card.get("word", "")),
            "question":    "Выберите вариант с правильной расстановкой запятых",
            "options":     options,
            "correct":     card["answer"],
            "explanation": card.get("rule", "Запятые расставлены по правилам пунктуации."),
        }
        q["topic"] = _infer_topic(q)
        questions.append(q)

    random.shuffle(questions)
    questions = questions[:10]

    _active_session = {
        "questions":       questions,
        "createdAt":       now_ms(),
        "startedAt":       now_ms(),
        "durationSeconds": 600,
        "submittedAt":     None,
    }
    return _active_session


def get_session() -> dict:
    global _active_session
    if not _active_session:
        _active_session = create_exam()
    return _active_session


# ── Нормализация ответа для проверки ─────────────────────────────────────────

def _normalize_answer(s: str) -> str:
    s = re.sub(r",\s*", ", ", str(s or "").strip().rstrip("."))
    return re.sub(r"\s+", " ", s).replace("ё", "е").lower().strip()


# ── Сдача экзамена ────────────────────────────────────────────────────────────

def submit_exam(answers: list, expired: bool = False) -> dict:
    global _active_session, _last_result

    session   = get_session()
    questions = session.get("questions", [])
    correct   = 0
    results   = []

    for i, q in enumerate(questions):
        user_answer = answers[i] if i < len(answers) else ""
        is_correct  = _normalize_answer(user_answer) == _normalize_answer(q.get("correct", ""))
        if is_correct:
            correct += 1
        results.append({**q, "userAnswer": user_answer, "isCorrect": is_correct})

    total    = len(questions)
    percent  = round(correct / total * 100) if total else 0
    mark_map = [(90, "5 — отлично"), (70, "4 — хорошо"),
                (50, "3 — удовлетворительно"), (0, "2 — нужно ещё тренироваться")]
    mark     = next(m for threshold, m in mark_map if percent >= threshold)

    xp_gained = correct * 12 + (30 if percent >= 80 else 15 if percent >= 60 else 0)
    add_xp(xp_gained)

    boss_damage = max(20, xp_gained * 2)
    boss        = damage_boss(boss_damage, "mixed")

    _last_result = {
        "questions":   results,
        "total":       total,
        "correct":     correct,
        "percent":     percent,
        "mark":        mark,
        "xpGained":    xp_gained,
        "bossDamage":  boss_damage,
        "boss":        boss,
        "expired":     expired,
    }
    _active_session = None
    return _last_result


def get_result() -> Optional[dict]:
    return _last_result
