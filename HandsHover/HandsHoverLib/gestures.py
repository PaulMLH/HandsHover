"""Moteur de gestes (v0.2, tout à une main) : transforme les poses et positions des mains
en commandes.

Python pur, sans Slicer : testable seul (voir Tests/run_tests.py).

Une seule main commande (« C ») : la première vue, qui garde le contrôle tant qu'elle reste
dans l'image. L'autre main éventuelle (« S ») ne sert qu'aux deux gestes à deux mains :
    deux poings fermés 1 s   -> pause / réactivation des commandes
    deux mains ouvertes 1 s  -> entrer / sortir du mode souris

Événements produits (tuples) :
    ("mode", mode, paused)            mode = "3D" | "Red" | "Green" | "Yellow" | "Mouse"
    ("progress", key, fraction)       jauge d'un geste maintenu (key None = aucune)
    ("action", kind | None)           action continue en cours (pour l'affichage)
    ("scroll", view, n)               n coupes (+ = main qui monte)
    ("slice_zoom", view, factor)      > 1 = zoom avant sur la coupe
    ("wl", view, dx, dy)              contraste / luminosité (déplacements normalisés)
    ("rotate", d_azimuth, d_elevation)   en degrés
    ("pan", dx, dy)                   déplacement normalisé de la main
    ("zoom", factor)                  zoom de la vue 3D, > 1 = zoom avant
    ("pointer", u, v)                 position du pointeur dans la fenêtre (0..1)
    ("mouse_button", "left"|"right", "down"|"up")
    ("mouse_click", "left"|"right")
    ("wheel", n)                      crans de molette (+ = vers le haut)
"""

import math
from dataclasses import dataclass, field

from .filters import OneEuroFilter2D
from .hands import ControlTracker, HandClassifier, palm_center

VIEW_ORDER = ("3D", "Red", "Green", "Yellow")
SLICE_VIEWS = ("Red", "Green", "Yellow")
MOUSE = "Mouse"
ROLES = ("C", "S")

HOLD_LABELS_FR = {
    "pause": "Pause / réactivation",
    "mouse_toggle": "Mode souris",
}

# pose de la main qui commande -> action continue, selon le mode
ENGAGE_3D = {"fist": "zoom", "pinch": "rotate", "shaka": "pan", "v": "swipe"}
ENGAGE_SLICE = {"pinch": "scroll", "fist": "slice_zoom", "shaka": "wl", "v": "swipe"}
ENGAGE_MOUSE = {"pinch": "lbutton", "fist": "rbutton", "v": "wheel"}

ACTION_LABELS_FR = {
    "zoom": "zoom",
    "rotate": "rotation",
    "pan": "déplacement",
    "swipe": "changement de vue",
    "scroll": "défilement",
    "slice_zoom": "zoom",
    "wl": "contraste",
    "lbutton": "clic gauche",
    "rbutton": "clic droit",
    "wheel": "molette",
}


@dataclass
class EngineConfig:
    # Gestes à deux mains maintenus (secondes)
    hold_pause_s: float = 1.0
    hold_mouse_s: float = 1.0
    # Défilement : course de main (fraction de la hauteur d'image) pour une coupe, au ralenti
    scroll_step: float = 0.02
    scroll_accel_max: float = 4.0       # multiplicateur maximal quand le geste est rapide
    scroll_accel_speed: float = 0.25    # vitesse (hauteurs d'image / s) au-delà de laquelle on accélère
    invert_scroll: bool = False
    # Zoom (poing + haut/bas)
    zoom_gain: float = 3.0
    slice_zoom_gain: float = 3.0
    # Vue 3D
    rotate_gain_deg: float = 180.0      # degrés pour une traversée complète de l'image
    pan_gain: float = 1.0
    # Balayage en V
    swipe_distance: float = 0.15        # fraction de la largeur d'image
    swipe_window_s: float = 0.6
    # Souris
    click_max_s: float = 0.4
    drag_threshold: float = 0.03
    wheel_step: float = 0.03
    mouse_zone: tuple = (0.25, 0.15, 0.75, 0.80)   # gauche, haut, droite, bas (image en miroir)


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
        events = self.release()
        self.mode = "3D"
        self.prev_view = "3D"
        self.paused = False
        self.hold_start = {}
        self.hold_lock = False
        events.append(("mode", self.mode, self.paused))
        return events

    def release(self):
        """Termine proprement l'action en cours (désactivation, pause…)."""
        events = []
        action = getattr(self, "action", None)
        if action is not None:
            if action["kind"] == "lbutton" and action.get("dragging"):
                events.append(("mouse_button", "left", "up"))
            events.append(("action", None))
        self.action = None
        return events

    # ------------------------------------------------------------- principal
    def update(self, t, hands):
        """hands : {"C": HandObs (main qui commande), "S": HandObs (autre main)} ; absent = non vue."""
        events = []
        c = hands.get("C")
        s = hands.get("S")

        # 1. Gestes à deux mains (prioritaires)
        pair = self._pair(c, s)
        if pair and self.action is not None:
            events += self.release()   # le début d'un geste à deux mains annule l'action en cours
        fired, progress = self._update_holds(t, pair)
        events.append(("progress", progress[0], progress[1]))
        if fired:
            events += self._fire(fired)

        # 2. Action continue de la main qui commande
        if self.action is not None:
            a = self.action
            keeps = c is not None and (c.pose == a["pose"] or (a["kind"] == "swipe" and c.pose == "other"))
            if keeps:
                events += self._continue_action(t, c)
            else:
                events += self._end_action(t, c)

        if self.action is None and c is not None and not self.paused and not pair and not fired:
            events += self._try_engage(t, c)

        # 3. Pointeur du mode souris
        if (self.mode == MOUSE and not self.paused and self.action is None
                and c is not None and c.pose not in ("pinch", "fist", "v")):
            events.append(("pointer",) + self._to_zone(c.x, c.y))
        return events

    # ----------------------------------------------------- gestes à deux mains
    @staticmethod
    def _pair(c, s):
        if c is None or s is None:
            return None
        if c.pose == "fist" and s.pose == "fist":
            return "pause"
        if c.pose == "open" and s.pose == "open":
            return "mouse_toggle"
        return None

    def _update_holds(self, t, pair):
        if self.hold_lock:
            if pair is None:
                self.hold_lock = False
            self.hold_start = {}
            return None, (None, 0.0)
        if pair is None or (self.paused and pair != "pause"):
            self.hold_start = {}
            return None, (None, 0.0)
        duration = self.cfg.hold_pause_s if pair == "pause" else self.cfg.hold_mouse_s
        start = self.hold_start.setdefault(pair, t)
        for key in list(self.hold_start):
            if key != pair:
                del self.hold_start[key]
        frac = (t - start) / duration if duration > 0 else 1.0
        if frac >= 1.0:
            self.hold_start = {}
            self.hold_lock = True   # relâcher avant de pouvoir recommencer
            return pair, (pair, 1.0)
        return None, (pair, frac)

    def _fire(self, key):
        events = []
        if key == "pause":
            self.paused = not self.paused
            events += self.release()
        elif key == "mouse_toggle":
            if self.mode == MOUSE:
                self.mode = self.prev_view
            else:
                self.prev_view = self.mode
                self.mode = MOUSE
        events.append(("mode", self.mode, self.paused))
        return events

    def _change_view(self, step):
        if self.mode not in VIEW_ORDER:
            return []
        i = VIEW_ORDER.index(self.mode)
        self.mode = VIEW_ORDER[(i + step) % len(VIEW_ORDER)]
        return [("mode", self.mode, self.paused)]

    # ---------------------------------------------------- actions continues
    def _engage_table(self):
        if self.mode == "3D":
            return ENGAGE_3D
        if self.mode in SLICE_VIEWS:
            return ENGAGE_SLICE
        if self.mode == MOUSE:
            return ENGAGE_MOUSE
        return {}

    def _try_engage(self, t, c):
        kind = self._engage_table().get(c.pose)
        if not kind:
            return []
        self.action = {
            "kind": kind, "pose": c.pose, "t0": t, "lt": t,
            "x0": c.x, "y0": c.y, "lx": c.x, "ly": c.y,
            "acc": 0.0, "dragging": False, "moved": 0.0,
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
        a["moved"] = max(a["moved"], math.hypot(c.x - a["x0"], c.y - a["y0"]))
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
                        a["swiped"] = True   # un seul changement par V
                        events += self._change_view(1 if sx > 0 else -1)
                        break
        elif kind == "scroll":
            speed = abs(dy) / dt
            accel = min(cfg.scroll_accel_max, max(1.0, speed / max(cfg.scroll_accel_speed, 1e-6)))
            sign = 1.0 if cfg.invert_scroll else -1.0   # main qui monte (dy < 0) = coupes +
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
        elif kind == "lbutton":
            if not a["dragging"] and a["moved"] > cfg.drag_threshold:
                a["dragging"] = True
                # bouton enfoncé à la position figée au début de la pince
                events.append(("mouse_button", "left", "down"))
            if a["dragging"]:
                events.append(("pointer",) + self._to_zone(c.x, c.y))
        elif kind == "wheel":
            a["acc"] += -dy / max(cfg.wheel_step, 1e-6)
            n = int(a["acc"])
            if n:
                a["acc"] -= n
                events.append(("wheel", n))
        # rbutton : rien pendant le geste (pointeur figé), le clic est décidé au relâchement
        return events

    def _end_action(self, t, c):
        a = self.action
        self.action = None
        events = [("action", None)]
        released = c is not None   # geste relâché (et pas main perdue)
        brief = (t - a["t0"]) <= self.cfg.click_max_s
        still = a["moved"] <= self.cfg.drag_threshold
        if a["kind"] == "lbutton":
            if a["dragging"]:
                events.append(("mouse_button", "left", "up"))
            elif released and brief:
                events.append(("mouse_click", "left"))
        elif a["kind"] == "rbutton":
            if released and brief and still:
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
    """Chaîne complète : mains brutes (sortie MediaPipe) -> rôle (commande / autre)
    -> pose -> lissage -> moteur."""

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
        """raw_hands : liste de dict {"lm": 21x[x,y,z], "wlm": 21x[x,y,z] | None, "score": float}.

        Renvoie (événements, mains par rôle {"C"|"S": main brute}, infos {rôle: dict})."""
        assigned = self.control.assign(t, raw_hands)
        observations = {}
        info = {}
        for role in ROLES:
            hand = assigned.get(role)
            if hand is None:
                last = self._last_obs.get(role)
                if role == "C" and last is not None and t - last[0] <= self.cfg.missing_grace_s:
                    # Perte de suivi très brève : on garde la dernière observation
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
