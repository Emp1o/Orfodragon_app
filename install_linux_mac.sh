#!/bin/bash
# ОрфоДракон — установщик для Linux и macOS
set -e
cd "$(dirname "$0")"
APP_DIR="$(pwd)"

echo "============================================"
echo "   ОрфоДракон — Установка"
echo "============================================"
echo ""

# ─── Ищем Python 3 ──────────────────────────────────────────────
PYTHON=""
PYTHON_FULL=""
for p in python3.12 python3.11 python3.10 python3.9 python3 python; do
    if command -v "$p" &>/dev/null; then
        MAJOR=$("$p" -c "import sys; print(sys.version_info.major)" 2>/dev/null)
        MINOR=$("$p" -c "import sys; print(sys.version_info.minor)" 2>/dev/null)
        if [ "$MAJOR" = "3" ] && [ -n "$MINOR" ] && [ "$MINOR" -ge 8 ] 2>/dev/null; then
            PYTHON="$p"
            PYTHON_FULL=$(command -v "$p")
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "[ОШИБКА] Python 3.8+ не найден!"
    echo ""
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "Установите Python:"
        echo "  Скачайте: https://www.python.org/downloads/macos/"
        echo "  или через Homebrew: brew install python3"
        printf "Открыть страницу загрузки? [д/н]: "
        read -r ans
        if [[ "$ans" =~ ^[ДдYy] ]]; then
            open "https://www.python.org/downloads/macos/"
        fi
    else
        echo "  Ubuntu/Debian: sudo apt install python3"
        echo "  Fedora:        sudo dnf install python3"
        echo "  Arch:          sudo pacman -S python"
    fi
    exit 1
fi

echo "[OK] Python: $($PYTHON --version) ($PYTHON_FULL)"
$PYTHON -c "import http.server, json, sqlite3, re, threading, webbrowser; print('[OK] Все модули работают')"
echo ""

chmod +x "$APP_DIR/main.py" 2>/dev/null || true

# ════════════════════════════════════════════════════════════════
# macOS
# ════════════════════════════════════════════════════════════════
if [[ "$OSTYPE" == "darwin"* ]]; then

    APP_PATH="$HOME/Desktop/ОрфоДракон.app"
    rm -rf "$APP_PATH"
    mkdir -p "$APP_PATH/Contents/MacOS"
    mkdir -p "$APP_PATH/Contents/Resources"

    echo "Создаём иконку приложения..."

    # Генерируем .icns из нашего PNG через Python
    "$PYTHON" - "$APP_DIR/icons/dragon_icon.png" "$APP_PATH/Contents/Resources/dragon.icns" <<'PYEOF'
import sys, struct, os

src = sys.argv[1]
dst = sys.argv[2]

if not os.path.exists(src):
    print("  PNG не найден, продолжаем без иконки")
    sys.exit(0)

with open(src, "rb") as f:
    png_data = f.read()

# ICNS: заголовок + блоки PNG для разных размеров
# macOS 10.5+ поддерживает PNG внутри ICNS
def icns_block(tag, data):
    return tag + struct.pack(">I", 8 + len(data)) + data

blocks = b""
for tag in [b"ic07", b"ic08", b"ic09", b"ic10"]:
    blocks += icns_block(tag, png_data)

icns = b"icns" + struct.pack(">I", 8 + len(blocks)) + blocks

with open(dst, "wb") as f:
    f.write(icns)

print(f"  [OK] Иконка dragon.icns создана ({len(icns)} байт)")
PYEOF

    # Info.plist
    cat > "$APP_PATH/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>ОрфоДракон</string>
    <key>CFBundleDisplayName</key><string>ОрфоДракон</string>
    <key>CFBundleIdentifier</key><string>ru.orfodragon.app</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleExecutable</key><string>launcher</string>
    <key>CFBundleIconFile</key><string>dragon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>LSMinimumSystemVersion</key><string>10.13</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSAppleScriptEnabled</key><false/>
</dict>
</plist>
PLIST

    # Исполняемый файл — запускает Python в фоне, затем открывает браузер
    # Имя должно совпадать с CFBundleExecutable в Info.plist
    cat > "$APP_PATH/Contents/MacOS/launcher" <<LAUNCHER
#!/bin/bash
MAIN_PY="$APP_DIR/main.py"
DEFAULT_PORT=17832

# Читаем фактический порт из файла (на случай если основной занят)
get_port() {
    local port_file
    # macOS: ~/Library/Application Support/OrfoDragon/port.txt
    port_file="\$HOME/Library/Application Support/OrfoDragon/port.txt"
    if [ -f "\$port_file" ]; then
        cat "\$port_file" 2>/dev/null || echo "\$DEFAULT_PORT"
    else
        echo "\$DEFAULT_PORT"
    fi
}

# Проверяем что сервер ещё не запущен
CURRENT_PORT=\$(get_port)
if curl -s "http://127.0.0.1:\$CURRENT_PORT/api/health" >/dev/null 2>&1; then
    # Уже запущен — просто открываем браузер
    open "http://127.0.0.1:\$CURRENT_PORT"
    exit 0
fi

# Запускаем сервер в фоне (без терминала)
ORFODRAGON_NO_BROWSER=1 nohup "$PYTHON_FULL" "\$MAIN_PY" > /tmp/orfodragon.log 2>&1 &

# Ждём запуска (максимум 8 секунд), проверяем оба порта
for i in 1 2 3 4 5 6 7 8; do
    sleep 1
    CURRENT_PORT=\$(get_port)
    curl -s "http://127.0.0.1:\$CURRENT_PORT/api/health" >/dev/null 2>&1 && break
    # Также пробуем основной порт
    curl -s "http://127.0.0.1:\$DEFAULT_PORT/api/health" >/dev/null 2>&1 && CURRENT_PORT=\$DEFAULT_PORT && break
done

# Открываем браузер
open "http://127.0.0.1:\$CURRENT_PORT"
LAUNCHER

    chmod +x "$APP_PATH/Contents/MacOS/launcher"

    # Снимаем карантин (иначе macOS заблокирует при первом запуске)
    xattr -rd com.apple.quarantine "$APP_PATH" 2>/dev/null || true

    # Обновляем кэш иконок
    touch "$APP_PATH"
    /System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister \
        -f "$APP_PATH" 2>/dev/null || true

    echo "[OK] ОрфоДракон.app создан на рабочем столе"
    echo ""
    echo "Если macOS заблокирует запуск при первом клике:"
    echo "  Системные настройки → Конфиденциальность и безопасность → 'Всё равно открыть'"
fi

# ════════════════════════════════════════════════════════════════
# Linux
# ════════════════════════════════════════════════════════════════
if [[ "$OSTYPE" == "linux-gnu"* ]] || [[ "$OSTYPE" == "linux"* ]]; then

    DESKTOP_DIR="$HOME/.local/share/applications"
    mkdir -p "$DESKTOP_DIR"

    ICON_PATH="$APP_DIR/icons/dragon_icon.png"

    DESKTOP_CONTENT="[Desktop Entry]
Name=ОрфоДракон
Comment=Тренажёр ударений и запятых
Exec=$PYTHON_FULL $APP_DIR/main.py
Icon=$ICON_PATH
Terminal=false
Type=Application
Categories=Education;
StartupWMClass=orfodragon"

    echo "$DESKTOP_CONTENT" > "$DESKTOP_DIR/orfodragon.desktop"
    chmod +x "$DESKTOP_DIR/orfodragon.desktop"
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    echo "[OK] Добавлено в меню приложений"

    for DESK in "$HOME/Desktop" "$HOME/Рабочий стол"; do
        if [ -d "$DESK" ]; then
            echo "$DESKTOP_CONTENT" > "$DESK/ОрфоДракон.desktop"
            chmod +x "$DESK/ОрфоДракон.desktop"
            gio set "$DESK/ОрфоДракон.desktop" metadata::trusted true 2>/dev/null || true
            echo "[OK] Ярлык создан на рабочем столе"
            break
        fi
    done
fi

echo ""
echo "============================================"
echo "   Установка завершена!"
echo "============================================"
echo ""

printf "Запустить ОрфоДракон прямо сейчас? [д/н]: "
read -r answer
if [[ "$answer" =~ ^[ДдDdYy] ]] || [ -z "$answer" ]; then
    ORFODRAGON_NO_BROWSER=1 "$PYTHON" "$APP_DIR/main.py" > /tmp/orfodragon.log 2>&1 &
    sleep 1.3
    if [[ "$OSTYPE" == "darwin"* ]]; then
        open "http://127.0.0.1:17832"
    else
        xdg-open "http://127.0.0.1:17832" 2>/dev/null || \
            "$PYTHON" -c "import webbrowser; webbrowser.open('http://127.0.0.1:17832')"
    fi
    echo "Приложение открыто в браузере!"
fi
