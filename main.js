'use strict';

const path = require('path');
const screenshot = require('screenshot-desktop');
const Jimp = require('jimp');
const robot = require('robotjs');

const TARGET_FPS = 60;
const FRAME_MS = Math.round(1000 / TARGET_FPS);
const WHITE_FLASH_THRESHOLD = 245;
const MATCH_THRESHOLD = 0.92;
const COOLDOWN_MS = 45;

// Update these from your game window coordinates.
const ARROW_REGION = {
  x: 0,
  y: 0,
  width: 1920,
  height: 1080,
};

// [X, Y] pixel to monitor for metronome flash.
const METRONOME_PIXEL = { x: 0, y: 0 };

const TEMPLATE_CONFIG = [
  { name: 'up', file: 'up.png', key: 'up' },
  { name: 'down', file: 'down.png', key: 'down' },
  { name: 'left', file: 'left.png', key: 'left' },
  { name: 'right', file: 'right.png', key: 'right' },
];

const state = {
  running: false,
  templates: [],
  lastArrowHitAt: new Map(),
  lastFlashAt: 0,
  flashLocked: false,
  loopPromise: null,
};

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function imageToGrayBuffer(image) {
  const { width, height, data } = image.bitmap;
  const gray = new Uint8Array(width * height);
  for (let i = 0, j = 0; i < data.length; i += 4, j += 1) {
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    gray[j] = ((r * 299) + (g * 587) + (b * 114)) / 1000;
  }
  return gray;
}

async function loadTemplates() {
  const loaded = [];
  for (const tpl of TEMPLATE_CONFIG) {
    const absolute = path.join(__dirname, tpl.file);
    const image = await Jimp.read(absolute);
    const gray = imageToGrayBuffer(image);

    loaded.push({
      ...tpl,
      width: image.bitmap.width,
      height: image.bitmap.height,
      gray,
    });
  }
  state.templates = loaded;
}

function scoreTemplate(screenGray, screenWidth, startX, startY, template) {
  const { width: tw, height: th, gray: tGray } = template;
  let sumDiff = 0;
  const totalPx = tw * th;

  for (let y = 0; y < th; y += 1) {
    const sy = startY + y;
    const sRow = sy * screenWidth;
    const tRow = y * tw;

    for (let x = 0; x < tw; x += 1) {
      const sIdx = sRow + (startX + x);
      const tIdx = tRow + x;
      sumDiff += Math.abs(screenGray[sIdx] - tGray[tIdx]);
    }
  }

  const normalized = 1 - (sumDiff / (totalPx * 255));
  return normalized;
}

function findTemplateInRegion(screenGray, screenWidth, screenHeight, template, region) {
  const maxX = Math.min(region.x + region.width - template.width, screenWidth - template.width);
  const maxY = Math.min(region.y + region.height - template.height, screenHeight - template.height);

  // Coarse scan first (stride 2) to reduce CPU, then fine scan around best candidate.
  let best = { score: -1, x: -1, y: -1 };

  for (let y = region.y; y <= maxY; y += 2) {
    for (let x = region.x; x <= maxX; x += 2) {
      const score = scoreTemplate(screenGray, screenWidth, x, y, template);
      if (score > best.score) best = { score, x, y };
      if (score >= MATCH_THRESHOLD) {
        return { found: true, x, y, score };
      }
    }
  }

  if (best.x >= 0) {
    const startX = Math.max(region.x, best.x - 1);
    const endX = Math.min(maxX, best.x + 1);
    const startY = Math.max(region.y, best.y - 1);
    const endY = Math.min(maxY, best.y + 1);

    for (let y = startY; y <= endY; y += 1) {
      for (let x = startX; x <= endX; x += 1) {
        const score = scoreTemplate(screenGray, screenWidth, x, y, template);
        if (score > best.score) best = { score, x, y };
        if (score >= MATCH_THRESHOLD) {
          return { found: true, x, y, score };
        }
      }
    }
  }

  return { found: false, ...best };
}

function getPixelBrightness(image, x, y) {
  if (x < 0 || y < 0 || x >= image.bitmap.width || y >= image.bitmap.height) {
    return 0;
  }
  const idx = (y * image.bitmap.width + x) * 4;
  const data = image.bitmap.data;
  return (data[idx] + data[idx + 1] + data[idx + 2]) / 3;
}

function tapKeyWithCooldown(key) {
  const now = Date.now();
  const last = state.lastArrowHitAt.get(key) || 0;
  if (now - last < COOLDOWN_MS) return;

  robot.keyTap(key);
  state.lastArrowHitAt.set(key, now);
}

async function handleMetronomeFlash(image) {
  const now = Date.now();
  const brightness = getPixelBrightness(image, METRONOME_PIXEL.x, METRONOME_PIXEL.y);
  const isBright = brightness >= WHITE_FLASH_THRESHOLD;

  // Rising edge trigger: bright now and wasn't bright in previous frame.
  if (isBright && !state.flashLocked) {
    state.flashLocked = true;
    state.lastFlashAt = now;

    const delay = 10 + Math.floor(Math.random() * 11); // 10-20 ms
    setTimeout(() => {
      if (state.running) robot.keyTap('space');
    }, delay);
  } else if (!isBright) {
    state.flashLocked = false;
  }
}

async function processFrame() {
  const buffer = await screenshot({ format: 'png' });
  const image = await Jimp.read(buffer);

  const screenWidth = image.bitmap.width;
  const screenHeight = image.bitmap.height;
  const screenGray = imageToGrayBuffer(image);

  for (const template of state.templates) {
    const result = findTemplateInRegion(screenGray, screenWidth, screenHeight, template, ARROW_REGION);
    if (result.found) {
      tapKeyWithCooldown(template.key);
    }
  }

  await handleMetronomeFlash(image);
}

async function scanLoop() {
  while (state.running) {
    const frameStart = performance.now();

    try {
      await processFrame();
    } catch (error) {
      console.error('[bot] frame processing failed:', error);
    }

    const elapsed = performance.now() - frameStart;
    const sleep = Math.max(0, FRAME_MS - elapsed);
    await wait(sleep);
  }
}

async function startAutomation() {
  if (state.running) return;

  if (!state.templates.length) {
    await loadTemplates();
  }

  state.running = true;
  state.loopPromise = scanLoop();
  console.log('[bot] automation started');
}

async function stopAutomation() {
  if (!state.running) return;

  state.running = false;
  await state.loopPromise;
  state.loopPromise = null;
  console.log('[bot] automation stopped');
}

function bindUi() {
  const startButton =
    document.getElementById('start') ||
    document.getElementById('startBtn') ||
    document.querySelector('[data-action="start"]');

  const stopButton =
    document.getElementById('stop') ||
    document.getElementById('stopBtn') ||
    document.querySelector('[data-action="stop"]');

  if (!startButton || !stopButton) {
    console.warn('[bot] START/STOP buttons not found. Expected ids: start|startBtn and stop|stopBtn');
    return;
  }

  startButton.addEventListener('click', () => {
    startAutomation().catch((err) => console.error('[bot] start failed:', err));
  });

  stopButton.addEventListener('click', () => {
    stopAutomation().catch((err) => console.error('[bot] stop failed:', err));
  });
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  window.addEventListener('DOMContentLoaded', bindUi);
}

module.exports = {
  startAutomation,
  stopAutomation,
};
