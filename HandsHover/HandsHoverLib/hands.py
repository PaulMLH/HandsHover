"""Hand analysis from the 21 MediaPipe landmarks: palm position, pose
(open hand, "OK" pinch, fist, index finger, V, thumb up) and choice of the controlling hand.

Pure Python, no dependency. Landmarks are [x, y, z] lists.
Image coordinates are normalized (0..1) on a MIRRORED image (like a selfie);
y grows downwards.
"""

import math

WRIST = 0
THUMB_MCP = 2
THUMB_TIP = 4
INDEX_TIP = 8
MIDDLE_MCP = 9

FINGERS = {
    "index": (5, 6, 7, 8),
    "middle": (9, 10, 11, 12),
    "ring": (13, 14, 15, 16),
    "pinky": (17, 18, 19, 20),
}
PALM_POINTS = (0, 5, 9, 13, 17)

# Segments used to draw the hand skeleton on the camera preview.
HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
)

POSES = ("open", "pinch", "fist", "index", "v", "thumb_up", "other")

POSE_LABELS = {
    "open": "open",
    "pinch": "pinch",
    "fist": "fist",
    "index": "index",
    "v": "V",
    "thumb_up": "thumb up",
    "other": "?",
}


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    return math.sqrt(_dot(a, a))


def _dist(a, b):
    return _norm(_sub(a, b))


def palm_center(landmarks):
    """Palm center (mean of the wrist and the 4 finger base joints).
    Stable when fingers bend: this is the point used for movements."""
    n = float(len(PALM_POINTS))
    x = sum(landmarks[i][0] for i in PALM_POINTS) / n
    y = sum(landmarks[i][1] for i in PALM_POINTS) / n
    return x, y


def finger_state(points, finger):
    """'ext' (extended), 'fold' (folded) or 'amb' (ambiguous) for a long finger."""
    mcp, pip, _dip, tip = (points[i] for i in finger)
    wrist = points[WRIST]
    v1 = _sub(mcp, pip)
    v2 = _sub(tip, pip)
    denom = _norm(v1) * _norm(v2)
    cos_pip = _dot(v1, v2) / denom if denom > 1e-9 else 0.0  # -1 = straight finger
    d_pip = _dist(wrist, pip)
    ratio = _dist(wrist, tip) / d_pip if d_pip > 1e-9 else 1.0
    if cos_pip < -0.75 and ratio > 1.1:
        return "ext"
    if cos_pip > -0.3 or ratio < 1.0:
        return "fold"
    return "amb"


def thumb_extended(points, palm):
    """Thumb out: its tip is far from the palm center (> 0.8 palm length)."""
    n = float(len(PALM_POINTS))
    center = tuple(sum(points[i][k] for i in PALM_POINTS) / n for k in range(3))
    return _dist(points[THUMB_TIP], center) / palm > 0.8


def thumb_points_up(image_landmarks):
    """Thumb pointing up in the image: tip clearly above its base, mostly vertical."""
    tip = image_landmarks[THUMB_TIP]
    base = image_landmarks[THUMB_MCP]
    dx = tip[0] - base[0]
    dy = base[1] - tip[1]          # > 0 when the tip is above the base
    return dy > 0 and dy > abs(dx)   # within 45° of vertical


class HandClassifier:
    """Classifies the pose of a hand. Keeps a state for the pinch hysteresis
    (closing threshold tighter than opening threshold)."""

    def __init__(self, pinch_on=0.30, pinch_off=0.45):
        self.pinch_on = pinch_on
        self.pinch_off = pinch_off
        self.pinching = False

    def reset(self):
        self.pinching = False

    def classify(self, landmarks, world_landmarks=None):
        # World landmarks (meters, hand-centered) do not depend on perspective:
        # they are preferred to recognize the shape of the hand.
        pts = world_landmarks if world_landmarks else landmarks
        palm = _dist(pts[WRIST], pts[MIDDLE_MCP])
        if palm < 1e-9:
            return "other"

        states = {name: finger_state(pts, f) for name, f in FINGERS.items()}
        idx, mid, rng, pky = (states[n] for n in ("index", "middle", "ring", "pinky"))

        pinch_ratio = _dist(pts[THUMB_TIP], pts[INDEX_TIP]) / palm
        if self.pinching:
            self.pinching = pinch_ratio < self.pinch_off
        else:
            self.pinching = pinch_ratio < self.pinch_on

        others_ext = sum(s == "ext" for s in (mid, rng, pky))
        if self.pinching and others_ext >= 2:
            return "pinch"  # "OK" sign

        four = (idx, mid, rng, pky)
        n_ext = sum(s == "ext" for s in four)
        n_fold = sum(s == "fold" for s in four)

        if n_ext == 4 or (n_ext == 3 and "fold" not in four):
            return "open"
        if idx == "ext" and mid == "ext" and rng != "ext" and pky != "ext":
            return "v"
        if idx == "ext" and mid != "ext" and rng != "ext" and pky != "ext" and (mid == "fold" or rng == "fold"):
            return "index"
        if n_ext == 0 and n_fold >= 3:
            if thumb_extended(pts, palm):
                # Fist with the thumb out: "thumb up" only if it points up,
                # otherwise ambiguous (not a clean fist either).
                return "thumb_up" if thumb_points_up(landmarks) else "other"
            return "fist"
        return "other"


class ControlTracker:
    """Chooses the controlling hand.

    The first hand seen keeps control as long as it stays in view (followed from one
    frame to the next by its position). The other hand, if any, is "secondary": it is
    only used for two-handed gestures. If the controlling hand disappears for more than
    `memory_s`, the remaining hand takes control.
    """

    def __init__(self, max_jump=0.25, memory_s=0.4):
        self.max_jump = max_jump
        self.memory_s = memory_s
        self.reset()

    def reset(self):
        self.pos = None
        self.t = None

    def assign(self, t, hands):
        hands = sorted(hands, key=lambda h: -h.get("score", 0.0))[:2]
        centers = [palm_center(h["lm"]) for h in hands]
        ctrl = None
        remembered = self.pos is not None and t - self.t <= self.memory_s
        if remembered and hands:
            dists = [math.hypot(c[0] - self.pos[0], c[1] - self.pos[1]) for c in centers]
            i = min(range(len(hands)), key=lambda k: dists[k])
            if dists[i] <= self.max_jump:
                ctrl = i
        if ctrl is None and hands and not remembered:
            ctrl = 0   # no recent controlling hand: the most confident one takes control
        result = {}
        if ctrl is not None:
            result["C"] = hands[ctrl]
            self.pos = centers[ctrl]
            self.t = t
        others = [h for k, h in enumerate(hands) if k != ctrl]
        if others:
            result["S"] = others[0]
        return result
