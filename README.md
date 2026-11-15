# HandsHover

Touchless, one-handed control of 3D Slicer with hand gestures filmed by an ordinary webcam
(Google MediaPipe hand tracking). Designed for sterile settings such as the operating room:
switch views, scroll slices, zoom and pan, rotate the 3D view —
without touching anything.

> **Research tool. Not a medical device, not validated for clinical use.**
> Camera images are processed locally on this computer; nothing is sent anywhere.

![HandsHover demo: one hand controls the 3D and slice views](docs/handshover-demo.gif)

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
rotation, pan sensitivity and smoothing.

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
| 2D | Index finger + move | Pan the slice |

A swipe changes the view only once per V: open the hand before the next swipe.

## Installation

You need **3D Slicer 5.10 or later** (free, [slicer.org](https://www.slicer.org)) and a webcam.

1. **Download** HandsHover: open the [Releases page](../../releases/latest) and click
   **Source code (zip)** under *Assets*.
2. **Extract** the zip file, for example to `C:\HandsHover`. Inside, you will find a folder
   named `HandsHover` that contains `HandsHover.py`.
3. **Add it to Slicer**: in Slicer, *Edit → Application Settings → Modules*. Next to
   *Additional module paths*, click the arrow / *Add*, select that `HandsHover` folder
   (the one containing `HandsHover.py`), click *OK* and restart Slicer.
4. **Open the module**: press *Ctrl+F* and type **HandsHover** (it is also in the *IGT* category).
5. **First start**: click **Refresh**. Slicer asks to install MediaPipe — accept and wait a few minutes.
   Then click **Start gesture control**. When asked for the hand model, choose **Download**
   (about 8 MB, internet needed once).
6. Load some images (for example *File → Sample Data → CTChest*), tick **Show camera image**
   and hold your hand 40–80 cm from the webcam.

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
