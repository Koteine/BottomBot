const path = require('path');
const { app, BrowserWindow, ipcMain, globalShortcut } = require('electron');
const robot = require('robotjs');
const Jimp = require('jimp');

const CONFIG = {
  scanIntervalMs: 100,
  whiteThreshold: 240,
  templateThreshold: 38,
  sampleStep: 2
};

const TEMPLATE_FILES = {
  up: 'up.png',
  down: 'down.png',
  left: 'left.png',
  right: 'right.png'
};

let mainWindow = null;
let scanTimer = null;
let isScanning = false;
let templates = {};

function notifyStatus(isRunning) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send('bot:status', { isRunning });
}

function notifyArrow(name) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send('bot:arrow', { name });
}

async function loadTemplates() {
  const entries = Object.entries(TEMPLATE_FILES);
  for (const [name, file] of entries) {
    templates[name] = await Jimp.read(path.join(__dirname, file));
  }
}

function captureScreenToJimp() {
  const size = robot.getScreenSize();
  const capture = robot.screen.capture(0, 0, size.width, size.height);
  const rgbaBuffer = Buffer.alloc(size.width * size.height * 4);

  for (let y = 0; y < size.height; y += 1) {
    for (let x = 0; x < size.width; x += 1) {
      const srcIdx = y * capture.byteWidth + x * capture.bytesPerPixel;
      const dstIdx = (y * size.width + x) * 4;

      const blue = capture.image[srcIdx + 0];
      const green = capture.image[srcIdx + 1];
      const red = capture.image[srcIdx + 2];

      rgbaBuffer[dstIdx + 0] = red;
      rgbaBuffer[dstIdx + 1] = green;
      rgbaBuffer[dstIdx + 2] = blue;
      rgbaBuffer[dstIdx + 3] = 255;
    }
  }

  return new Jimp({ data: rgbaBuffer, width: size.width, height: size.height });
}

function templateErrorAt(screenImage, templateImage, startX, startY) {
  let totalDiff = 0;
  let samples = 0;

  for (let y = 0; y < templateImage.bitmap.height; y += CONFIG.sampleStep) {
    for (let x = 0; x < templateImage.bitmap.width; x += CONFIG.sampleStep) {
      const sColor = Jimp.intToRGBA(screenImage.getPixelColor(startX + x, startY + y));
      const tColor = Jimp.intToRGBA(templateImage.getPixelColor(x, y));

      totalDiff += Math.abs(sColor.r - tColor.r);
      totalDiff += Math.abs(sColor.g - tColor.g);
      totalDiff += Math.abs(sColor.b - tColor.b);
      samples += 3;
    }
  }

  return totalDiff / samples;
}

function findArrow(screenImage) {
  const sw = screenImage.bitmap.width;
  const sh = screenImage.bitmap.height;
  const bottomStartY = Math.floor(sh * 0.5);
  let best = { name: null, error: Number.POSITIVE_INFINITY };

  for (const [name, template] of Object.entries(templates)) {
    const maxX = sw - template.bitmap.width;
    const maxY = sh - template.bitmap.height;

    if (maxX <= 0 || maxY <= 0) continue;

    for (let y = bottomStartY; y <= maxY; y += 8) {
      for (let x = 0; x <= maxX; x += 8) {
        const error = templateErrorAt(screenImage, template, x, y);
        if (error < best.error) {
          best = { name, error };
        }
      }
    }
  }

  if (best.name && best.error <= CONFIG.templateThreshold) {
    return best.name;
  }

  return null;
}

function checkPerfectHit() {
  const size = robot.getScreenSize();
  const cx = Math.floor(size.width / 2);
  const cy = Math.floor(size.height / 2);

  const hex = robot.getPixelColor(cx, cy);
  const numeric = parseInt(hex, 16);

  const r = (numeric >> 16) & 255;
  const g = (numeric >> 8) & 255;
  const b = numeric & 255;

  if (r > CONFIG.whiteThreshold && g > CONFIG.whiteThreshold && b > CONFIG.whiteThreshold) {
    robot.keyTap('space');
  }
}

async function scanTick() {
  if (isScanning || !scanTimer) return;
  isScanning = true;

  try {
    const screenImage = captureScreenToJimp();
    const arrow = findArrow(screenImage);

    if (arrow) {
      robot.keyTap(arrow);
      notifyArrow(arrow);
    }

    checkPerfectHit();
  } catch (error) {
    console.error('[Para Pa Bot] Scan error:', error.message);
  } finally {
    isScanning = false;
  }
}

function startBot() {
  if (scanTimer) return;
  scanTimer = setInterval(() => {
    void scanTick();
  }, CONFIG.scanIntervalMs);
  notifyStatus(true);
}

function stopBot() {
  if (!scanTimer) return;
  clearInterval(scanTimer);
  scanTimer = null;
  notifyStatus(false);
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
    alwaysOnTop: true,
    resizable: false,
    backgroundColor: '#0a0d15',
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
  await loadTemplates();
  createWindow();

  globalShortcut.register('F10', () => {
    toggleBot();
  });

  ipcMain.handle('bot:start', () => {
    startBot();
    return { isRunning: true };
  });

  ipcMain.handle('bot:stop', () => {
    stopBot();
    return { isRunning: false };
  });

  ipcMain.handle('bot:state', () => ({ isRunning: Boolean(scanTimer) }));
});

app.on('will-quit', () => {
  stopBot();
  globalShortcut.unregisterAll();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
