# SlicerHandsHover

Touchless control of 3D Slicer with hand gestures filmed by an ordinary webcam
(Google MediaPipe hand tracking). Designed for sterile settings: scroll slices,
adjust window/level, rotate / pan / zoom the 3D view, and drive the mouse —
without touching anything.

> **Research tool. Not a medical device, not validated for clinical use.**
> Camera images are processed locally on this computer; nothing is sent anywhere.

![Icon](HandsHover.png)

## Features

- On/off button, camera selection by name (including virtual cameras such as NVIDIA Broadcast).
- Mode banner always visible at the top of the Slicer window: **3D, RED, GREEN, YELLOW, MOUSE, PAUSE**.
- Coloured frame around the controlled view; the standard four-view layout is unchanged.
- Hand tracking runs in a separate process (`PythonSlicer`): Slicer stays responsive,
  OpenCV is never loaded inside Slicer, and the camera is released if Slicer closes.
- Gesture engine in pure Python, unit-tested on synthetic hands (no camera needed).

## Gestures

Everything is done **with one hand**: the first hand seen controls Slicer and keeps control
while it stays in view; the other hand is ignored, except for two two-handed gestures.
"Pinch" = OK sign (thumb and index touching, the other three fingers extended).

| Where | Gesture | Effect |
|---|---|---|
| Everywhere | Two closed fists, 1 s | Pause / resume commands (avoids accidental commands) |
| Everywhere | Two open hands, 1 s | Enter / leave mouse mode |
| 3D and 2D | V + horizontal swipe | Next (right) / previous (left) view: 3D → Red → Green → Yellow |
| 3D | Fist + up/down | Zoom in / out |
| 3D | Pinch + move | Rotate the model |
| 3D | Thumb + little finger (shaka) + move | Pan the model |
| 2D | Pinch + up/down | Scroll slices (faster gesture = accelerated) |
| 2D | Fist + up/down | Zoom in / out |
| 2D | Shaka + move | Window (horizontal) / level (vertical) |
| Mouse | Open hand | Pointer (limited to the Slicer window) |
| Mouse | Short pinch | Left click (two quick ones = double click) |
| Mouse | Pinch + move | Drag |
| Mouse | Short fist | Right click |
| Mouse | V + up/down | Mouse wheel (Windows) |

A swipe changes the view only once per V: open the hand before the next swipe.

## Installation (development)

1. Slicer ≥ 5.10 (Python 3.12).
2. *Edit → Application Settings → Modules → Additional module paths*: add the `HandsHover` folder, restart.
3. Open *HandsHover* (category IGT), click **Rafraîchir**, then **Activer**.
   On first use the module asks before installing `mediapipe` (and OpenCV if needed) and
   the hand model (`hand_landmarker.task`, ~8 MB) — download it from Google or pick an existing file.

## Tests

```
python Tests/run_tests.py          # gesture engine, plain Python
```
In Slicer: *Developer mode → Reload and Test* runs the engine tests plus view actions on a synthetic volume.

## Known limitations (v0.2)

- Mouse clicks and wheel use native Windows events; on Linux/macOS they use Qt events (experimental, no wheel).
- Not yet tested with real surgical gloves and operating-room lighting: please measure before relying on it.
- User interface currently in French.

## License

Apache 2.0 — see [LICENSE](LICENSE). MediaPipe and its models are © Google, Apache 2.0.

---

## En bref (français)

HandsHover : module de contrôle gestuel sans contact pour 3D Slicer. Pour l'essayer :
ajouter le dossier `HandsHover` dans *Edit → Application Settings → Modules → Additional module paths*,
redémarrer Slicer, ouvrir **HandsHover**, cliquer **Rafraîchir** puis **Activer**.
Au premier lancement, le module demande l'autorisation d'installer MediaPipe et propose
de télécharger le modèle de suivi des mains (ou de choisir le fichier `hand_landmarker.task` du prototype).
Outil de recherche, non validé pour un usage clinique.
