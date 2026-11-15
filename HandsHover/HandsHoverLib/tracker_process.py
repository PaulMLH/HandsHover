"""Processus de suivi des mains, lancé par le module avec PythonSlicer.

Il tourne à part de Slicer : la caméra et MediaPipe ne ralentissent pas l'interface,
OpenCV n'est jamais chargé dans Slicer, et si Slicer se ferme ou plante, l'entrée
standard se ferme et ce processus s'arrête en libérant la caméra.

Sortie : une ligne JSON par message sur la sortie standard.
    {"type": "status", "message": ...}
    {"type": "ready", "width": w, "height": h}
    {"type": "frame", "t": secondes, "fps": f, "hands": [{"lm": [[x,y,z]x21], "wlm": [...], "label": "Left", "score": 0.98}]}
    {"type": "preview", "jpg": base64}
    {"type": "cameras", "cameras": [{"index": i, "backend": b, "name": "..."}]}
    {"type": "error", "message": ...}
Entrée : commandes texte, une par ligne : "preview 1", "preview 0", "quit".

L'image est retournée horizontalement (vue « miroir ») avant l'analyse.
"""

import argparse
import base64
import json
import sys
import threading
import time

_out_lock = threading.Lock()


def emit(obj):
    line = json.dumps(obj, separators=(",", ":"))
    with _out_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def _default_backend(cv2):
    # Sous Windows, DirectShow liste aussi les caméras virtuelles (NVIDIA Broadcast, OBS…)
    if sys.platform.startswith("win"):
        return cv2.CAP_DSHOW
    return cv2.CAP_ANY


def list_cameras():
    import cv2
    backend = _default_backend(cv2)
    cameras = []
    try:
        from cv2_enumerate_cameras import enumerate_cameras
        for info in enumerate_cameras(backend):
            cameras.append({"index": int(info.index), "backend": int(info.backend), "name": str(info.name)})
    except Exception:
        cameras = []
    if not cameras:
        # Repli : on teste les 6 premiers numéros (sans nom)
        for i in range(6):
            cap = cv2.VideoCapture(i, backend)
            if cap is not None and cap.isOpened():
                ok, _ = cap.read()
                if ok:
                    cameras.append({"index": i, "backend": int(backend), "name": f"Caméra {i}"})
            if cap is not None:
                cap.release()
    emit({"type": "cameras", "cameras": cameras})


class Commands:
    def __init__(self, preview):
        self.preview = preview
        self.running = True
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for line in sys.stdin:
                cmd = line.strip().lower()
                if cmd == "quit":
                    break
                if cmd.startswith("preview"):
                    self.preview = cmd.endswith("1")
        except Exception:
            pass
        # Entrée fermée (Slicer arrêté ou « quit ») : on s'arrête
        self.running = False


def _round_points(points, nd=4):
    return [[round(p.x, nd), round(p.y, nd), round(p.z, nd)] for p in points]


def track(args):
    emit({"type": "status", "message": "Chargement de MediaPipe…"})
    import cv2
    import mediapipe as mp

    # Modèle d'abord : en cas d'erreur, la caméra n'est même pas ouverte
    vision = mp.tasks.vision
    options = vision.HandLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=args.model),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=args.detection_confidence,
        min_hand_presence_confidence=args.presence_confidence,
        min_tracking_confidence=args.tracking_confidence,
    )
    landmarker = vision.HandLandmarker.create_from_options(options)

    emit({"type": "status", "message": "Ouverture de la caméra…"})
    backend = args.backend if args.backend >= 0 else _default_backend(cv2)
    cap = cv2.VideoCapture(args.camera, backend)
    if not cap.isOpened():
        landmarker.close()
        emit({"type": "error", "message": f"Impossible d'ouvrir la caméra {args.camera} "
                                          "(débranchée ou utilisée par un autre logiciel ?)."})
        return 2
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    cap.set(cv2.CAP_PROP_FPS, 30)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass

    commands = Commands(args.preview)
    t0 = time.monotonic()
    last_ts = -1
    last_preview = 0.0
    fps = 0.0
    last_frame_time = None
    failures = 0
    ready_sent = False

    try:
        while commands.running:
            ok, frame = cap.read()
            if not ok or frame is None:
                failures += 1
                if failures > 100:
                    emit({"type": "error", "message": "La caméra ne renvoie plus d'image."})
                    return 3
                time.sleep(0.01)
                continue
            failures = 0
            frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]
            if not ready_sent:
                emit({"type": "ready", "width": w, "height": h})
                ready_sent = True

            now = time.monotonic()
            ts = int((now - t0) * 1000)
            if ts <= last_ts:
                ts = last_ts + 1
            last_ts = ts

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = landmarker.detect_for_video(image, ts)

            hands = []
            world = getattr(result, "hand_world_landmarks", None) or []
            handedness = getattr(result, "handedness", None) or []
            for i, lms in enumerate(result.hand_landmarks or []):
                hand = {"lm": _round_points(lms), "wlm": None, "label": "", "score": 0.0}
                if i < len(world) and world[i]:
                    hand["wlm"] = _round_points(world[i], 5)
                if i < len(handedness) and handedness[i]:
                    hand["label"] = handedness[i][0].category_name
                    hand["score"] = round(float(handedness[i][0].score), 3)
                hands.append(hand)

            if last_frame_time is not None:
                dt = now - last_frame_time
                if dt > 0:
                    fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps else 1.0 / dt
            last_frame_time = now
            emit({"type": "frame", "t": round(now, 4), "fps": round(fps, 1), "hands": hands})

            if commands.preview and now - last_preview >= 1.0 / args.preview_fps:
                last_preview = now
                pw = args.preview_width
                small = cv2.resize(frame, (pw, max(1, int(pw * h / w))))
                ok, buf = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                if ok:
                    emit({"type": "preview", "jpg": base64.b64encode(buf.tobytes()).decode("ascii")})
    finally:
        cap.release()
        try:
            landmarker.close()
        except Exception:
            pass
    return 0


def main():
    parser = argparse.ArgumentParser(description="Suivi des mains pour HandsHover")
    parser.add_argument("--list-cameras", action="store_true")
    parser.add_argument("--model", default="")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--backend", type=int, default=-1)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--preview-fps", type=float, default=10.0)
    parser.add_argument("--preview-width", type=int, default=320)
    parser.add_argument("--detection-confidence", type=float, default=0.5)
    parser.add_argument("--presence-confidence", type=float, default=0.5)
    parser.add_argument("--tracking-confidence", type=float, default=0.5)
    args = parser.parse_args()

    try:
        if args.list_cameras:
            list_cameras()
            return 0
        return track(args)
    except Exception as e:  # message lisible pour le module
        emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
