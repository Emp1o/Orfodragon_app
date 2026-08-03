#!/usr/bin/env python3
"""
main.py — точка входа ОрфоДракона.

Запускается напрямую (python main.py) или через PyInstaller-бинарник.
Переменная окружения ORFODRAGON_NO_BROWSER=1 подавляет автооткрытие
браузера (используется при запуске из Electron).
"""

import os
import sys

# Гарантируем, что корень проекта находится в sys.path,
# даже если скрипт запущен из другой директории.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.api.server import run_server

if __name__ == "__main__":
    open_browser = os.environ.get("ORFODRAGON_NO_BROWSER") != "1"
    run_server(open_browser=open_browser)
