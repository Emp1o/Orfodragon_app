#!/usr/bin/env python3
"""ОрфоДракон — запуск приложения."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import run_server
if __name__ == "__main__":
    run_server(open_browser=os.environ.get("ORFODRAGON_NO_BROWSER") != "1")
