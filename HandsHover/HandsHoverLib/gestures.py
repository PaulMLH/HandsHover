"""Moteur de gestes : transforme les poses et positions des mains en commandes.

Python pur, sans Slicer : testable seul (voir Tests/test_engine.py).

Le moteur produit des « événements » (tuples) que le module Slicer exécute :
    ("mode", mode, paused)            mode = "3D" | "Red" | "Green" | "Yellow" | "Mouse"
    ("progress", key, fraction)       jauge d'un geste maintenu (key None = aucune)
    ("action", kind | None)           action continue en cours (pour l'affichage)
    ("scroll", view, n)               n coupes (+ = vers le haut de la main)
    ("wl", view, dx, dy)              contraste / luminosité (déplacements normalisés)
    ("wl_reset", view)
    ("rotate", d_azimuth, d_elevation)   en degrés
    ("pan", dx, dy)                   déplacement normalisé de la main
    ("zoom", factor)                  > 1 = zoom avant
    ("recenter",)
    ("pointer", u, v)                 position du pointeur dans la fenêtre (0..1)
    ("mouse_button", "left"|"right", "down"|"up")
    ("mouse_click", "left"|"right")
    ("wheel", n)                      crans de molette (+ = vers le haut)
"""

import math
from dataclasses import dataclass, field

from .filters import OneEuroFilter2D
from .hands import HandClassifier, SideTracker, palm_center

VIEW_ORDER = ("3D", "Red", "Green", "Yellow")
SLICE_VIEWS = ("Red", "Green", "Yellow")
MOUSE = "Mouse"

HOLD_LABELS_FR = {
    "pause": "Pause / reprise",
    "next_view": "Vue suivante",
    "mouse_toggle": "Mode souris",
    "reset": "Réinitialiser",
}

# (côté, pose) -> action continue, selon le mode
ENGAGE_3D = {
    ("L", "fist"): "rotate",
    ("R", "fist"): "pan",
    ("L", "pinch"): "zoom_fast",
    ("R", "pinch"): "zoom_fine",
}
ENGAGE_SLICE = {
    ("L", "pinch"): "scroll_fast",
    ("R", "pinch"): "scroll_slow",
    ("R", "fist"): "wl",
}
ENGAGE_MOUSE = {
    ("R", "pinch"): "lbutton",
    ("L", "pinch"): "rbutton",
}

ACTION_LABELS_FR = {
    "rotate": "rotation",
    "pan": "déplacement",
    "zoom_fast": "zoom rapide",
    "zoom_fine": "zoom fin",
    "scroll_fast": "défilement rapide",
    "scroll_slow": "défilement lent",
    "wl": "contraste",
    "lbutton": "clic gauche",
    "rbutton": "clic droit / molette",
}


@dataclass
class EngineConfig:
    # Durées des gestes maintenus (secondes)
    hold_pause_s: float = 1.0
    hold_next_view_s: float = 0.5
    hold_mouse_s: float = 0.5
    hold_reset_s: float = 1.0
    # Défilement : course de main (fraction de la hauteur d'image) pour une coupe
    scroll_step_slow: float = 0.02
    scroll_step_fast: float = 0.005
    invert_scroll: bool = False
    # Vue 3D
    rotate_gain_deg: float = 180.0      # degrés pour une traversée complète de l'image
    pan_gain: float = 1.0
    zoom_gain_fast: float = 4.0
    zoom_gain_fine: float = 1.5
    # Souris
    click_max_s: float = 0.4
    drag_threshold: float = 0.03
    wheel_threshold: float = 0.02
    wheel_step: float = 0.03
    mouse_zone: tuple = (0.40, 0.15, 0.95, 0.80)   # gauche, haut, droite, bas (image en miroir)


@dataclass
class HandObs:
    pose: str
    x: float
    y: float


class PoseDebouncer:
    """Une pose ne devient « stable » qu'après avoir été vue `delay` secondes :
    évite qu'une image mal reconnue relâche une pince ou déclenche une action."""

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

    # ------------------------------------------------------------------ état
    def reset(self):
        """Remet à zéro. Renvoie les événements nécessaires (relâcher un bouton…)."""
        events = []
        action = getattr(self, "action", None)
        if action and action["kind"] == "lbutton" and action.get("dragging"):
            events.append(("mouse_button", "left", "up"))
        self.mode = "3D"
        self.prev_view = "3D"
        self.paused = False
        self.action = None
        self.holds = {}
        self.hold_lock = False
        events.append(("mode", self.mode, self.paused))
        return events

    def release(self):
        """Termine proprement l'action en cours (désactivation, perte caméra)."""
        events = []
        if self.action is not None:
            if self.action["kind"] == "lbutton" and self.action.get("dragging"):
                events.append(("mouse_button", "left", "up"))
            self.action = None
            events.append(("action", None))
        self.holds = {}
        return events

    # ------------------------------------------------------------- principal
    def update(self, t, hands):
        """hands : dict {"L": HandObs, "R": HandObs} (côté absent = main non vue)."""
        events = []
        left = hands.get("L")
        right = hands.get("R")

        # 1. Poursuivre ou terminer l'action continue en cours
        if self.action is not None:
            owner = hands.get(self.action["side"])
            if owner is None or owner.pose != self.action["pose"]:
                events += self._end_action(t, owner)
            else:
                events += self._continue_action(owner)

        # 2. Gestes maintenus (seulement sans action en cours)
        fired = None
        progress = (None, 0.0)
        if self.action is None:
            fired, progress = self._update_holds(t, left, right)
        else:
            self.holds = {}
        events.append(("progress", progress[0], progress[1]))
        if fired:
            events += self._fire(fired)

        # 3. Nouvelle action continue
        if self.action is None and not self.paused and not fired:
            events += self._try_engage(t, hands)

        # 4. Pointeur du mode souris
        if (self.mode == MOUSE and not self.paused and self.action is None
                and right is not None and right.pose != "pinch"):
            u, v = self._to_zone(right.x, right.y)
            events.append(("pointer", u, v))
        return events

    # ---------------------------------------------------- gestes maintenus
    def _update_holds(self, t, left, right):
        lp = left.pose if left else None
        rp = right.pose if right else None

        # Après un geste déclenché, attendre que plus aucune main ne fasse index / V
        if self.hold_lock:
            if lp not in ("index", "v") and rp not in ("index", "v"):
                self.hold_lock = False
            else:
                self.holds = {}
                return None, (None, 0.0)

        cands = {}
        if lp == "index" and rp == "index":
            cands["pause"] = self.cfg.hold_pause_s
        elif not self.paused:
            if lp == "index" and self.mode != MOUSE:
                cands["next_view"] = self.cfg.hold_next_view_s
            if lp == "v":
                cands["mouse_toggle"] = self.cfg.hold_mouse_s
            if rp == "index" and self.mode != MOUSE:
                cands["reset"] = self.cfg.hold_reset_s

        for key in list(self.holds):
            if key not in cands:
                del self.holds[key]

        best = (None, 0.0)
        for key, duration in cands.items():
            start = self.holds.setdefault(key, t)
            frac = (t - start) / duration if duration > 0 else 1.0
            if frac >= 1.0:
                self.holds = {}
                self.hold_lock = True
                return key, (key, 1.0)
            if frac > best[1] or best[0] is None:
                best = (key, frac)
        return None, best

    def _fire(self, key):
        events = []
        if key == "pause":
            self.paused = not self.paused
            events.append(("mode", self.mode, self.paused))
        elif key == "next_view":
            if self.mode in VIEW_ORDER:
                i = VIEW_ORDER.index(self.mode)
                self.mode = VIEW_ORDER[(i + 1) % len(VIEW_ORDER)]
                events.append(("mode", self.mode, self.paused))
        elif key == "mouse_toggle":
            if self.mode == MOUSE:
                self.mode = self.prev_view
            else:
                self.prev_view = self.mode
                self.mode = MOUSE
            events.append(("mode", self.mode, self.paused))
        elif key == "reset":
            if self.mode == "3D":
                events.append(("recenter",))
            elif self.mode in SLICE_VIEWS:
                events.append(("wl_reset", self.mode))
        return events

    # ---------------------------------------------------- actions continues
    def _engage_table(self):
        if self.mode == "3D":
            return ENGAGE_3D
        if self.mode in SLICE_VIEWS:
            return ENGAGE_SLICE
        if self.mode == MOUSE:
            return ENGAGE_MOUSE
        return {}

    def _try_engage(self, t, hands):
        table = self._engage_table()
        for side in ("L", "R"):
            h = hands.get(side)
            if h is None:
                continue
            kind = table.get((side, h.pose))
            if kind:
                self.action = {
                    "kind": kind, "side": side, "pose": h.pose, "t0": t,
                    "x0": h.x, "y0": h.y, "lx": h.x, "ly": h.y,
                    "acc": 0.0, "dragging": False, "wheeling": False,
                }
                return [("action", kind)]
        return []

    def _continue_action(self, h):
        a = self.action
        cfg = self.cfg
        dx = h.x - a["lx"]
        dy = h.y - a["ly"]
        a["lx"], a["ly"] = h.x, h.y
        kind = a["kind"]
        events = []

        if kind in ("scroll_fast", "scroll_slow"):
            step = cfg.scroll_step_fast if kind == "scroll_fast" else cfg.scroll_step_slow
            sign = 1.0 if cfg.invert_scroll else -1.0   # main qui monte (dy < 0) = coupes +
            a["acc"] += sign * dy / max(step, 1e-6)
            n = int(a["acc"])
            if n:
                a["acc"] -= n
                events.append(("scroll", self.mode, n))
        elif kind == "wl":
            if dx or dy:
                events.append(("wl", self.mode, dx, dy))
        elif kind == "rotate":
            if dx or dy:
                events.append(("rotate", -dx * cfg.rotate_gain_deg, dy * cfg.rotate_gain_deg))
        elif kind == "pan":
            if dx or dy:
                events.append(("pan", dx * cfg.pan_gain, dy * cfg.pan_gain))
        elif kind in ("zoom_fast", "zoom_fine"):
            k = cfg.zoom_gain_fast if kind == "zoom_fast" else cfg.zoom_gain_fine
            if dy:
                events.append(("zoom", math.exp(-dy * k)))
        elif kind == "lbutton":
            if not a["dragging"]:
                moved = math.hypot(h.x - a["x0"], h.y - a["y0"])
                if moved > cfg.drag_threshold:
                    a["dragging"] = True
                    # bouton enfoncé à la position figée au début de la pince
                    events.append(("mouse_button", "left", "down"))
            if a["dragging"]:
                u, v = self._to_zone(h.x, h.y)
                events.append(("pointer", u, v))
        elif kind == "rbutton":
            if not a["wheeling"] and abs(h.y - a["y0"]) > cfg.wheel_threshold:
                a["wheeling"] = True
                a["acc"] = 0.0
            if a["wheeling"]:
                a["acc"] += -dy / max(cfg.wheel_step, 1e-6)
                n = int(a["acc"])
                if n:
                    a["acc"] -= n
                    events.append(("wheel", n))
        return events

    def _end_action(self, t, owner):
        a = self.action
        self.action = None
        events = [("action", None)]
        duration = t - a["t0"]
        released = owner is not None   # pince relâchée (et pas main perdue)
        if a["kind"] == "lbutton":
            if a["dragging"]:
                events.append(("mouse_button", "left", "up"))
            elif released and duration <= self.cfg.click_max_s:
                events.append(("mouse_click", "left"))
        elif a["kind"] == "rbutton":
            if not a["wheeling"] and released and duration <= self.cfg.click_max_s:
                events.append(("mouse_click", "right"))
        return events

    def _to_zone(self, x, y):
        x0, y0, x1, y1 = self.cfg.mouse_zone
        u = (x - x0) / max(x1 - x0, 1e-6)
        v = (y - y0) / max(y1 - y0, 1e-6)
        return min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0)


@dataclass
class PipelineConfig:
    pose_confirm_s: float = 0.08
    missing_grace_s: float = 0.15   # une main perdue moins longtemps que ça garde son état
    filter_min_cutoff: float = 1.5
    filter_beta: float = 4.0
    pinch_on: float = 0.30
    pinch_off: float = 0.45
    engine: EngineConfig = field(default_factory=EngineConfig)


class GesturePipeline:
    """Chaîne complète : mains brutes (sortie MediaPipe) -> côté -> pose -> lissage -> moteur."""

    def __init__(self, config=None):
        self.cfg = config or PipelineConfig()
        self.invert_hands = False
        self.sides = SideTracker()
        self.classifiers = {s: HandClassifier(self.cfg.pinch_on, self.cfg.pinch_off) for s in ("L", "R")}
        self.debouncers = {s: PoseDebouncer(self.cfg.pose_confirm_s) for s in ("L", "R")}
        self.filters = {s: OneEuroFilter2D(self.cfg.filter_min_cutoff, self.cfg.filter_beta) for s in ("L", "R")}
        self.engine = GestureEngine(self.cfg.engine)
        self._last_obs = {}

    def reset(self):
        self.sides.reset()
        self._last_obs = {}
        for s in ("L", "R"):
            self.classifiers[s].reset()
            self.debouncers[s].reset()
            self.filters[s].reset()
        return self.engine.reset()

    def set_filter(self, min_cutoff=None, beta=None):
        for f in self.filters.values():
            f.configure(min_cutoff, beta)

    def process(self, t, raw_hands):
        """raw_hands : liste de dict {"lm": 21x[x,y,z], "wlm": 21x[x,y,z] | None, "score": float}.

        Renvoie (événements, mains attribuées {côté: main brute}, infos {côté: dict})."""
        assigned = self.sides.assign(t, raw_hands, self.invert_hands)
        observations = {}
        info = {}
        for side in ("L", "R"):
            hand = assigned.get(side)
            if hand is None:
                last = self._last_obs.get(side)
                if last is not None and t - last[0] <= self.cfg.missing_grace_s:
                    # Perte de suivi très brève : on garde la dernière observation
                    observations[side] = last[1]
                    continue
                self._last_obs.pop(side, None)
                self.debouncers[side].update(None, t)
                self.classifiers[side].reset()
                self.filters[side].reset()
                continue
            raw_pose = self.classifiers[side].classify(hand["lm"], hand.get("wlm"))
            pose = self.debouncers[side].update(raw_pose, t)
            cx, cy = palm_center(hand["lm"])
            fx, fy = self.filters[side](t, cx, cy)
            info[side] = {"raw": raw_pose, "pose": pose, "x": fx, "y": fy}
            if pose is not None:
                obs = HandObs(pose, fx, fy)
                observations[side] = obs
                self._last_obs[side] = (t, obs)
        events = self.engine.update(t, observations)
        return events, assigned, info
