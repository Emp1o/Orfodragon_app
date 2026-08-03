"""
config.py — глобальные константы и пути.

Все пути вычисляются один раз при импорте модуля; остальной код
импортирует их отсюда и не занимается определением платформы.
"""

import os
import sys
from pathlib import Path

# ── Корень проекта ──────────────────────────────────────────────────────────
# backend/ находится внутри корня приложения, поэтому .parent поднимается
# на уровень выше к orfodragon/
APP_DIR: Path = Path(__file__).parent.parent.resolve()

DATA_DIR:   Path = APP_DIR / "data"
STATIC_DIR: Path = APP_DIR / "static"
ICONS_DIR:  Path = APP_DIR / "icons"

# ── Пользовательские данные (зависит от ОС) ─────────────────────────────────
def _resolve_user_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "OrfoDragon"


USER_DATA_DIR: Path = _resolve_user_data_dir()
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

PORT_FILE: Path = USER_DATA_DIR / "port.txt"
DB_PATH:   Path = USER_DATA_DIR / "orfodragon.db"

# ── Сеть ─────────────────────────────────────────────────────────────────────
DEFAULT_PORT = 17_832
PORT_SCAN_ATTEMPTS = 20

# ── Приложение ───────────────────────────────────────────────────────────────
APP_VERSION = "2026.05.14-refactored"

# ── Лингвистика ──────────────────────────────────────────────────────────────
VOWELS = "аеёиоуыэюя"
