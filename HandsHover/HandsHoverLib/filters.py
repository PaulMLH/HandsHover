"""One-Euro filter: smooths jitter at low speed without adding lag when the hand
moves fast (Casiez et al., CHI 2012).

Pure Python, no dependency.
"""

import math


def _alpha(dt, cutoff):
    tau = 1.0 / (2.0 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


class OneEuroFilter:
    def __init__(self, min_cutoff=1.5, beta=4.0, d_cutoff=1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.reset()

    def reset(self):
        self._t = None
        self._x = None
        self._dx = 0.0

    def __call__(self, t, x):
        if self._t is None:
            self._t, self._x, self._dx = t, x, 0.0
            return x
        dt = max(t - self._t, 1e-3)
        dx = (x - self._x) / dt
        a_d = _alpha(dt, self.d_cutoff)
        dx_hat = a_d * dx + (1.0 - a_d) * self._dx
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = _alpha(dt, cutoff)
        x_hat = a * x + (1.0 - a) * self._x
        self._t, self._x, self._dx = t, x_hat, dx_hat
        return x_hat


class OneEuroFilter2D:
    def __init__(self, min_cutoff=1.5, beta=4.0, d_cutoff=1.0):
        self.fx = OneEuroFilter(min_cutoff, beta, d_cutoff)
        self.fy = OneEuroFilter(min_cutoff, beta, d_cutoff)

    def configure(self, min_cutoff=None, beta=None):
        for f in (self.fx, self.fy):
            if min_cutoff is not None:
                f.min_cutoff = min_cutoff
            if beta is not None:
                f.beta = beta

    def reset(self):
        self.fx.reset()
        self.fy.reset()

    def __call__(self, t, x, y):
        return self.fx(t, x), self.fy(t, y)
