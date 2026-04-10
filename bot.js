const path = require('path');
const cv = require('opencv4nodejs');
const screenshot = require('screenshot-desktop');
const activeWin = require('active-win');
const ioHook = require('iohook');
const { keyboard, Key } = require('@nut-tree/nut-js');

const CONFIG = {
  gameTitleIncludes: 'Para Pa: City of Dances',
  frameIntervalMs: 16,
  arrowConfidenceThreshold: 0.8,
  arrowCooldownMs: 70,
  metronomeBrightnessThreshold: 240,
  perfectDelayMs: 10,
  spaceCooldownMs: 80,
  jitterMinMs: 1,
  jitterMaxMs: 5,
  // Relative zones against full screen dimensions.
  arrowZone: { x: 0.33, y: 0.72, width: 0.34, height: 0.22 },
  metronomeProbe: { x: 0.5, y: 0.86 },
  templatesDir: path.join(__dirname, 'templates')
};

const ARROW_TEMPLATES = {
  up: { file: 'up.png', key: Key.Up },
  down: { file: 'down.png', key: Key.Down },
  left: { file: 'left.png', key: Key.Left },
  right: { file: 'right.png', key: Key.Right }
};

class RhythmBot {
  constructor(config) {
    this.config = config;
    this.running = false;
    this.loopHandle = null;
    this.lastArrowPressByName = new Map();
    this.lastSpacePress = 0;
    this.templates = this.loadTemplates();
  }

  loadTemplates() {
    const templates = {};

    for (const [name, value] of Object.entries(ARROW_TEMPLATES)) {
      const templatePath = path.join(this.config.templatesDir, value.file);
      try {
        templates[name] = {
          key: value.key,
          mat: cv.imread(templatePath)
        };
      } catch (error) {
        throw new Error(`Failed to load template ${templatePath}: ${error.message}`);
      }
    }

    return templates;
  }

  async toggle() {
    this.running = !this.running;

    if (this.running) {
      console.log('[BottomBot] Started.');
      this.runLoop();
    } else {
      console.log('[BottomBot] Stopped.');
      if (this.loopHandle) {
        clearTimeout(this.loopHandle);
        this.loopHandle = null;
      }
    }
  }

  randomJitterMs() {
    const { jitterMinMs, jitterMaxMs } = this.config;
    return Math.floor(Math.random() * (jitterMaxMs - jitterMinMs + 1)) + jitterMinMs;
  }

  async sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  isGameWindowActive(windowInfo) {
    const title = windowInfo?.title || '';
    return title.toLowerCase().includes(this.config.gameTitleIncludes.toLowerCase());
  }

  getRectFromRelative(screenWidth, screenHeight, relativeRect) {
    return {
      x: Math.floor(screenWidth * relativeRect.x),
      y: Math.floor(screenHeight * relativeRect.y),
      width: Math.floor(screenWidth * relativeRect.width),
      height: Math.floor(screenHeight * relativeRect.height)
    };
  }

  getPointFromRelative(screenWidth, screenHeight, relativePoint) {
    return {
      x: Math.floor(screenWidth * relativePoint.x),
      y: Math.floor(screenHeight * relativePoint.y)
    };
  }

  getBrightnessFromBgr(pixelVec3) {
    const [b, g, r] = pixelVec3;
    return (r + g + b) / 3;
  }

  canPressArrow(name, now) {
    const last = this.lastArrowPressByName.get(name) || 0;
    return now - last >= this.config.arrowCooldownMs;
  }

  async pressArrow(name, key) {
    const now = Date.now();
    if (!this.canPressArrow(name, now)) return;

    this.lastArrowPressByName.set(name, now);
    await this.sleep(this.randomJitterMs());
    await keyboard.type(key);
  }

  async pressPerfectSpace() {
    const now = Date.now();
    if (now - this.lastSpacePress < this.config.spaceCooldownMs) return;

    this.lastSpacePress = now;
    await this.sleep(this.config.perfectDelayMs + this.randomJitterMs());
    await keyboard.type(Key.Space);
  }

  detectBestArrow(arrowZoneMat) {
    let best = { name: null, key: null, score: 0 };

    for (const [name, template] of Object.entries(this.templates)) {
      const result = arrowZoneMat.matchTemplate(template.mat, cv.TM_CCOEFF_NORMED);
      const { maxVal } = result.minMaxLoc();
      if (maxVal > best.score) {
        best = { name, key: template.key, score: maxVal };
      }
    }

    return best;
  }

  async scanAndAct() {
    const windowInfo = await activeWin();
    if (!this.isGameWindowActive(windowInfo)) return;

    const imageBuffer = await screenshot({ format: 'png' });
    const frame = cv.imdecode(imageBuffer);

    const arrowRect = this.getRectFromRelative(frame.cols, frame.rows, this.config.arrowZone);
    const arrowZoneMat = frame.getRegion(new cv.Rect(arrowRect.x, arrowRect.y, arrowRect.width, arrowRect.height));

    const bestArrow = this.detectBestArrow(arrowZoneMat);
    if (bestArrow.score >= this.config.arrowConfidenceThreshold) {
      await this.pressArrow(bestArrow.name, bestArrow.key);
    }

    const probePoint = this.getPointFromRelative(frame.cols, frame.rows, this.config.metronomeProbe);
    const pixel = frame.atRaw(probePoint.y, probePoint.x);
    const brightness = this.getBrightnessFromBgr(pixel);

    if (brightness >= this.config.metronomeBrightnessThreshold) {
      await this.pressPerfectSpace();
    }
  }

  runLoop() {
    if (!this.running) return;

    this.scanAndAct()
      .catch((error) => console.error('[BottomBot] Loop error:', error.message))
      .finally(() => {
        this.loopHandle = setTimeout(() => this.runLoop(), this.config.frameIntervalMs);
      });
  }
}

const bot = new RhythmBot(CONFIG);

// F10 keycode in iohook is 68 on Windows keyboards.
ioHook.on('keydown', async (event) => {
  if (event.keycode === 68) {
    await bot.toggle();
  }
});

ioHook.start();
console.log('[BottomBot] Ready. Press F10 to start/stop.');
