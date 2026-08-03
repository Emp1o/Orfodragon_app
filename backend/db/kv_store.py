"""
db/kv_store.py — обобщённое key-value хранилище поверх SQLite.

Используется для таблиц settings, profile и app_state — у всех
одинаковая структура (key TEXT, value TEXT с JSON-сериализацией).
"""

import json
from typing import Any

from backend.db.connection import get_db, get_write_lock


def load(table: str, defaults: dict) -> dict:
    """Загружает все пары ключ-значение из таблицы, возвращает dict с defaults."""
    result = dict(defaults)
    try:
        with get_db() as conn:
            for row in conn.execute(f"SELECT key, value FROM {table}").fetchall():
                try:
                    result[row["key"]] = json.loads(row["value"])
                except (json.JSONDecodeError, TypeError):
                    result[row["key"]] = row["value"]
    except Exception:
        pass
    return result


def save(table: str, data: dict) -> None:
    """Записывает словарь data в таблицу; существующие ключи перезаписываются."""
    with get_write_lock():
        with get_db() as conn:
            conn.executemany(
                f"INSERT OR REPLACE INTO {table} (key, value) VALUES (?, ?)",
                [(k, json.dumps(v, ensure_ascii=False)) for k, v in data.items()],
            )


def get_one(table: str, key: str, default: Any = None) -> Any:
    """Читает одно значение по ключу."""
    try:
        with get_db() as conn:
            row = conn.execute(
                f"SELECT value FROM {table} WHERE key = ?", (key,)
            ).fetchone()
            return json.loads(row["value"]) if row else default
    except Exception:
        return default


def set_one(table: str, key: str, value: Any) -> None:
    """Записывает одно значение по ключу."""
    save(table, {key: value})
