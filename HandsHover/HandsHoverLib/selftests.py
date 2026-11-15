"""Gesture engine tests on synthetic hands (no camera, no Slicer).

Run by Tests/run_tests.py (plain Python) and by the module's built-in test in Slicer.
"""

import itertools

from .gestures import GesturePipeline
from .hands import ControlTracker, HandClassifier
from .synthetic import make_hand

FPS = 30.0
CTRL = (0.60, 0.50)    # resting position of the controlling hand
OTHER = (0.25, 0.50)   # position of the other hand


class Sim:
    """Replays frames at 30 fps through the pipeline and collects events."""

    def __init__(self):
        self.p = GesturePipeline()
        self.t = 0.0
        self.events = list(self.p.reset())
        # The controlling hand appears first: it takes control
        self.run(0.3, ("open",) + CTRL)

    def run(self, duration, ctrl=None, other=None, ctrl_move=(0.0, 0.0), other_move=(0.0, 0.0)):
        """ctrl / other: (pose, x, y) or None; *_move: total displacement during `duration`."""
        n = max(1, int(round(duration * FPS)))
        out = []
        for i in range(n):
            f = (i + 1) / n
            hands = []
            if ctrl:
                hands.append(make_hand(ctrl[0], ctrl[1] + ctrl_move[0] * f, ctrl[2] + ctrl_move[1] * f, score=0.9))
            if other:
                # higher score on purpose: the controlling hand must stay the first one seen
                hands.append(make_hand(other[0], other[1] + other_move[0] * f, other[2] + other_move[1] * f, score=0.99))
            self.t += 1.0 / FPS
            ev, _assigned, _info = self.p.process(self.t, hands)
            out += ev
        self.events += out
        return out

    def rest(self, duration=0.3):
        return self.run(duration, ("open",) + CTRL)

    def go(self, pose, move=(0.0, 0.0), duration=0.6, start=CTRL, hold=0.0):
        """The controlling hand takes `pose` (still for `hold` s), moves by `move`,
        then opens and comes back to rest."""
        ev = self.run(hold, (pose,) + start) if hold > 0 else []
        ev += self.run(duration, (pose,) + start, ctrl_move=move)
        end = (start[0] + move[0], start[1] + move[1])
        ev += self.run(0.2, ("open",) + end)
        self.run(0.3, ("open",) + CTRL)
        return ev

    def both(self, pose, duration=1.2):
        ev = self.run(duration, (pose,) + CTRL, (pose,) + OTHER)
        ev += self.run(0.3, ("open",) + CTRL)   # the other hand leaves the field
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


def _commands(events):
    return [e for e in events if e[0] not in ("progress", "action")]


# ------------------------------------------------------------------ poses
def test_pose_classification():
    poses = ("open", "fist", "index", "v", "pinch")
    for roll, yaw, pitch in itertools.product((-35, 0, 35), (-45, 0, 45), (-30, 0, 30)):
        for pose in poses:
            h = make_hand(pose, roll=roll, yaw=yaw, pitch=pitch)
            got = HandClassifier().classify(h["lm"], h["wlm"])
            assert got == pose, f"pose {pose} at {(roll, yaw, pitch)} recognized as {got}"
    # Thumb up must point roughly upwards in the image
    for roll, yaw, pitch in [(0, 0, 0), (12, 0, 0), (-10, 0, 0), (0, 35, 0), (0, -35, 0), (0, 0, 20)]:
        h = make_hand("thumb_up", roll=roll, yaw=yaw, pitch=pitch)
        got = HandClassifier().classify(h["lm"], h["wlm"])
        assert got == "thumb_up", f"thumb up at {(roll, yaw, pitch)} recognized as {got}"


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
    assert r["C"] is not b and r["S"] is b, "the first hand seen keeps control"
    # The controlling hand disappears: the other takes over only after a delay
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
    assert s.mode == "Red", "swipe to the left = previous view"
    s.go("v", move=(-0.25, 0.0), duration=0.3)
    s.go("v", move=(-0.25, 0.0), duration=0.3)
    assert s.mode == "Yellow", "the cycle works in both directions"
    # A large back-and-forth swipe with the same V changes the view only once
    s.run(0.3, ("v",) + CTRL, ctrl_move=(0.3, 0.0))
    s.run(0.3, ("v", CTRL[0] + 0.3, CTRL[1]), ctrl_move=(-0.3, 0.0))
    s.rest()
    assert s.mode == "3D", s.mode
    # Slow movement with a V (repositioning): no change
    s.go("v", move=(0.2, 0.0), duration=2.0)
    assert s.mode == "3D", "a slow movement must not change the view"
    # Vertical movement with a V: no change
    s.go("v", move=(0.05, 0.25), duration=0.3)
    assert s.mode == "3D"


def test_3d_actions():
    s = Sim()
    ev = s.go("fist", move=(0.0, -0.2))
    assert _product(ev, "zoom") > 1.5, "fist moving up = zoom in"
    ev = s.go("fist", move=(0.0, 0.2))
    assert _product(ev, "zoom") < 0.67, "fist moving down = zoom out"
    ev = s.go("pinch", move=(0.2, 0.0))
    rot = _kinds(ev, "rotate")
    assert rot and sum(e[1] for e in rot) < -10, "pinch moving right = rotation"
    ev = s.go("index", move=(0.1, 0.1))
    assert _kinds(ev, "pan"), "index finger + move = pan"
    ev = s.run(1.2, ("thumb_up",) + CTRL) + s.rest()
    assert ("recenter",) in ev, "thumb up held 1 s = recenter"
    assert len(_kinds(ev, "recenter")) == 1
    assert not _kinds(s.events, "scroll") and not _kinds(s.events, "wl")


def test_slice_actions_and_acceleration():
    s = Sim()
    s.go("v", move=(0.25, 0.0), duration=0.3)
    assert s.mode == "Red"
    slow = sum(e[2] for e in _kinds(s.go("pinch", move=(0.0, -0.12), duration=2.0, hold=0.2), "scroll"))
    fast = sum(e[2] for e in _kinds(s.go("pinch", move=(0.0, -0.12), duration=0.2, hold=0.2), "scroll"))
    assert 3 <= slow <= 7, f"slow scrolling: {slow} slices"
    assert fast >= 2 * slow, f"a fast gesture accelerates ({fast} vs {slow})"
    assert all(e[1] == "Red" for e in _kinds(s.events, "scroll"))
    ev = s.go("fist", move=(0.0, -0.2))
    assert _product(ev, "slice_zoom") > 1.5, "fist moving up = zoom in on the slice"
    ev = s.go("index", move=(0.1, 0.05))
    assert _kinds(ev, "wl"), "index finger + move = window / level"
    ev = s.run(1.2, ("thumb_up",) + CTRL) + s.rest()
    assert ("slice_fit", "Red") in ev, "thumb up held 1 s = recenter the slice view"
    assert not _kinds(s.events, "rotate") and not _kinds(s.events, "zoom")


def test_other_hand_is_ignored():
    s = Sim()
    ev = s.run(0.8, ("open",) + CTRL, ("pinch",) + OTHER, other_move=(0.0, -0.2))
    ev += s.run(0.8, ("open",) + CTRL, ("fist",) + OTHER, other_move=(0.0, -0.2))
    ev += s.run(1.2, ("open",) + CTRL, ("thumb_up",) + OTHER)
    assert not _commands(ev), f"the other hand must not command anything ({_commands(ev)})"


def test_two_open_hands_do_nothing():
    s = Sim()
    ev = s.both("open", duration=2.0)
    assert not _commands(ev) and s.mode == "3D" and not s.paused


def test_pause_with_two_fists():
    s = Sim()
    s.both("fist")
    assert s.paused
    ev = s.go("pinch", move=(0.2, 0.0))
    ev += s.go("v", move=(0.25, 0.0), duration=0.3)
    ev += s.run(1.2, ("thumb_up",) + CTRL) + s.rest()
    assert not _kinds(ev, "rotate") and not _kinds(ev, "recenter") and s.mode == "3D", \
        "no command while paused"
    s.both("fist")
    assert not s.paused
    ev = s.go("pinch", move=(0.2, 0.0))
    assert _kinds(ev, "rotate")


def test_brief_tracking_loss_keeps_action():
    s = Sim()
    ev = s.run(0.3, ("pinch",) + CTRL, ctrl_move=(0.1, 0.0))
    assert ("action", "rotate") in ev
    ev = s.run(2.0 / FPS, None)                                  # 2 frames without a hand
    ev += s.run(0.2, ("pinch", CTRL[0] + 0.1, CTRL[1]))
    assert ("action", None) not in ev, "a 2-frame loss must not end the action"
    ev = s.run(0.5, None)
    assert ("action", None) in ev, "hand out of view = action ends"


ALL_TESTS = [
    test_pose_classification,
    test_pinch_hysteresis,
    test_control_hand,
    test_swipe_changes_view,
    test_3d_actions,
    test_slice_actions_and_acceleration,
    test_other_hand_is_ignored,
    test_two_open_hands_do_nothing,
    test_pause_with_two_fists,
    test_brief_tracking_loss_keeps_action,
]


def run_all(log=print):
    failures = []
    for test in ALL_TESTS:
        try:
            test()
            log(f"OK     {test.__name__}")
        except AssertionError as e:
            failures.append((test.__name__, str(e)))
            log(f"FAILED {test.__name__}: {e}")
    return failures
