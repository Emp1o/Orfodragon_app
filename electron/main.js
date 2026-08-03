"use strict";

/**
 * electron/main.js — главный процесс Electron.
 *
 * Отвечает за:
 *   1. Запуск Python-бэкенда (бандл или fallback на python3 в dev-режиме).
 *   2. Ожидание готовности бэкенда (polling порта).
 *   3. Создание окна BrowserWindow и загрузку UI.
 *   4. Корректное завершение бэкенда при выходе.
 */

const { app, BrowserWindow, dialog, shell, session } = require("electron");
const path = require("path");
const fs   = require("fs");
const net  = require("net");
const { spawn } = require("child_process");

// ── Состояние приложения ─────────────────────────────────────────────────────

let mainWindow      = null;
let backendProcess  = null;
let backendPort     = 17832;

// ── Единственный экземпляр приложения ────────────────────────────────────────

const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
}

// ── Вспомогательные функции: пути ────────────────────────────────────────────

const isDev = () => !app.isPackaged;

/** Корень исходников (orfodragon/) — только в dev-режиме. */
const getProjectRoot = () => path.resolve(__dirname, "..");

/** Путь к ресурсам в собранном приложении. */
const getResourceRoot = () => process.resourcesPath;

function getIconPath() {
  const iconFile = process.platform === "win32" ? "icon.ico" : "icon.png";
  const candidates = isDev()
    ? [path.join(getProjectRoot(), "icons", iconFile)]
    : [path.join(getResourceRoot(), "icons", iconFile)];
  return candidates.find((p) => fs.existsSync(p)) ?? null;
}

function getBackendExecutableName() {
  return process.platform === "win32" ? "OrfoDragonBackend.exe" : "OrfoDragonBackend";
}

function getBundledBackendPath() {
  const root = isDev() ? getProjectRoot() : getResourceRoot();
  const subdir = isDev() ? "packaged_backend" : "backend";
  return path.join(root, subdir, getBackendExecutableName());
}

/**
 * Файл с портом записывается Python-бэкендом при старте.
 * Путь совпадает с тем, что вычисляет backend/config.py.
 */
function getPortFilePath() {
  const home = app.getPath("home");
  if (process.platform === "darwin") {
    return path.join(home, "Library", "Application Support", "OrfoDragon", "port.txt");
  }
  if (process.platform === "win32") {
    return path.join(app.getPath("appData"), "OrfoDragon", "port.txt");
  }
  const xdgData = process.env.XDG_DATA_HOME ?? path.join(home, ".local", "share");
  return path.join(xdgData, "OrfoDragon", "port.txt");
}

// ── Вспомогательные функции: сеть ────────────────────────────────────────────

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function canConnect(port) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    socket.setTimeout(350);
    socket.once("connect", () => { socket.destroy(); resolve(true); });
    socket.once("timeout", () => { socket.destroy(); resolve(false); });
    socket.once("error",   () => resolve(false));
    socket.connect(port, "127.0.0.1");
  });
}

function readPortFile() {
  try {
    const raw    = fs.readFileSync(getPortFilePath(), "utf8").trim();
    const parsed = Number.parseInt(raw, 10);
    if (Number.isInteger(parsed) && parsed > 0 && parsed < 65536) {
      return parsed;
    }
  } catch (_) { /* файл ещё не создан — это нормально */ }
  return 17832;
}

/** Ждём до 30 секунд, пока бэкенд не поднимется. */
async function waitForBackend() {
  for (let i = 0; i < 120; i++) {
    backendPort = readPortFile();
    if (await canConnect(backendPort)) return true;
    // Запасной вариант: дефолтный порт
    if (backendPort !== 17832 && await canConnect(17832)) {
      backendPort = 17832;
      return true;
    }
    await sleep(250);
  }
  return false;
}

// ── Запуск бэкенда ────────────────────────────────────────────────────────────

function showFatalError(message) {
  dialog.showErrorBox("OrfoDragon: ошибка запуска", message);
}

function buildDiagnostics(backendPath) {
  return [
    `Backend:       ${backendPath}`,
    `resourcesPath: ${process.resourcesPath}`,
    `appPath:       ${app.getAppPath()}`,
    `platform:      ${process.platform}`,
    `packaged:      ${app.isPackaged}`,
  ].join("\n");
}

function startBackend() {
  const env     = { ...process.env, ORFODRAGON_NO_BROWSER: "1", ORFODRAGON_ELECTRON: "1" };
  const bundled = getBundledBackendPath();

  if (fs.existsSync(bundled)) {
    // Убеждаемся, что файл исполняемый (актуально для macOS/Linux)
    if (process.platform !== "win32") {
      try { fs.chmodSync(bundled, 0o755); } catch (_) {}
    }
    try {
      backendProcess = spawn(bundled, [], {
        env,
        cwd:         path.dirname(bundled),
        windowsHide: true,
        detached:    false,
        stdio:       ["ignore", "pipe", "pipe"],
      });
    } catch (err) {
      showFatalError(`Не удалось запустить backend.\n\n${buildDiagnostics(bundled)}\n\n${err.message}`);
      return;
    }
  } else if (isDev()) {
    // В режиме разработки падаем на системный python3
    const pythonBin  = process.platform === "win32" ? "python" : "python3";
    const scriptPath = path.join(getProjectRoot(), "main.py");
    backendProcess = spawn(pythonBin, [scriptPath], {
      env,
      cwd:         getProjectRoot(),
      windowsHide: true,
      detached:    false,
      stdio:       ["ignore", "pipe", "pipe"],
    });
  } else {
    showFatalError(`Не найден backend приложения.\n\n${buildDiagnostics(bundled)}`);
    return;
  }

  backendProcess.stdout.on("data", (data) => console.log(`[backend] ${data}`));
  backendProcess.stderr.on("data", (data) => console.error(`[backend] ${data}`));
  backendProcess.on("error", (err) => {
    showFatalError(`Backend не смог запуститься.\n\n${err.message}`);
  });
}

// ── Создание окна ─────────────────────────────────────────────────────────────

async function createWindow() {
  if (mainWindow) {
    mainWindow.focus();
    return;
  }

  // Разрешаем только уведомления (нужны для напоминаний о повторении)
  session.defaultSession.setPermissionRequestHandler((_webContents, permission, callback) => {
    callback(permission === "notifications");
  });

  mainWindow = new BrowserWindow({
    width:           1180,
    height:          820,
    minWidth:        920,
    minHeight:       640,
    title:           "OrfoDragon",
    icon:            getIconPath(),
    show:            false,
    autoHideMenuBar: true,
    webPreferences: {
      preload:          path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration:  false,
      sandbox:          true,
    },
  });

  // Показываем окно только после полной загрузки — избегаем белого мелька
  mainWindow.once("ready-to-show", () => mainWindow.show());

  const backendReady = await waitForBackend();
  if (!backendReady) {
    showFatalError(`Backend не запустился.\n\n${buildDiagnostics(getBundledBackendPath())}`);
    return;
  }

  await mainWindow.loadURL(`http://127.0.0.1:${backendPort}`);

  // Внешние ссылки открываем в системном браузере, а не внутри Electron
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

// ── Жизненный цикл приложения ─────────────────────────────────────────────────

if (gotTheLock) {
  app.whenReady().then(async () => {
    app.setAppUserModelId("ru.orfodragon.app");

    // Второй экземпляр → фокусируем существующее окно
    app.on("second-instance", () => {
      if (!mainWindow) return;
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    });

    startBackend();
    await createWindow();

    // macOS: повторное создание окна при клике по иконке в Dock
    app.on("activate", async () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        await createWindow();
      }
    });
  });
}

app.on("before-quit", () => {
  if (backendProcess && !backendProcess.killed) {
    try { backendProcess.kill(); } catch (_) {}
  }
});

app.on("window-all-closed", () => {
  // На macOS приложения остаются в памяти после закрытия всех окон
  if (process.platform !== "darwin") {
    app.quit();
  }
});
