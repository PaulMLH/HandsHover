"""Tests du moteur de gestes v0.2 (tout à une main) sur mains synthétiques,
sans caméra ni Slicer.

Lancés par Tests/run_tests.py (Python seul) et par le test intégré du module dans Slicer.
"""

import itertools

from .gestures import MOUSE, GesturePipeline
from .hands import ControlTracker, HandClassifier
from .synthetic import make_hand

FPS = 30.0
CTRL = (0.60, 0.50)    # position de repos de la main qui commande
OTHER = (0.25, 0.50)   # position de l'autre main


class Sim:
    """Rejoue des images à 30 i/s dans le pipeline et collecte les événements."""

    def __init__(self):
        self.p = GesturePipeline()
        self.t = 0.0
        self.events = list(self.p.reset())
        # La main qui commande apparaît en premier : elle prend le contrôle
        self.run(0.3, ("open",) + CTRL)

    def run(self, duration, ctrl=None, other=None, ctrl_move=(0.0, 0.0), other_move=(0.0, 0.0)):
        """ctrl / other : (pose, x, y) ou None ; *_move : déplacement total pendant `duration`."""
        n = max(1, int(round(duration * FPS)))
        out = []
        for i in range(n):
            f = (i + 1) / n
            hands = []
            if ctrl:
                hands.append(make_hand(ctrl[0], ctrl[1] + ctrl_move[0] * f, ctrl[2] + ctrl_move[1] * f, score=0.9))
            if other:
                # score plus élevé exprès : la main qui commande doit rester la première vue
                hands.append(make_hand(other[0], other[1] + other_move[0] * f, other[2] + other_move[1] * f, score=0.99))
            self.t += 1.0 / FPS
            ev, _assigned, _info = self.p.process(self.t, hands)
            out += ev
        self.events += out
        return out

    def rest(self, duration=0.3):
        return self.run(duration, ("open",) + CTRL)

    def go(self, pose, move=(0.0, 0.0), duration=0.6, start=CTRL, hold=0.0):
        """La main qui commande prend `pose` (immobile `hold` s), se déplace de `move`,
        puis revient au repos."""
        ev = self.run(hold, (pose,) + start) if hold > 0 else []
        ev += self.run(duration, (pose,) + start, ctrl_move=move)
        end = (start[0] + move[0], start[1] + move[1])
        ev += self.run(0.2, ("open",) + end)
        self.run(0.3, ("open",) + CTRL)   # retour au centre, main ouverte
        return ev

    def both(self, pose, duration=1.2):
        ev = self.run(duration, (pose,) + CTRL, (pose,) + OTHER)
        ev += self.run(0.3, ("open",) + CTRL)   # l'autre main sort du champ
        return ev

    @property
    def mode(self):
        return self.p.engine.mode

    @property
    def paused(self):
        return self.p.engine.paused


def _kinds(events, kind):
    return [e for e in events if e[0] == kind]


def _product(events, kind):
    value = 1.0
    for e in _kinds(events, kind):
        value *= e[-1]
    return value


# ------------------------------------------------------------------ poses
def test_pose_classification():
    poses = ("open", "fist", "index", "v", "pinch", "shaka")
    orientations = itertools.product((-35, 0, 35), (-45, 0, 45), (-30, 0, 30))
    for roll, yaw, pitch in orientations:
        for pose in poses:
            h = make_hand(pose, roll=roll, yaw=yaw, pitch=pitch)
            got = HandClassifier().classify(h["lm"], h["wlm"])
            assert got == pose, f"pose {pose} orientée {(roll, yaw, pitch)} reconnue comme {got}"


def test_pinch_hysteresis():
    c = HandClassifier(pinch_on=0.30, pinch_off=0.45)
    h = make_hand("pinch")
    assert c.classify(h["lm"], h["wlm"]) == "pinch" and c.pinching
    c.reset()
    assert not c.pinching


def test_control_hand():
    ct = ControlTracker()
    a = make_hand("open", 0.6, 0.5, score=0.8)
    r = ct.assign(0.0, [a])
    assert r["C"] is a
    b = make_hand("open", 0.25, 0.5, score=0.99)
    r = ct.assign(0.033, [b, make_hand("open", 0.61, 0.5, score=0.8)])
    assert r["C"] is not b and r["S"] is b, "la première main vue garde le contrôle"
    # La main qui commande disparaît : l'autre ne prend le relais qu'après un délai
    r = ct.assign(0.1, [b])
    assert "C" not in r
    r = ct.assign(0.6, [b])
    assert r["C"] is b


# --------------------------------------------------------------- engine
def test_swipe_changes_view():
    s = Sim()
    s.go("v", move=(0.25, 0.0), duration=0.3)
    assert s.mode == "Red", s.mode
    s.go("v", move=(0.25, 0.0), duration=0.3)
    assert s.mode == "Green", s.mode
    s.go("v", move=(-0.25, 0.0), duration=0.3)
    assert s.mode == "Red", "balayage vers la gauche = vue précédente"
    s.go("v", move=(-0.25, 0.0), duration=0.3)
    s.go("v", move=(-0.25, 0.0), duration=0.3)
    assert s.mode == "Yellow", "le cycle tourne dans les deux sens"
    # Un grand balayage aller-retour avec le même V ne change qu'une fois
    s.run(0.3, ("v",) + CTRL, ctrl_move=(0.3, 0.0))
    s.run(0.3, ("v", CTRL[0] + 0.3, CTRL[1]), ctrl_move=(-0.3, 0.0))
    s.rest()
    assert s.mode == "3D", s.mode
    # Déplacement lent en V (repositionnement) : pas de changement
    s.go("v", move=(0.2, 0.0), duration=2.0)
    assert s.mode == "3D", "un déplacement lent ne doit pas changer de vue"
    # Mouvement vertical en V : pas de changement
    s.go("v", move=(0.05, 0.25), duration=0.3)
    assert s.mode == "3D"


def test_3d_actions():
    s = Sim()
    ev = s.go("fist", move=(0.0, -0.2))
    assert _product(ev, "zoom") > 1.5, "poing vers le haut = zoom avant"
    ev = s.go("fist", move=(0.0, 0.2))
    assert _product(ev, "zoom") < 0.67, "poing vers le bas = zoom arrière"
    ev = s.go("pinch", move=(0.2, 0.0))
    rot = _kinds(ev, "rotate")
    assert rot and sum(e[1] for e in rot) < -10, "pince vers la droite = rotation"
    ev = s.go("shaka", move=(0.1, 0.1))
    assert _kinds(ev, "pan"), "pouce + auriculaire = déplacement du modèle"
    assert not _kinds(s.events, "scroll") and not _kinds(s.events, "wl")


def test_slice_actions_and_acceleration():
    s = Sim()
    s.go("v", move=(0.25, 0.0), duration=0.3)
    assert s.mode == "Red"
    slow = sum(e[2] for e in _kinds(s.go("pinch", move=(0.0, -0.12), duration=2.0, hold=0.2), "scroll"))
    fast = sum(e[2] for e in _kinds(s.go("pinch", move=(0.0, -0.12), duration=0.2, hold=0.2), "scroll"))
    assert 3 <= slow <= 7, f"défilement lent : {slow} coupes"
    assert fast >= 2 * slow, f"le geste rapide accélère ({fast} contre {slow})"
    assert all(e[1] == "Red" for e in _kinds(s.events, "scroll"))
    ev = s.go("fist", move=(0.0, -0.2))
    assert _product(ev, "slice_zoom") > 1.5, "poing vers le haut = zoom avant de la coupe"
    ev = s.go("shaka", move=(0.1, 0.05))
    assert _kinds(ev, "wl"), "pouce + auriculaire = contraste / luminosité"
    assert not _kinds(s.events, "rotate") and not _kinds(s.events, "zoom")


def test_other_hand_is_ignored():
    s = Sim()
    ev = s.run(0.8, ("open",) + CTRL, ("pinch",) + OTHER, other_move=(0.0, -0.2))
    ev += s.run(0.8, ("open",) + CTRL, ("fist",) + OTHER, other_move=(0.0, -0.2))
    for kind in ("rotate", "zoom", "pan", "scroll"):
        assert not _kinds(ev, kind), f"l'autre main ne doit rien commander ({kind})"


def test_pause_with_two_fists():
    s = Sim()
    s.both("fist")
    assert s.paused
    ev = s.go("pinch", move=(0.2, 0.0))
    ev += s.go("v", move=(0.25, 0.0), duration=0.3)
    assert not _kinds(ev, "rotate") and s.mode == "3D", "aucune commande pendant la pause"
    s.both("fist")
    assert not s.paused
    ev = s.go("pinch", move=(0.2, 0.0))
    assert _kinds(ev, "rotate")


def test_mouse_mode():
    s = Sim()
    s.go("v", move=(0.25, 0.0), duration=0.3)   # -> Red
    s.both("open")
    assert s.mode == MOUSE
    ev = s.run(0.5, ("open",) + CTRL, ctrl_move=(0.1, -0.1))
    assert len(_kinds(ev, "pointer")) > 5
    s.rest()
    # Clic gauche : pince brève
    ev = s.run(0.2, ("pinch",) + CTRL) + s.run(0.2, ("open",) + CTRL)
    assert ("mouse_click", "left") in ev
    # Glisser : pince + déplacement
    ev = s.go("pinch", move=(0.15, 0.0))
    assert ("mouse_button", "left", "down") in ev and ("mouse_button", "left", "up") in ev
    assert ("mouse_click", "left") not in ev
    # Clic droit : poing bref
    ev = s.run(0.2, ("fist",) + CTRL) + s.run(0.2, ("open",) + CTRL)
    assert ("mouse_click", "right") in ev
    # Poing long : pas de clic
    ev = s.run(1.0, ("fist",) + CTRL) + s.run(0.2, ("open",) + CTRL)
    assert ("mouse_click", "right") not in ev
    # Molette : V + vers le haut, sans changer de vue
    ev = s.go("v", move=(0.0, -0.15))
    assert sum(e[1] for e in _kinds(ev, "wheel")) >= 2
    ev = s.go("v", move=(0.25, 0.0), duration=0.3)
    assert s.mode == MOUSE, "pas de changement de vue en mode souris"
    # Sortie : deux mains ouvertes -> retour à la vue précédente
    s.both("open")
    assert s.mode == "Red", s.mode


def test_drag_survives_brief_tracking_loss():
    s = Sim()
    s.both("open")
    assert s.mode == MOUSE
    ev = s.run(0.4, ("pinch",) + CTRL, ctrl_move=(0.1, 0.0))
    assert ("mouse_button", "left", "down") in ev
    ev = s.run(2.0 / FPS, None)                                  # 2 images sans main
    ev += s.run(0.2, ("pinch", CTRL[0] + 0.1, CTRL[1]))
    assert ("mouse_button", "left", "up") not in ev, "une perte de 2 images ne doit pas relâcher le bouton"
    ev = s.run(0.5, None)
    assert ("mouse_button", "left", "up") in ev, "main sortie du champ = bouton relâché"


ALL_TESTS = [
    test_pose_classification,
    test_pinch_hysteresis,
    test_control_hand,
    test_swipe_changes_view,
    test_3d_actions,
    test_slice_actions_and_acceleration,
    test_other_hand_is_ignored,
    test_pause_with_two_fists,
    test_mouse_mode,
    test_drag_survives_brief_tracking_loss,
]


def run_all(log=print):
    failures = []
    for test in ALL_TESTS:
        try:
            test()
            log(f"OK     {test.__name__}")
        except AssertionError as e:
            failures.append((test.__name__, str(e)))
            log(f"ÉCHEC  {test.__name__} : {e}")
    return failures
