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

"Pinch" = OK sign (thumb and index touching, the other three fingers extended).
Hands are identified by their position in the (mirrored) image; a setting swaps left/right.

| Where | Gesture | Effect |
|---|---|---|
| Everywhere | Both index fingers up, 1 s | Pause / resume |
| Everywhere | Left index up, 0.5 s | Next controlled view: 3D → Red → Green → Yellow |
| Everywhere | Left V, 0.5 s | Enter / leave mouse mode |
| 3D | Left fist + move | Rotate |
| 3D | Right fist + move | Pan |
| 3D | Left pinch + up/down | Fast zoom |
| 3D | Right pinch + up/down | Fine zoom |
| 3D | Right index up, 1 s | Recenter |
| 2D | Left pinch + up/down | Fast scrolling |
| 2D | Right pinch + up/down | Slow scrolling |
| 2D | Right fist + move | Window (horizontal) / level (vertical) |
| 2D | Right index up, 1 s | Restore original window/level |
| Mouse | Right hand | Pointer (limited to the Slicer window) |
| Mouse | Short right pinch | Left click (two quick ones = double click) |
| Mouse | Right pinch + move | Drag |
| Mouse | Short left pinch | Right click |
| Mouse | Left pinch + up/down | Mouse wheel (Windows) |

Only one continuous action runs at a time: the first hand engaged keeps control.

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

## Known limitations (v0.1)

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
