"""Gesture engine (one-handed): turns hand poses and positions into commands.

Pure Python, no Slicer dependency: testable on its own (see Tests/run_tests.py).

One hand controls ("C"): the first hand seen, which keeps control as long as it stays
in view. The other hand, if any ("S"), is only used for the two-handed pause gesture.

Gestures
    Everywhere   two closed fists, 1 s      -> pause / resume all commands
                 thumb up, 1 s              -> recenter the controlled view
                 V + horizontal swipe       -> next (right) / previous (left) view
    3D view      fist + up/down             -> zoom
                 pinch + move               -> rotate
                 index finger + move        -> pan
    Slice views  pinch + up/down            -> scroll slices (faster gesture = accelerated)
                 fist + up/down             -> zoom
                 index finger + move        -> window (horizontal) / level (vertical)

Events produced (tuples):
    ("mode", view, paused)            view = "3D" | "Red" | "Green" | "Yellow"
    ("progress", key, fraction)       gauge of a held gesture (key None = none)
    ("action", kind | None)           continuous action in progress (for display)
    ("scroll", view, n)               n slices (+ = hand moving up)
    ("slice_zoom", view, factor)      > 1 = zoom in
    ("wl", view, dx, dy)              window / level (normalized hand displacement)
    ("slice_fit", view)               recenter a slice view
    ("rotate", d_azimuth, d_elevation)   degrees
    ("pan", dx, dy)                   normalized hand displacement
    ("zoom", factor)                  3D view zoom, > 1 = zoom in
    ("recenter",)                     recenter the 3D view
"""

import math
from dataclasses import dataclass, field

from .filters import OneEuroFilter2D
from .hands import ControlTracker, HandClassifier, palm_center

VIEW_ORDER = ("3D", "Red", "Green", "Yellow")
SLICE_VIEWS = ("Red", "Green", "Yellow")
ROLES = ("C", "S")

HOLD_LABELS = {
    "pause": "Pause / resume",
    "recenter": "Recenter view",
}

# controlling-hand pose -> continuous action, per view type
ENGAGE_3D = {"fist": "zoom", "pinch": "rotate", "index": "pan", "v": "swipe"}
ENGAGE_SLICE = {"pinch": "scroll", "fist": "slice_zoom", "index": "wl", "v": "swipe"}

ACTION_LABELS = {
    "zoom": "zoom",
    "rotate": "rotate",
    "pan": "pan",
    "swipe": "change view",
    "scroll": "scroll",
    "slice_zoom": "zoom",
    "wl": "window / level",
}


@dataclass
class EngineConfig:
    # Held gestures (seconds)
    hold_pause_s: float = 1.0
    hold_recenter_s: float = 1.0
    # Scrolling: hand travel (fraction of image height) for one slice, slow gesture
    scroll_step: float = 0.02
    scroll_accel_max: float = 4.0       # maximum multiplier for a fast gesture
    scroll_accel_speed: float = 0.25    # speed (image heights / s) above which scrolling accelerates
    invert_scroll: bool = False
    # Zoom (fist + up/down)
    zoom_gain: float = 3.0
    slice_zoom_gain: float = 3.0
    # 3D view
    rotate_gain_deg: float = 180.0      # degrees for a full traversal of the image
    pan_gain: float = 1.0
    # V swipe
    swipe_distance: float = 0.15        # fraction of image width
    swipe_window_s: float = 0.6


@dataclass
class HandObs:
    pose: str
    x: float
    y: float


class PoseDebouncer:
    """A pose only becomes "stable" after being seen for `delay` seconds: prevents a
    single misrecognized frame from releasing a pinch or triggering an action."""

    def __init__(self, delay=0.08):
        self.delay = delay
        self.reset()

    def reset(self):
        self.stable = None
        self.candidate = None
        self.since = 0.0

    def update(self, raw, t):
        if raw is None:
            self.reset()
            return None
        if raw == self.stable:
            self.candidate = None
            return self.stable
        if raw != self.candidate:
            self.candidate = raw
            self.since = t
        if t - self.since >= self.delay:
            self.stable = raw
            self.candidate = None
        return self.stable


class GestureEngine:
    def __init__(self, config=None):
        self.cfg = config or EngineConfig()
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self):
        events = self.release()
        self.mode = "3D"
        self.paused = False
        self.hold_start = {}
        self.hold_lock = False
        events.append(("mode", self.mode, self.paused))
        return events

    def release(self):
        """Ends the action in progress (disable, pause...)."""
        events = []
        if getattr(self, "action", None) is not None:
            events.append(("action", None))
        self.action = None
        return events

    # --------------------------------------------------------------- main
    def update(self, t, hands):
        """hands: {"C": HandObs (controlling hand), "S": HandObs (other hand)}; missing = not seen."""
        events = []
        c = hands.get("C")
        s = hands.get("S")

        # 1. Held gestures (two fists = pause, thumb up = recenter)
        both_fists = c is not None and s is not None and c.pose == "fist" and s.pose == "fist"
        if both_fists and self.action is not None:
            events += self.release()   # starting the pause gesture cancels the current action
        candidate = None
        if both_fists:
            candidate = "pause"
        elif not self.paused and self.action is None and c is not None and c.pose == "thumb_up":
            candidate = "recenter"
        fired, progress = self._update_holds(t, candidate)
        events.append(("progress", progress[0], progress[1]))
        if fired:
            events += self._fire(fired)

        # 2. Continuous action of the controlling hand
        if self.action is not None:
            a = self.action
            keeps = c is not None and (c.pose == a["pose"] or (a["kind"] == "swipe" and c.pose == "other"))
            if keeps:
                events += self._continue_action(t, c)
            else:
                events += self._end_action()

        if self.action is None and c is not None and not self.paused and not both_fists and not fired:
            events += self._try_engage(t, c)
        return events

    # ------------------------------------------------------- held gestures
    def _update_holds(self, t, candidate):
        if self.hold_lock:
            if candidate is None:
                self.hold_lock = False   # gesture released: can be used again
            self.hold_start = {}
            return None, (None, 0.0)
        if candidate is None:
            self.hold_start = {}
            return None, (None, 0.0)
        duration = self.cfg.hold_pause_s if candidate == "pause" else self.cfg.hold_recenter_s
        start = self.hold_start.get(candidate)
        if start is None:
            self.hold_start = {candidate: t}
            start = t
        frac = (t - start) / duration if duration > 0 else 1.0
        if frac >= 1.0:
            self.hold_start = {}
            self.hold_lock = True   # release before it can fire again
            return candidate, (candidate, 1.0)
        return None, (candidate, frac)

    def _fire(self, key):
        events = []
        if key == "pause":
            self.paused = not self.paused
            events += self.release()
            events.append(("mode", self.mode, self.paused))
        elif key == "recenter":
            if self.mode == "3D":
                events.append(("recenter",))
            else:
                events.append(("slice_fit", self.mode))
        return events

    def _change_view(self, step):
        i = VIEW_ORDER.index(self.mode)
        self.mode = VIEW_ORDER[(i + step) % len(VIEW_ORDER)]
        return [("mode", self.mode, self.paused)]

    # -------------------------------------------------- continuous actions
    def _try_engage(self, t, c):
        table = ENGAGE_3D if self.mode == "3D" else ENGAGE_SLICE
        kind = table.get(c.pose)
        if not kind:
            return []
        self.action = {
            "kind": kind, "pose": c.pose, "t0": t, "lt": t,
            "lx": c.x, "ly": c.y, "acc": 0.0,
            "history": [(t, c.x, c.y)], "swiped": False,
        }
        return [("action", kind)]

    def _continue_action(self, t, c):
        a = self.action
        cfg = self.cfg
        dx = c.x - a["lx"]
        dy = c.y - a["ly"]
        dt = max(t - a["lt"], 1e-3)
        a["lx"], a["ly"], a["lt"] = c.x, c.y, t
        kind = a["kind"]
        events = []

        if kind == "swipe":
            if not a["swiped"]:
                hist = [h for h in a["history"] if t - h[0] <= cfg.swipe_window_s]
                hist.append((t, c.x, c.y))
                a["history"] = hist
                for _t0, x0, y0 in hist:
                    sx, sy = c.x - x0, c.y - y0
                    if abs(sx) >= cfg.swipe_distance and abs(sx) > 2.0 * abs(sy):
                        a["swiped"] = True   # only one view change per V
                        events += self._change_view(1 if sx > 0 else -1)
                        break
        elif kind == "scroll":
            speed = abs(dy) / dt
            accel = min(cfg.scroll_accel_max, max(1.0, speed / max(cfg.scroll_accel_speed, 1e-6)))
            sign = 1.0 if cfg.invert_scroll else -1.0   # hand moving up (dy < 0) = slices +
            a["acc"] += sign * dy * accel / max(cfg.scroll_step, 1e-6)
            n = int(a["acc"])
            if n:
                a["acc"] -= n
                events.append(("scroll", self.mode, n))
        elif kind == "slice_zoom":
            if dy:
                events.append(("slice_zoom", self.mode, math.exp(-dy * cfg.slice_zoom_gain)))
        elif kind == "wl":
            if dx or dy:
                events.append(("wl", self.mode, dx, dy))
        elif kind == "zoom":
            if dy:
                events.append(("zoom", math.exp(-dy * cfg.zoom_gain)))
        elif kind == "rotate":
            if dx or dy:
                events.append(("rotate", -dx * cfg.rotate_gain_deg, dy * cfg.rotate_gain_deg))
        elif kind == "pan":
            if dx or dy:
                events.append(("pan", dx * cfg.pan_gain, dy * cfg.pan_gain))
        return events

    def _end_action(self):
        self.action = None
        return [("action", None)]


@dataclass
class PipelineConfig:
    pose_confirm_s: float = 0.08
    missing_grace_s: float = 0.15   # a hand lost for less than this keeps its state
    filter_min_cutoff: float = 1.5
    filter_beta: float = 4.0
    pinch_on: float = 0.30
    pinch_off: float = 0.45
    engine: EngineConfig = field(default_factory=EngineConfig)


class GesturePipeline:
    """Full chain: raw hands (MediaPipe output) -> role (controlling / other)
    -> pose -> smoothing -> engine."""

    def __init__(self, config=None):
        self.cfg = config or PipelineConfig()
        self.control = ControlTracker()
        self.classifiers = {r: HandClassifier(self.cfg.pinch_on, self.cfg.pinch_off) for r in ROLES}
        self.debouncers = {r: PoseDebouncer(self.cfg.pose_confirm_s) for r in ROLES}
        self.filters = {r: OneEuroFilter2D(self.cfg.filter_min_cutoff, self.cfg.filter_beta) for r in ROLES}
        self.engine = GestureEngine(self.cfg.engine)
        self._last_obs = {}

    def reset(self):
        self.control.reset()
        self._last_obs = {}
        for r in ROLES:
            self.classifiers[r].reset()
            self.debouncers[r].reset()
            self.filters[r].reset()
        return self.engine.reset()

    def set_filter(self, min_cutoff=None, beta=None):
        for f in self.filters.values():
            f.configure(min_cutoff, beta)

    def process(self, t, raw_hands):
        """raw_hands: list of dict {"lm": 21x[x,y,z], "wlm": 21x[x,y,z] | None, "score": float}.

        Returns (events, hands by role {"C"|"S": raw hand}, info {role: dict})."""
        assigned = self.control.assign(t, raw_hands)
        observations = {}
        info = {}
        for role in ROLES:
            hand = assigned.get(role)
            if hand is None:
                last = self._last_obs.get(role)
                if role == "C" and last is not None and t - last[0] <= self.cfg.missing_grace_s:
                    # Very short tracking loss: keep the last observation
                    observations[role] = last[1]
                    continue
                self._last_obs.pop(role, None)
                self.debouncers[role].update(None, t)
                self.classifiers[role].reset()
                self.filters[role].reset()
                continue
            raw_pose = self.classifiers[role].classify(hand["lm"], hand.get("wlm"))
            pose = self.debouncers[role].update(raw_pose, t)
            cx, cy = palm_center(hand["lm"])
            fx, fy = self.filters[role](t, cx, cy)
            info[role] = {"raw": raw_pose, "pose": pose, "x": fx, "y": fy}
            if pose is not None:
                obs = HandObs(pose, fx, fy)
                observations[role] = obs
                self._last_obs[role] = (t, obs)
        events = self.engine.update(t, observations)
        return events, assigned, info
