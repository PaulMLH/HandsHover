# HandsHover

Touchless, one-handed control of 3D Slicer with hand gestures filmed by an ordinary webcam
(Google MediaPipe hand tracking). Designed for sterile settings such as the operating room:
switch views, scroll slices, zoom, adjust window/level, rotate and pan the 3D view —
without touching anything.

> **Research tool. Not a medical device, not validated for clinical use.**
> Camera images are processed locally on this computer; nothing is sent anywhere.

![HandsHover icon](HandsHover.png)

## Features

- One button to start / stop, camera selection by name (including virtual cameras such as NVIDIA Broadcast).
- Everything is done **with one hand**: the first hand seen controls Slicer; the other hand is ignored
  (except for the two-fist pause).
- Mode banner always visible at the top of the Slicer window (**3D, RED, GREEN, YELLOW, PAUSED**)
  and a coloured frame around the controlled view; the standard four-view layout is unchanged.
- Hand tracking runs in a separate process (`PythonSlicer`): Slicer stays responsive,
  OpenCV is never loaded inside Slicer, and the camera is released if Slicer closes.
- Gesture engine in pure Python, unit-tested on synthetic hands (no camera needed).

## Module

**HandsHover** (category IGT): starts the webcam hand tracking and turns gestures into
view commands. Settings for scrolling speed and acceleration, swipe distance, zoom,
rotation, pan, window/level sensitivity and smoothing.

## Gestures

"Pinch" = OK sign (thumb and index touching, the other three fingers extended).

| Where | Gesture | Effect |
|---|---|---|
| Everywhere | Two closed fists, 1 s | Pause / resume all commands |
| Everywhere | Thumb up, 1 s | Recenter the controlled view |
| Everywhere | V + horizontal swipe | Next (right) / previous (left) view: 3D → Red → Green → Yellow |
| 3D | Fist + up/down | Zoom in / out |
| 3D | Pinch + move | Rotate the model |
| 3D | Index finger + move | Pan the model |
| 2D | Pinch + up/down | Scroll slices (faster gesture = accelerated) |
| 2D | Fist + up/down | Zoom in / out |
| 2D | Index finger + move | Window (horizontal) / level (vertical) |

A swipe changes the view only once per V: open the hand before the next swipe.

## Installation (development)

1. 3D Slicer ≥ 5.10 (Python 3.12).
2. *Edit → Application Settings → Modules → Additional module paths*: add the `HandsHover` folder
   (the one containing `HandsHover.py`), then restart Slicer.
3. Open **HandsHover** (category IGT), click **Refresh**, then **Start gesture control**.
   On first use the module asks before installing `mediapipe` (and OpenCV if needed) and
   the hand model (`hand_landmarker.task`, ~8 MB): download it from Google or select an existing file.

## Tests

```
python Tests/run_tests.py          # gesture engine, plain Python
```
In Slicer (developer mode): *Reload and Test* runs the engine tests plus view actions on a synthetic volume.

## Known limitations

- Not yet evaluated with surgical gloves and operating-room lighting: please test before relying on it.
- Thumb-up detection depends on the thumb being clearly visible and pointing up.

## Author

Created by Dr Paul Martre - Le Havre - France.

## License

Apache 2.0 — see [LICENSE.txt](LICENSE.txt). MediaPipe and its models are © Google, Apache 2.0.
