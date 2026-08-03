"""
api/server.py — HTTP-сервер и маршрутизация запросов.

Архитектура намеренно минималистична: stdlib http.server без внешних
зависимостей. Все роуты описаны в компактных таблицах GET_ROUTES и
POST_ROUTES, что упрощает навигацию и добавление новых эндпоинтов.
"""

import http.server
import json
import socket
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Callable

from backend.config import (
    APP_VERSION,
    DB_PATH,
    DEFAULT_PORT,
    ICONS_DIR,
    PORT_FILE,
    PORT_SCAN_ATTEMPTS,
    STATIC_DIR,
)
from backend.db.connection import init_schema
from backend.domain.cards import (
    delete_card,
    delete_cards_many,
    get_all_cards,
    get_due_cards,
    get_favorites,
    grade_card,
    normalize_word,
    search_punctuation_card,
    toggle_favorite,
    upsert_stress_card,
    save_stress_override,
)
from backend.domain.dictionary import search_stress
from backend.domain.ege import create_exam, get_result, get_session, submit_exam
from backend.domain.gamification import claim_daily_chest, set_mascot_skin
from backend.domain.import_export import (
    export_txt,
    import_json_legacy,
    import_txt,
    initialize_default_cards,
)
from backend.domain.punctuation import local_punctuate
from backend.domain.settings import get_profile, get_settings, save_settings
from backend.domain.state import get_snapshot
from backend.domain.training import build_session as build_training_session


# ── Статический файловый кэш ──────────────────────────────────────────────────
# Файлы небольшие (иконки, один HTML), кэшируем целиком в памяти.
_static_cache: dict[str, bytes] = {}


# ── Вспомогательные функции HTTP ─────────────────────────────────────────────

def _send_json(handler: http.server.BaseHTTPRequestHandler, data: dict, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type",   "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(body)


def _send_file(handler: http.server.BaseHTTPRequestHandler, path: Path) -> None:
    resolved = path.resolve()
    if not resolved.exists():
        handler.send_error(404)
        return

    content_types = {
        ".html": "text/html; charset=utf-8",
        ".css":  "text/css",
        ".js":   "application/javascript",
        ".svg":  "image/svg+xml",
        ".png":  "image/png",
        ".ico":  "image/x-icon",
        ".json": "application/json",
    }
    content_type = content_types.get(resolved.suffix.lower(), "application/octet-stream")

    cache_key = str(resolved)
    if cache_key not in _static_cache:
        _static_cache[cache_key] = resolved.read_bytes()
    data = _static_cache[cache_key]

    handler.send_response(200)
    handler.send_header("Content-Type",   content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control",  "max-age=3600")
    handler.send_header("Connection",     "close")
    handler.end_headers()
    handler.wfile.write(data)
    handler.close_connection = True


def _read_body(handler: http.server.BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _qs(handler: http.server.BaseHTTPRequestHandler) -> dict:
    """Возвращает query-string параметры в виде dict {key: first_value}."""
    parsed = urllib.parse.urlparse(handler.path)
    return {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items() if v}


# ── Обработчик запросов ───────────────────────────────────────────────────────

class _RequestHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, *args) -> None:
        """Отключаем стандартный лог — в продакшне он засоряет вывод."""

    # ── CORS preflight ───────────────────────────────────────────────────────

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── GET ──────────────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        try:
            self._handle_get()
        except Exception as exc:
            print(f"[GET] {self.path}: {exc}")
            try:
                self.send_error(500, str(exc))
            except Exception:
                pass

    def _handle_get(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path
        qs     = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items() if v}

        # ── Статика ──────────────────────────────────────────────────────────
        if path in ("/", "/index.html"):
            return _send_file(self, STATIC_DIR / "index.html")
        if path.startswith("/icons/"):
            return _send_file(self, ICONS_DIR / path[7:])
        if path.startswith("/static/"):
            return _send_file(self, STATIC_DIR / path[8:])

        # ── API ───────────────────────────────────────────────────────────────
        if path == "/api/health":
            return _send_json(self, {"ok": True, "appVersion": APP_VERSION, "dbPath": str(DB_PATH)})

        if path == "/api/state":
            return _send_json(self, get_snapshot())

        if path == "/api/stats":
            return _send_json(self, get_snapshot()["stats"])

        if path == "/api/settings":
            return _send_json(self, get_settings())

        if path == "/api/profile":
            return _send_json(self, get_profile())

        if path == "/api/search":
            return _send_json(self, search_stress(qs.get("q", ""), qs.get("save") == "1"))

        if path == "/api/cards":
            return _send_json(self, get_all_cards(
                qs.get("kind"),
                qs.get("favorite"),
                qs.get("search"),
                qs.get("sort", "created"),
            ))

        if path == "/api/due":
            return _send_json(self, get_due_cards())

        if path == "/api/cards/due":
            due = get_due_cards()
            return _send_json(self, {"count": len(due), "cards": due[:20]})

        if path == "/api/favorites":
            return _send_json(self, get_favorites())

        if path == "/api/ege/session":
            return _send_json(self, get_session())

        if path == "/api/ege/result":
            return _send_json(self, get_result() or {})

        if path == "/api/punctuate/lookup":
            card = search_punctuation_card(qs.get("text", ""))
            return _send_json(self, {"found": card is not None, "card": card})

        if path == "/api/export/txt":
            data = export_txt().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type",        "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="orfodragon_cards.txt"')
            self.send_header("Content-Length",      str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        # ── Фолбэк: SPA (index.html) ─────────────────────────────────────────
        index = STATIC_DIR / "index.html"
        if index.exists():
            return _send_file(self, index)
        self.send_error(404)

    # ── POST ─────────────────────────────────────────────────────────────────

    def do_POST(self) -> None:
        try:
            self._handle_post()
        except Exception as exc:
            print(f"[POST] {self.path}: {exc}")
            try:
                self.send_error(500, str(exc))
            except Exception:
                pass

    def _handle_post(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        body = _read_body(self)

        if path == "/api/search/save":
            upsert_stress_card(
                normalize_word(body.get("word", "")),
                body.get("stressed", ""),
                body.get("source", "search"),
                body.get("note", ""),
                favorite=True,
            )
            return _send_json(self, {"ok": True})

        if path == "/api/punctuate":
            return _send_json(self, local_punctuate(body.get("text", "")))

        if path == "/api/punctuate/save":
            from backend.domain.cards import save_punctuation_card  # noqa: PLC0415
            cid = save_punctuation_card(
                body.get("original", ""),
                body.get("corrected", ""),
                body.get("explanations", []),
                body.get("note", ""),
                body.get("favorite", True),
                body.get("manualRule", ""),
            )
            return _send_json(self, {"ok": True, "id": cid})

        if path == "/api/cards/grade":
            return _send_json(self, grade_card(
                body.get("id", body.get("word", "")),
                body.get("result", ""),
            ))

        if path == "/api/cards/favorite":
            toggle_favorite(body.get("id", ""), bool(body.get("value", False)))
            return _send_json(self, {"ok": True})

        if path == "/api/cards/delete":
            return _send_json(self, {"ok": delete_card(body.get("id", ""))})

        if path == "/api/cards/delete_many":
            return _send_json(self, {"ok": True, "deleted": delete_cards_many(body.get("ids", []))})

        if path == "/api/cards/stress":
            upsert_stress_card(
                normalize_word(body.get("word", "")),
                body.get("stressed", ""),
                body.get("source", "manual"),
                body.get("note", ""),
                body.get("favorite", True),
            )
            return _send_json(self, {"ok": True})

        if path == "/api/cards/stress_override":
            save_stress_override(body.get("word", ""), body.get("stressed", ""))
            return _send_json(self, {"ok": True})

        if path == "/api/settings/save":
            save_settings(body)
            return _send_json(self, {"ok": True})

        if path == "/api/chest/claim":
            return _send_json(self, claim_daily_chest())

        if path == "/api/mascot/skin":
            return _send_json(self, set_mascot_skin(body.get("skin", "dragon_king")))

        if path == "/api/training/start":
            return _send_json(self, build_training_session(
                body.get("mode", "all"),
                int(body.get("count", 10)),
            ))

        if path == "/api/ege/create":
            return _send_json(self, create_exam())

        if path == "/api/ege/submit":
            return _send_json(self, submit_exam(
                body.get("answers", []),
                body.get("expired", False),
            ))

        # Два алиаса — оставлены для обратной совместимости
        if path in ("/api/import/txt", "/api/import_commas"):
            text = body.get("data", body.get("text", ""))
            return _send_json(self, {"ok": True, "imported": import_txt(text)})

        if path == "/api/import/json_legacy":
            try:
                count = import_json_legacy(body.get("data", ""))
                return _send_json(self, {"ok": True, "imported": count})
            except Exception as exc:
                return _send_json(self, {"ok": False, "imported": 0, "error": str(exc)})

        self.send_error(404, f"Unknown endpoint: {path}")


# ── Обнаружение и запуск сервера ──────────────────────────────────────────────

def _is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _find_free_port(start: int = DEFAULT_PORT, attempts: int = PORT_SCAN_ATTEMPTS) -> int | None:
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return None


def run_server(port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    init_schema()
    initialize_default_cards()

    if _is_port_in_use(port):
        url = f"http://127.0.0.1:{port}"
        print(f"ОрфоДракон уже запущен: {url}")
        if open_browser:
            webbrowser.open(url)
        return

    actual_port = _find_free_port(port)
    if actual_port is None:
        print("Ошибка: не удалось найти свободный порт.")
        return

    if actual_port != port:
        print(f"Порт {port} занят, использую {actual_port}.")

    try:
        PORT_FILE.write_text(str(actual_port))
    except OSError:
        pass  # не критично

    server = http.server.ThreadingHTTPServer(("127.0.0.1", actual_port), _RequestHandler)
    print(f"ОрфоДракон: http://127.0.0.1:{actual_port}")

    if open_browser:
        def _open_browser() -> None:
            time.sleep(0.8)
            webbrowser.open(f"http://127.0.0.1:{actual_port}")
        threading.Thread(target=_open_browser, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлен.")
        server.shutdown()
    finally:
        try:
            PORT_FILE.unlink()
        except OSError:
            pass
