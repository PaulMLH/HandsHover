"""Synthetic hands (21 landmarks) to test the gesture engine without a camera.

Simple model: palm in the XY plane, fingers towards +Y (up in the image), flexion bends
the fingers towards +Z (palm side, facing the camera). Units: meters (like MediaPipe
world landmarks).
"""

import math

MCP = {
    "index": (0.025, 0.085, 0.0),
    "middle": (0.005, 0.090, 0.0),
    "ring": (-0.015, 0.085, 0.0),
    "pinky": (-0.032, 0.075, 0.0),
}
LENGTHS = {
    "index": (0.040, 0.025, 0.020),
    "middle": (0.045, 0.028, 0.022),
    "ring": (0.042, 0.027, 0.021),
    "pinky": (0.032, 0.020, 0.018),
}
ORDER = ("index", "middle", "ring", "pinky")
THUMB_CMC = (0.030, 0.020, 0.010)
THUMB_LENGTHS = (0.035, 0.030, 0.025)

EXT = (5, 5, 5)
FOLD = (90, 100, 70)
OK_CURL = (40, 60, 40)

POSE_FLEXION = {
    "open": {f: EXT for f in ORDER},
    "fist": {f: FOLD for f in ORDER},
    "index": {"index": EXT, "middle": FOLD, "ring": FOLD, "pinky": FOLD},
    "v": {"index": EXT, "middle": EXT, "ring": FOLD, "pinky": FOLD},
    "pinch": {"index": OK_CURL, "middle": EXT, "ring": EXT, "pinky": EXT},
    "thumb_up": {f: FOLD for f in ORDER},
}


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _lerp(a, b, f):
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f)


def _finger(mcp, lengths, flex_deg):
    pts = [mcp]
    theta = 0.0
    p = mcp
    for length, flex in zip(lengths, flex_deg):
        theta += math.radians(flex)
        d = (0.0, math.cos(theta), math.sin(theta))
        p = _add(p, _scale(d, length))
        pts.append(p)
    return pts  # MCP, PIP, DIP, TIP


def _thumb_towards(tip):
    """Thumb from the CMC joint to `tip`, intermediate joints interpolated."""
    cmc = THUMB_CMC
    mcp = _lerp(cmc, tip, 0.40)
    ip = _lerp(cmc, tip, 0.72)
    return [cmc, mcp, ip, tip]


def local_hand(pose):
    flex = POSE_FLEXION[pose]
    fingers = {f: _finger(MCP[f], LENGTHS[f], flex[f]) for f in ORDER}
    if pose == "open":
        tip = _add(THUMB_CMC, _scale((0.8, 0.6, 0.0), sum(THUMB_LENGTHS)))
    elif pose == "thumb_up":
        tip = _add(THUMB_CMC, _scale((0.45, 0.88, 0.15), sum(THUMB_LENGTHS)))
    elif pose == "pinch":
        tip = _add(fingers["index"][3], (0.006, 0.0, 0.0))
    else:  # thumb folded over the fingers
        tip = (0.0, 0.055, 0.040)
    thumb = _thumb_towards(tip)
    pts = [(0.0, 0.0, 0.0)] + thumb
    for f in ORDER:
        pts += fingers[f]
    return pts  # 21 points


def _rotate(p, roll=0.0, yaw=0.0, pitch=0.0):
    x, y, z = p
    # roll: around Z (in the image plane)
    c, s = math.cos(math.radians(roll)), math.sin(math.radians(roll))
    x, y = c * x - s * y, s * x + c * y
    # yaw: around Y
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    x, z = c * x + s * z, -s * x + c * z
    # pitch: around X
    c, s = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    y, z = c * y - s * z, s * y + c * z
    return (x, y, z)


def make_hand(pose, cx=0.5, cy=0.5, roll=0.0, yaw=0.0, pitch=0.0, scale=2.5, score=0.95):
    """Raw hand in the tracking-process format: {"lm", "wlm", "score"}.
    (cx, cy) = approximate palm-center position in the image (0..1)."""
    local = [_rotate(p, roll, yaw, pitch) for p in local_hand(pose)]
    # shift so that the palm center lands on (cx, cy)
    palm = [local[i] for i in (0, 5, 9, 13, 17)]
    px = sum(p[0] for p in palm) / 5.0
    py = sum(p[1] for p in palm) / 5.0
    lm = [[cx + scale * (p[0] - px), cy - scale * (p[1] - py), scale * p[2]] for p in local]
    wlm = [[p[0], -p[1], p[2]] for p in local]
    return {"lm": lm, "wlm": wlm, "score": score}
