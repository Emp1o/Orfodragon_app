"""
db/connection.py — управление соединением с SQLite и схемой БД.

Все остальные модули получают соединение через контекстный менеджер
`get_db()` и никогда не открывают sqlite3.connect() напрямую.
"""

import contextlib
import sqlite3
import threading
from typing import Generator

from backend.config import DB_PATH

# Единственный мьютекс для операций записи; читать можно без него,
# потому что SQLite в режиме WAL поддерживает параллельные чтения.
# Но мы используем journal_mode=DELETE (совместимость), поэтому
# блокируем и запись, и повторные чтения при наличии активной записи.
_write_lock = threading.Lock()


@contextlib.contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Открывает соединение с БД, коммитит при выходе, всегда закрывает."""
    conn = sqlite3.connect(str(DB_PATH), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = DELETE")
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_write_lock() -> threading.Lock:
    """Возвращает глобальный мьютекс записи."""
    return _write_lock


def init_schema() -> None:
    """Создаёт таблицы, если они ещё не существуют."""
    ddl = """
    CREATE TABLE IF NOT EXISTS cards (
        id                  TEXT PRIMARY KEY,
        kind                TEXT    DEFAULT 'stress',
        word                TEXT,
        stressed            TEXT,
        prompt              TEXT,
        answer              TEXT,
        rule                TEXT    DEFAULT '',
        source              TEXT    DEFAULT '',
        note                TEXT    DEFAULT '',
        created_at          INTEGER,
        updated_at          INTEGER,
        interval_minutes    INTEGER DEFAULT 15,
        ease                REAL    DEFAULT 2.2,
        reps                INTEGER DEFAULT 0,
        due_at              INTEGER,
        last_result         TEXT,
        favorite            INTEGER DEFAULT 0,
        times_looked_up     INTEGER DEFAULT 0,
        score               INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS review_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        card_id     TEXT,
        result      TEXT,
        at          INTEGER,
        FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS settings  (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS profile   (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY, value TEXT);
    """
    with get_db() as conn:
        conn.executescript(ddl)
