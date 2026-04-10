const path = require('path');
const os = require('os');
const { app, BrowserWindow, ipcMain, globalShortcut } = require('electron');
const { keyboard, Key, Region, Point, imageResource, screen } = require('@nut-tree/nut-js');

const CONFIG = {
  arrowScanIntervalMs: 50,
  perfectScanIntervalMs: 5,
  perfectMicroDelayMs: 10,
  arrowConfidence: 0.85,
  arrowRegion: new Region(600, 120, 720, 260),
  perfectPixel: new Point(960, 540),
  perfectWhiteThreshold: 240,
  perfectDebounceMs: 120
};

const TEMPLATE_FILES = {
  up: 'up.png',
  down: 'down.png',
  left: 'left.png',
  right: 'right.png'
};

const KEY_MAP = {
  up: Key.Up,
  down: Key.Down,
  left: Key.Left,
  right: Key.Right
};

let mainWindow = null;
let isRunning = false;
let arrowTimer = null;
let perfectTimer = null;
let arrowBusy = false;
let perfectBusy = false;
let lastPerfectHitAt = 0;
let templates = {};

function setHighPriority() {
  try {
    os.setPriority(process.pid, os.constants.priority.PRIORITY_HIGH);
    console.log('[Para Pa Bot] Process priority set to HIGH.');
  } catch (error) {
    console.warn('[Para Pa Bot] Unable to set HIGH priority:', error.message);
  }
}

function notifyStatus(status) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send('bot:status', { status, isRunning });
}

async function tapKey(key) {
  await keyboard.pressKey(key);
  await keyboard.releaseKey(key);
}

async function loadTemplates() {
  templates = Object.fromEntries(
    Object.entries(TEMPLATE_FILES).map(([name, filename]) => [
      name,
      imageResource(path.join(__dirname, filename))
    ])
  );
}

function parseColor(colorValue) {
  if (!colorValue) return { r: 0, g: 0, b: 0 };

  if (typeof colorValue === 'string') {
    const clean = colorValue.replace('#', '').trim();
    if (clean.length === 6) {
      const numeric = Number.parseInt(clean, 16);
      return {
        r: (numeric >> 16) & 255,
        g: (numeric >> 8) & 255,
        b: numeric & 255
      };
    }
  }

  if (typeof colorValue === 'object') {
    return {
      r: colorValue.r ?? colorValue.red ?? 0,
      g: colorValue.g ?? colorValue.green ?? 0,
      b: colorValue.b ?? colorValue.blue ?? 0
    };
  }

  return { r: 0, g: 0, b: 0 };
}

async function scanForArrow() {
  if (!isRunning || arrowBusy) return;
  arrowBusy = true;

  try {
    for (const [name, template] of Object.entries(templates)) {
      try {
        const match = await screen.find(template, {
          searchRegion: CONFIG.arrowRegion,
          confidence: CONFIG.arrowConfidence
        });

        if (match) {
          await tapKey(KEY_MAP[name]);
          notifyStatus('Scanning');
          return;
        }
      } catch {
        // Template not found in this frame.
      }
    }
  } catch (error) {
    console.error('[Para Pa Bot] Arrow scan error:', error.message);
  } finally {
    arrowBusy = false;
  }
}

async function scanPerfectPixel() {
  if (!isRunning || perfectBusy) return;
  perfectBusy = true;

  try {
    const now = Date.now();
    if (now - lastPerfectHitAt < CONFIG.perfectDebounceMs) return;

    const pixel = await screen.colorAt(CONFIG.perfectPixel);
    const { r, g, b } = parseColor(pixel);

    if (
      r >= CONFIG.perfectWhiteThreshold &&
      g >= CONFIG.perfectWhiteThreshold &&
      b >= CONFIG.perfectWhiteThreshold
    ) {
      notifyStatus('Perfect Active');
      setTimeout(() => {
        void tapKey(Key.Space);
      }, CONFIG.perfectMicroDelayMs);
      lastPerfectHitAt = Date.now();
    }
  } catch (error) {
    console.error('[Para Pa Bot] Perfect scan error:', error.message);
  } finally {
    perfectBusy = false;
  }
}

function startBot() {
  if (isRunning) return;
  isRunning = true;
  notifyStatus('Scanning');

  arrowTimer = setInterval(() => {
    void scanForArrow();
  }, CONFIG.arrowScanIntervalMs);

  perfectTimer = setInterval(() => {
    void scanPerfectPixel();
  }, CONFIG.perfectScanIntervalMs);
}

function stopBot() {
  if (!isRunning) return;
  isRunning = false;

  if (arrowTimer) clearInterval(arrowTimer);
  if (perfectTimer) clearInterval(perfectTimer);

  arrowTimer = null;
  perfectTimer = null;
  notifyStatus('Waiting');
}

function toggleBot() {
  if (isRunning) {
    stopBot();
  } else {
    startBot();
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 250,
    height: 350,
    resizable: false,
    alwaysOnTop: true,
    backgroundColor: '#0e1118',
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    }
  });

  mainWindow.removeMenu();
  mainWindow.setAlwaysOnTop(true, 'screen-saver');
  mainWindow.loadFile('index.html');
}

app.whenReady().then(async () => {
  setHighPriority();

  keyboard.config.autoDelayMs = 0;
  await loadTemplates();
  createWindow();

  globalShortcut.register('F10', toggleBot);

  ipcMain.handle('bot:toggle', () => {
    toggleBot();
    return { isRunning, status: isRunning ? 'Scanning' : 'Waiting' };
  });

  ipcMain.handle('bot:state', () => ({
    isRunning,
    status: isRunning ? 'Scanning' : 'Waiting'
  }));
});

app.on('will-quit', () => {
  stopBot();
  globalShortcut.unregisterAll();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
