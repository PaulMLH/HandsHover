"""Analyse d'une main à partir des 21 points MediaPipe : position de la paume,
pose (main ouverte, pince « OK », poing, index levé, V) et attribution gauche / droite.

Python pur, aucune dépendance. Les points sont des listes [x, y, z].
Les coordonnées « image » sont normalisées (0..1) sur une image EN MIROIR
(comme un selfie) : la main gauche de l'utilisateur apparaît à gauche.
"""

import math

WRIST = 0
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

# Segments pour dessiner le squelette de la main dans l'aperçu.
HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
)

POSES = ("open", "pinch", "fist", "index", "v", "other")

POSE_LABELS_FR = {
    "open": "ouverte",
    "pinch": "pince",
    "fist": "poing",
    "index": "index",
    "v": "V",
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
    """Centre de la paume (moyenne du poignet et des 4 articulations de base des doigts).
    Stable quand les doigts se plient : c'est le point utilisé pour les déplacements."""
    n = float(len(PALM_POINTS))
    x = sum(landmarks[i][0] for i in PALM_POINTS) / n
    y = sum(landmarks[i][1] for i in PALM_POINTS) / n
    return x, y


def finger_state(points, finger):
    """'ext' (tendu), 'fold' (replié) ou 'amb' (ambigu) pour un doigt long."""
    mcp, pip, _dip, tip = (points[i] for i in finger)
    wrist = points[WRIST]
    v1 = _sub(mcp, pip)
    v2 = _sub(tip, pip)
    denom = _norm(v1) * _norm(v2)
    cos_pip = _dot(v1, v2) / denom if denom > 1e-9 else 0.0  # -1 = doigt droit
    d_pip = _dist(wrist, pip)
    ratio = _dist(wrist, tip) / d_pip if d_pip > 1e-9 else 1.0
    if cos_pip < -0.75 and ratio > 1.1:
        return "ext"
    if cos_pip > -0.3 or ratio < 1.0:
        return "fold"
    return "amb"


class HandClassifier:
    """Classe la pose d'une main. Garde un état pour l'hystérésis de la pince
    (seuil de fermeture plus serré que le seuil d'ouverture)."""

    def __init__(self, pinch_on=0.30, pinch_off=0.45):
        self.pinch_on = pinch_on
        self.pinch_off = pinch_off
        self.pinching = False

    def reset(self):
        self.pinching = False

    def classify(self, landmarks, world_landmarks=None):
        # Les points « monde » (en mètres, centrés sur la main) sont indépendants
        # de la perspective : on les préfère pour reconnaître la forme de la main.
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
            return "pinch"  # signe « OK »

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
            return "fist"
        return "other"


class SideTracker:
    """Décide quelle main est la gauche et laquelle est la droite.

    - Deux mains : celle de gauche dans l'image (en miroir) est la main gauche.
    - Une main : on garde son identité si elle est proche de sa dernière position
      (évite qu'elle change de côté en franchissant le milieu de l'image) ;
      sinon, on se fie au côté de l'image.
    - `invert` échange gauche et droite (caméra déjà en miroir, par exemple).
    """

    def __init__(self, max_jump=0.2, memory_s=0.5):
        self.max_jump = max_jump
        self.memory_s = memory_s
        self.last = {}

    def reset(self):
        self.last = {}

    def assign(self, t, hands, invert=False):
        hands = sorted(hands, key=lambda h: -h.get("score", 0.0))[:2]
        centers = [palm_center(h["lm"]) for h in hands]
        result = {}
        if len(hands) == 2:
            left_first = centers[0][0] <= centers[1][0]
            order = (0, 1) if left_first else (1, 0)
            sides = ("R", "L") if invert else ("L", "R")
            result[sides[0]] = hands[order[0]]
            result[sides[1]] = hands[order[1]]
        elif len(hands) == 1:
            cx, cy = centers[0]
            best = None
            for side, (tl, xl, yl) in self.last.items():
                if t - tl <= self.memory_s:
                    d = math.hypot(cx - xl, cy - yl)
                    if d <= self.max_jump and (best is None or d < best[1]):
                        best = (side, d)
            if best is not None:
                side = best[0]
            else:
                side = "L" if cx < 0.5 else "R"
                if invert:
                    side = "R" if side == "L" else "L"
            result[side] = hands[0]
        for side, h in result.items():
            cx, cy = palm_center(h["lm"])
            self.last[side] = (t, cx, cy)
        return result
