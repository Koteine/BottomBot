const path = require("path");
const { app, BrowserWindow, ipcMain, globalShortcut } = require("electron");
const { keyboard, Key, screen, imageResource, Point } = require("@nut-tree/nut-js");

const CONFIG = {
  confidence: 0.8,
  scanIntervalMs: 75,
  jitterMinMs: 5,
  jitterMaxMs: 10,
  metronomePoint: { x: 960, y: 540 },
  whiteThreshold: 240,
  spaceCooldownMs: 60
};

const TEMPLATE_MAP = {
  up: { file: "up.png", key: Key.Up },
  down: { file: "down.png", key: Key.Down },
  left: { file: "left.png", key: Key.Left },
  right: { file: "right.png", key: Key.Right }
};

let mainWindow = null;
let scanTimer = null;
let busy = false;
let lastSpacePressAt = 0;

screen.config.confidence = CONFIG.confidence;

const templates = Object.fromEntries(
  Object.entries(TEMPLATE_MAP).map(([name, value]) => [
    name,
    {
      key: value.key,
      image: imageResource(path.join(__dirname, value.file))
    }
  ])
);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function jitterMs() {
  const { jitterMinMs, jitterMaxMs } = CONFIG;
  return Math.floor(Math.random() * (jitterMaxMs - jitterMinMs + 1)) + jitterMinMs;
}

function channelStatus(isRunning) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send("bot:status", { isRunning });
}

function isBrightWhite(color) {
  const r = color.r ?? color.red ?? color.R ?? 0;
  const g = color.g ?? color.green ?? color.G ?? 0;
  const b = color.b ?? color.blue ?? color.B ?? 0;
  return r > CONFIG.whiteThreshold && g > CONFIG.whiteThreshold && b > CONFIG.whiteThreshold;
}

async function pressKeyWithJitter(key) {
  await sleep(jitterMs());
  await keyboard.pressKey(key);
  await keyboard.releaseKey(key);
}

async function scanArrows() {
  for (const template of Object.values(templates)) {
    try {
      await screen.find(template.image);
      await pressKeyWithJitter(template.key);
    } catch {
      // No match above confidence threshold.
    }
  }
}

async function scanMetronome() {
  const point = new Point(CONFIG.metronomePoint.x, CONFIG.metronomePoint.y);
  const color = await screen.colorAt(point);

  if (!isBrightWhite(color)) return;

  const now = Date.now();
  if (now - lastSpacePressAt < CONFIG.spaceCooldownMs) return;

  lastSpacePressAt = now;
  await pressKeyWithJitter(Key.Space);
}

async function scanTick() {
  if (busy) return;
  busy = true;
  try {
    await scanArrows();
    await scanMetronome();
  } catch (error) {
    console.error("[ParaPa Electron Bot] Scan error:", error.message);
  } finally {
    busy = false;
  }
}

function startBot() {
  if (scanTimer) return;
  scanTimer = setInterval(() => {
    void scanTick();
  }, CONFIG.scanIntervalMs);
  channelStatus(true);
}

function stopBot() {
  if (!scanTimer) return;
  clearInterval(scanTimer);
  scanTimer = null;
  channelStatus(false);
}

function toggleBot() {
  if (scanTimer) {
    stopBot();
  } else {
    startBot();
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 300,
    height: 400,
    resizable: false,
    alwaysOnTop: true,
    backgroundColor: "#111111",
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    }
  });

  mainWindow.setAlwaysOnTop(true, "screen-saver");
  mainWindow.loadFile("index.html");
  mainWindow.removeMenu();
}

app.whenReady().then(() => {
  createWindow();

  globalShortcut.register("F10", () => {
    toggleBot();
  });

  ipcMain.handle("bot:start", () => {
    startBot();
    return { isRunning: true };
  });

  ipcMain.handle("bot:stop", () => {
    stopBot();
    return { isRunning: false };
  });

  ipcMain.handle("bot:state", () => ({ isRunning: Boolean(scanTimer) }));

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  stopBot();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
