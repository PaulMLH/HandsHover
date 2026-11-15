"""Tests du moteur de gestes sur mains synthétiques (sans caméra, sans Slicer).

Lancés par Tests/run_tests.py (Python seul) et par le test intégré du module dans Slicer.
"""

from .gestures import GesturePipeline, MOUSE
from .hands import HandClassifier, SideTracker
from .synthetic import make_hand

FPS = 30.0


class Sim:
    """Rejoue des images à 30 i/s dans le pipeline et collecte les événements."""

    def __init__(self):
        self.p = GesturePipeline()
        self.t = 0.0
        self.events = list(self.p.reset())

    def run(self, duration, left=None, right=None, left_move=(0.0, 0.0), right_move=(0.0, 0.0)):
        """left / right : (pose, x, y) ou None. *_move : déplacement total pendant `duration`."""
        n = max(1, int(round(duration * FPS)))
        out = []
        for i in range(n):
            f = (i + 1) / n
            hands = []
            if left:
                hands.append(make_hand(left[0], left[1] + left_move[0] * f, left[2] + left_move[1] * f))
            if right:
                hands.append(make_hand(right[0], right[1] + right_move[0] * f, right[2] + right_move[1] * f))
            self.t += 1.0 / FPS
            ev, _assigned, _info = self.p.process(self.t, hands)
            out += ev
        self.events += out
        return out

    def rest(self, duration=0.3):
        return self.run(duration, ("open", 0.25, 0.5), ("open", 0.75, 0.5))

    @property
    def mode(self):
        return self.p.engine.mode

    @property
    def paused(self):
        return self.p.engine.paused


def _kinds(events, kind):
    return [e for e in events if e[0] == kind]


# ------------------------------------------------------------------ poses
def test_pose_classification():
    poses = ("open", "fist", "index", "v", "pinch")
    orientations = [(0, 0, 0), (25, 0, 0), (-25, 0, 0), (0, 35, 0), (0, -35, 0), (0, 0, 25), (15, 20, -15)]
    for pose in poses:
        for roll, yaw, pitch in orientations:
            c = HandClassifier()
            h = make_hand(pose, roll=roll, yaw=yaw, pitch=pitch)
            got = c.classify(h["lm"], h["wlm"])
            assert got == pose, f"pose {pose} orientée {(roll, yaw, pitch)} reconnue comme {got}"


def test_pinch_hysteresis():
    c = HandClassifier(pinch_on=0.30, pinch_off=0.45)
    h = make_hand("pinch")
    assert c.classify(h["lm"], h["wlm"]) == "pinch"
    assert c.pinching
    c.reset()
    assert not c.pinching


def test_side_tracker():
    st = SideTracker()
    a = make_hand("open", 0.3, 0.5)
    b = make_hand("open", 0.7, 0.5)
    r = st.assign(0.0, [b, a])
    assert r["L"] is a and r["R"] is b
    r = st.assign(0.0, [a, b], invert=True)
    assert r["R"] is a and r["L"] is b
    # Une seule main qui franchit le milieu garde son identité
    st = SideTracker()
    st.assign(0.0, [make_hand("open", 0.45, 0.5)])
    r = st.assign(0.033, [make_hand("open", 0.55, 0.5)])
    assert "L" in r, "la main gauche a changé de côté en franchissant le milieu"


# --------------------------------------------------------------- engine
def test_next_view_cycle_and_latch():
    s = Sim()
    s.rest()
    seen = []
    for _ in range(4):
        s.run(0.7, left=("index", 0.25, 0.5), right=("open", 0.75, 0.5))
        seen.append(s.mode)
        s.rest()
    assert seen == ["Red", "Green", "Yellow", "3D"], seen
    # Maintenir longtemps ne fait changer qu'une fois
    s.run(2.0, left=("index", 0.25, 0.5), right=("open", 0.75, 0.5))
    assert s.mode == "Red", s.mode


def test_pause_and_resume():
    s = Sim()
    s.rest()
    s.run(1.2, left=("index", 0.25, 0.5), right=("index", 0.75, 0.5))
    assert s.paused
    assert s.mode == "3D", "le geste de pause ne doit pas changer de vue"
    # Baisser la main droite d'abord ne doit pas déclencher « vue suivante »
    s.run(0.8, left=("index", 0.25, 0.5), right=("open", 0.75, 0.5))
    assert s.mode == "3D"
    s.rest()
    ev = s.run(0.6, left=("fist", 0.25, 0.5), left_move=(0.2, 0.0), right=("open", 0.75, 0.5))
    assert not _kinds(ev, "rotate"), "aucune action pendant la pause"
    s.rest()
    s.run(1.2, left=("index", 0.25, 0.5), right=("index", 0.75, 0.5))
    assert not s.paused


def test_3d_actions():
    s = Sim()
    s.rest()
    ev = s.run(0.6, left=("fist", 0.25, 0.5), left_move=(0.2, 0.0), right=("open", 0.75, 0.5))
    rot = _kinds(ev, "rotate")
    assert rot and sum(e[1] for e in rot) < -10, "poing gauche vers la droite = rotation (azimut négatif)"
    s.rest()
    ev = s.run(0.6, left=("open", 0.25, 0.5), right=("fist", 0.75, 0.5), right_move=(0.0, 0.1))
    assert _kinds(ev, "pan")
    s.rest()
    ev = s.run(0.6, left=("pinch", 0.25, 0.6), left_move=(0.0, -0.2), right=("open", 0.75, 0.5))
    zoom = 1.0
    for e in _kinds(ev, "zoom"):
        zoom *= e[1]
    assert zoom > 1.5, f"pince gauche vers le haut = zoom avant ({zoom:.2f})"
    s.rest()
    ev = s.run(1.2, left=("open", 0.25, 0.5), right=("index", 0.75, 0.5))
    assert _kinds(ev, "recenter")


def test_one_action_at_a_time():
    s = Sim()
    s.rest()
    s.run(0.2, left=("fist", 0.25, 0.5), right=("open", 0.75, 0.5))
    ev = s.run(0.6, left=("fist", 0.25, 0.5), right=("fist", 0.75, 0.5), right_move=(0.2, 0.0))
    assert not _kinds(ev, "pan"), "la main droite doit être ignorée tant que la gauche agit"


def test_slice_scroll_and_contrast():
    s = Sim()
    s.rest()
    s.run(0.7, left=("index", 0.25, 0.5), right=("open", 0.75, 0.5))
    assert s.mode == "Red"
    s.rest()
    ev = s.run(1.0, left=("open", 0.25, 0.5), right=("pinch", 0.75, 0.6), right_move=(0.0, -0.10))
    slow = sum(e[2] for e in _kinds(ev, "scroll"))
    s.rest()
    ev = s.run(1.0, left=("pinch", 0.25, 0.6), left_move=(0.0, -0.10), right=("open", 0.75, 0.5))
    fast = sum(e[2] for e in _kinds(ev, "scroll"))
    assert 2 <= slow <= 6, f"défilement lent : {slow} coupes"
    assert fast >= 3 * slow, f"défilement rapide ({fast}) nettement plus rapide que lent ({slow})"
    assert all(e[1] == "Red" for e in _kinds(s.events, "scroll"))
    s.rest()
    ev = s.run(0.6, left=("open", 0.25, 0.5), right=("fist", 0.75, 0.5), right_move=(0.1, 0.05))
    assert _kinds(ev, "wl")
    s.rest()
    ev = s.run(1.2, left=("open", 0.25, 0.5), right=("index", 0.75, 0.5))
    assert ("wl_reset", "Red") in ev


def test_mouse_mode():
    s = Sim()
    s.rest()
    s.run(0.7, left=("v", 0.25, 0.5), right=("open", 0.75, 0.5))
    assert s.mode == MOUSE
    s.rest()
    ev = s.run(0.5, left=("open", 0.25, 0.5), right=("open", 0.6, 0.4), right_move=(0.2, 0.2))
    assert len(_kinds(ev, "pointer")) > 5
    # Clic gauche : pince brève
    ev = s.run(0.2, left=("open", 0.25, 0.5), right=("pinch", 0.8, 0.6))
    ev += s.run(0.2, left=("open", 0.25, 0.5), right=("open", 0.8, 0.6))
    assert ("mouse_click", "left") in ev, "pince droite brève = clic gauche"
    # Glisser : pince + déplacement
    ev = s.run(0.6, left=("open", 0.25, 0.5), right=("pinch", 0.7, 0.5), right_move=(0.15, 0.0))
    ev += s.run(0.2, left=("open", 0.25, 0.5), right=("open", 0.85, 0.5))
    assert ("mouse_button", "left", "down") in ev and ("mouse_button", "left", "up") in ev
    assert ("mouse_click", "left") not in ev
    # Clic droit : pince gauche brève
    ev = s.run(0.2, left=("pinch", 0.25, 0.5), right=("open", 0.75, 0.5))
    ev += s.run(0.2, left=("open", 0.25, 0.5), right=("open", 0.75, 0.5))
    assert ("mouse_click", "right") in ev
    # Molette : pince gauche + vers le haut
    ev = s.run(0.6, left=("pinch", 0.25, 0.6), left_move=(0.0, -0.15), right=("open", 0.75, 0.5))
    ev += s.run(0.2, left=("open", 0.25, 0.5), right=("open", 0.75, 0.5))
    wheel = sum(e[1] for e in _kinds(ev, "wheel"))
    assert wheel >= 2, f"molette vers le haut ({wheel})"
    assert ("mouse_click", "right") not in ev
    # Sortie : V gauche -> retour à la vue précédente
    s.run(0.7, left=("v", 0.25, 0.5), right=("open", 0.75, 0.5))
    assert s.mode == "3D"


def test_drag_survives_brief_tracking_loss():
    s = Sim()
    s.rest()
    s.run(0.7, left=("v", 0.25, 0.5), right=("open", 0.75, 0.5))
    s.rest()
    ev = s.run(0.4, left=("open", 0.25, 0.5), right=("pinch", 0.7, 0.5), right_move=(0.1, 0.0))
    assert ("mouse_button", "left", "down") in ev
    ev = s.run(2.0 / FPS, left=("open", 0.25, 0.5), right=None)       # 2 images sans main droite
    ev += s.run(0.2, left=("open", 0.25, 0.5), right=("pinch", 0.8, 0.5))
    assert ("mouse_button", "left", "up") not in ev, "une perte de 2 images ne doit pas relâcher le bouton"
    ev = s.run(0.5, left=("open", 0.25, 0.5), right=None)
    assert ("mouse_button", "left", "up") in ev, "main sortie du champ = bouton relâché"


ALL_TESTS = [
    test_pose_classification,
    test_pinch_hysteresis,
    test_side_tracker,
    test_next_view_cycle_and_latch,
    test_pause_and_resume,
    test_3d_actions,
    test_one_action_at_a_time,
    test_slice_scroll_and_contrast,
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
