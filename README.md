# BottomBot Rhythm Assistant

A modern desktop rhythm assistant that uses **computer vision** on a selected screen region to detect visual rhythm cues and evaluate keypress timing in real time.

## Features

- Transparent-style rhythm overlay canvas with:
  - timing bar
  - beat pulses
  - perfect zone
  - feedback (`Perfect/Good/Miss`)
- Drag-to-select region capture
- Low-latency frame capture (mss + OpenCV, target 30-60 FPS)
- Vision-based beat detection (frame difference + motion area threshold)
- Dynamic BPM estimation + smoothing via moving average
- Global keyboard listener and timing evaluation:
  - Perfect: ±30 ms
  - Good: ±80 ms
  - Miss: otherwise
- Manual calibration tap button to align visual timing with user timing
- Debug preview with highlighted motion heatmap
- Runtime stats:
  - Accuracy %
  - Average offset
  - Hit distribution counters (internal)

## Constraints & Safety

This tool is **observe-only**:

- ✅ Reads pixels from a user-selected screen region
- ✅ Listens to user keyboard events
- ❌ Does not inject input
- ❌ Does not hook or modify external processes

## Project Structure

```text
main.py
ui/
  main_window.py
  region_selector.py
vision/
  beat_detector.py
timing/
  beat_estimator.py
input/
  key_listener.py
utils/
  models.py
requirements.txt
```

## Run Instructions

1. Create virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

2. Start application:

```bash
python main.py
```

3. Usage flow:

- Click **Select Region** and drag over the rhythm indicator area.
- Turn **Debug Mode** on to inspect detection quality.
- Tune **Sensitivity** slider if over/under-detecting beats.
- Press rhythm keys in sync.
- Click **Calibrate Tap** while tapping to shift timing alignment.

## Optional ideas (next steps)

- Auto-detect hit line with edge detection / Hough lines.
- Adaptive timing learning per song segment.
- Optional sound feedback (beep per detection/hit result).
