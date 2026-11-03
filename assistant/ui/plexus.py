"""Ken's look: linked particles ("plexus") that morph between shapes.

  idle    a slowly turning 3D sphere of linked points that breathes
  listen  a messy rush, then an ear, with sound arcs coming in (follows the mic level)
  think   a gyroscope: three rings orbiting on different axes, a bright head running round each
  speak   a double sound ribbon with rungs, following the voice
  done    a burst that settles into a check mark in a ring, with a ripple
  sleep   dim, sunk low, breathing slowly, little z's
  jump()  the "Ken!" moment: a quick hop and flash

Pure numpy physics (springs) + one batched QPainter pass; the same engine draws the pill, the
panel's avatars and the empty-state hero. Colours suit the light Frost theme.
"""

from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

IDLE, LISTEN, THINK, SPEAK, DONE, SLEEP = "idle", "listen", "think", "speak", "done", "sleep"

# ----------------------------------------------------------------------------
# States, colours and the gyroscope's rings
# ----------------------------------------------------------------------------

STATES = (IDLE, LISTEN, THINK, SPEAK, DONE, SLEEP)
COLORS = {IDLE: (15, 163, 127), LISTEN: (47, 127, 224), THINK: (122, 92, 240), SPEAK: (15, 163, 127),
          DONE: (16, 163, 90), SLEEP: (140, 155, 168)}
TAU = math.tau
RING_SPEED = (1.6, 1.95, 2.3)  # gyroscope: each ring orbits at its own pace…
RING_TILT = ((1.1, 0.0), (-1.1, 0.6), (0.0, 1.4))  # …on its own axis


# ----------------------------------------------------------------------------
# Shapes
#   Target point sets per state (sphere, ear, gyroscope, ribbon, check mark), sampled along paths.
# ----------------------------------------------------------------------------

def _sample(paths: list[list[tuple[float, float]]], n: int) -> np.ndarray:
    """n points evenly along polylines (by length)."""
    segs, total = [], 0.0
    for path in paths:
        for a, b in zip(path, path[1:]):
            length = math.dist(a, b)
            segs.append((a, b, length))
            total += length
    out = []
    for k in range(n):
        d = (k + 0.5) / n * total
        for a, b, length in segs:
            if d <= length:
                t = d / length
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
                break
            d -= length
    return np.array(out, dtype=np.float32)


EAR_PATHS = [
    [(-0.3, -0.55), (-0.05, -0.85), (0.32, -0.88), (0.62, -0.6), (0.7, -0.15), (0.52, 0.24), (0.36, 0.5),
     (0.3, 0.78), (0.1, 0.92), (-0.12, 0.82)],
    [(-0.1, -0.35), (0.18, -0.5), (0.4, -0.3), (0.37, 0.0), (0.16, 0.16), (0.05, 0.36)],
    [(-0.24, 0.02), (-0.13, 0.22)],
]
_RING = [(math.cos(i / 48 * TAU - math.pi / 2) * 0.86, math.sin(i / 48 * TAU - math.pi / 2) * 0.86) for i in range(49)]
DONE_PATHS = [[(-0.42, 0.02), (-0.1, 0.33), (0.46, -0.33)], _RING]


# ----------------------------------------------------------------------------
# The engine
#   Particles with spring physics (numpy) pulled toward the current state's shape, linked to their
#   2-3 nearest neighbours. Drawing is batched into a few calls, so a frame costs a few ms.
# ----------------------------------------------------------------------------

class Plexus:
    def __init__(self, n: int = 118, seed: int = 7):
        self.n = n
        rng = np.random.default_rng(seed)
        self.pos = rng.uniform(-0.8, 0.8, (n, 2)).astype(np.float32)
        self.vel = np.zeros((n, 2), np.float32)
        self.depth = np.ones(n, np.float32)
        self.phase = rng.uniform(0, TAU, n).astype(np.float32)
        # fibonacci sphere for idle
        i = np.arange(n)
        y = 1 - (i / max(1, n - 1)) * 2
        r = np.sqrt(np.clip(1 - y * y, 0, 1))
        th = math.pi * (3 - math.sqrt(5)) * i
        self.sphere = np.stack([np.cos(th) * r, y, np.sin(th) * r], 1).astype(np.float32) * 0.82
        self.ear = _sample(EAR_PATHS, n)
        self.done_shape = _sample(DONE_PATHS, n)
        # gyroscope: particle i rides ring i % 3, at slot i // 3
        self.ring = i % 3
        self.ring_n = math.ceil(n / 3)
        self.slot = (i // 3).astype(np.float32)
        self.head = np.zeros(n, bool)  # the bright leading particles of each ring
        self.state = IDLE
        self.since = time.monotonic()
        self.color = np.array(COLORS[IDLE], np.float32)
        self.jump_at = -10.0
        self.ripple_at = -10.0
        self.level = 0.0  # 0..1, the mic (listen) or voice (speak) loudness when known

    # ------------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------------

    def set_state(self, state: str) -> None:
        if state != self.state:
            self.state, self.since = state, time.monotonic()
            if state == DONE:
                self.ripple_at = self.since

    def jump(self) -> None:
        """The "Ken!" moment."""
        self.jump_at = time.monotonic()

    # ------------------------------------------------------------------------
    # Physics
    # ------------------------------------------------------------------------

    @staticmethod
    def _voice(t: float) -> float:
        return 0.5 + 0.5 * (0.55 * math.sin(t * 5.3) + 0.3 * math.sin(t * 8.9 + 1) + 0.15 * math.sin(t * 13.7 + 2))

    def _gyroscope(self, t: float) -> tuple[np.ndarray, np.ndarray]:
        speed = np.array(RING_SPEED, np.float32)[self.ring]
        a = self.slot / self.ring_n * TAU + t * speed
        x, y, z = np.cos(a) * 0.8, np.sin(a) * 0.8, np.zeros(self.n, np.float32)
        tilt = np.array(RING_TILT, np.float32)[self.ring]
        ax, ay = tilt[:, 0] + t * 0.2, tilt[:, 1] + t * 0.3
        y, z = y * np.cos(ax) - z * np.sin(ax), y * np.sin(ax) + z * np.cos(ax)
        x, z = x * np.cos(ay) + z * np.sin(ay), -x * np.sin(ay) + z * np.cos(ay)
        lead = (t * speed / TAU * self.ring_n) % self.ring_n
        behind = (lead - self.slot) % self.ring_n  # 0 = the head, then the ones just behind it
        self.head = behind < 2.5
        return np.stack([x, y], 1), 1 + 0.35 * z

    def _targets(self, t: float, el: float) -> tuple[np.ndarray, np.ndarray]:
        n, st = self.n, self.state
        if st in (IDLE, SLEEP):
            a = t * (0.35 if st == IDLE else 0.12)
            c, s = math.cos(a), math.sin(a)
            x = self.sphere[:, 0] * c - self.sphere[:, 2] * s
            z = self.sphere[:, 0] * s + self.sphere[:, 2] * c
            breathe = 1 + 0.03 * math.sin(t * 1.6)
            if st == SLEEP:
                return np.stack([x * 0.75, self.sphere[:, 1] * 0.45 + 0.35 + 0.03 * np.sin(t * 0.8 + self.phase)], 1), \
                    0.55 + 0.25 * z
            return np.stack([x * breathe, self.sphere[:, 1] * breathe], 1), 1 + 0.35 * z
        if st == LISTEN:
            if el < 0.7:
                k = 1.15 + 0.3 * np.sin(t * 6 + self.phase)
                return np.stack([np.sin(t * 3.1 + self.phase * 2) * 0.7 * k,
                                 np.cos(t * 2.7 + self.phase * 3) * 0.7 * k], 1), np.ones(n, np.float32)
            level = self.level if self.level > 0.02 else self._voice(t) * 0.6
            return self.ear * (1 + 0.09 * level) + np.array([0.08, 0], np.float32), np.ones(n, np.float32)
        if st == THINK:
            return self._gyroscope(t)
        if st == SPEAK:
            half = n // 2
            k = np.arange(n) % half
            x = -0.92 + 1.84 * k / max(1, half - 1)
            env = np.power(np.cos(x * math.pi / 2), 1.4)
            amp = 0.12 + 0.32 * (self.level if self.level > 0.02 else self._voice(t))
            second = np.arange(n) >= half
            y = np.sin(x * 5.5 - t * 6 + np.where(second, math.pi, 0)) * amp * env * np.where(second, 0.8, 1)
            return np.stack([x, y], 1), np.ones(n, np.float32)
        wobble = 1 + 0.02 * np.sin(t * 3 + self.phase)
        return self.done_shape * wobble[:, None], np.ones(n, np.float32)

    def step(self) -> None:
        now = time.monotonic()
        target, depth = self._targets(now, now - self.since)
        stiff = 0.02 if self.state == SLEEP else 0.07
        damp = 0.86 if self.state == DONE and now - self.since < 0.5 else 0.8
        if self.state == THINK and now - self.since > 0.6:  # once formed, ride the rings closely
            stiff, damp = 0.3, 0.6
        self.vel = (self.vel + (target - self.pos) * stiff) * damp
        self.pos += self.vel
        self.depth += (depth - self.depth) * 0.15
        self.color += (np.array(COLORS[self.state], np.float32) - self.color) * 0.06

    # ------------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------------

    def paint(self, p: QPainter, rect: QRectF, small: bool = False, alpha: float = 1.0, wide: bool = False) -> None:
        """Draw into rect (square-ish). small: fewer/thinner links for the pill and icons.
        wide: stretch sideways to fill a wide rect (the speaking ribbon in the pill)."""
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        now = time.monotonic()
        w = min(rect.width(), rect.height())
        jt = now - self.jump_at
        jump = math.sin(jt / 0.6 * math.pi) if jt < 0.6 else 0.0
        flash = (1 - jt / 0.6) if jt < 0.6 else 0.0
        scale = w * 0.42 * (1 + 0.14 * jump)
        cx, cy = rect.center().x(), rect.center().y() - w * 0.05 * jump
        sx = rect.width() * 0.46 * (1 + 0.14 * jump) if wide else scale
        pts = self.pos * np.array([sx, scale], np.float32) + np.array([cx, cy], np.float32)
        col = tuple(int(c) for c in self.color)
        dim = (0.55 if self.state == SLEEP else 1.0) * alpha
        think = self.state == THINK

        # links to the 2-3 nearest neighbours, bucketed by strength (one drawLines per bucket)
        lim = w * (0.22 if think else 0.10 if self.state == SPEAK else 0.14)
        diff = pts[:, None, :] - pts[None, :, :]
        dist = np.sqrt((diff ** 2).sum(-1))
        np.fill_diagonal(dist, np.inf)
        if think:  # each ring links only to itself, so the three orbits read as separate rings
            dist[self.ring[:, None] != self.ring[None, :]] = np.inf
        k = 2 if small else 3
        nearest = np.argpartition(dist, k, axis=1)[:, :k]
        a_idx = np.repeat(np.arange(self.n), k)
        b_idx = nearest.ravel()
        d = dist[a_idx, b_idx]
        keep = d < lim
        a_idx, b_idx, d = a_idx[keep], b_idx[keep], d[keep]
        lo, hi = np.minimum(a_idx, b_idx), np.maximum(a_idx, b_idx)
        _, first = np.unique(lo * self.n + hi, return_index=True)  # each pair once
        lo, hi, d = lo[first], hi[first], d[first]
        strength = (1 - d / lim) * np.minimum(self.depth[lo], self.depth[hi])
        bucket = np.clip((strength * 5).astype(int), 0, 4)
        for b in range(5):
            sel = bucket == b
            if sel.any():
                p.setPen(QPen(QColor(*col, int(255 * min(1, (b + 0.5) / 5 * 0.6 * dim))), 0.6 if small else 0.9))
                p.drawLines([QLineF(*pts[i], *pts[j]) for i, j in zip(lo[sel], hi[sel])])

        if self.state == SPEAK and not small:  # rungs between the two strands
            half = self.n // 2
            p.setPen(QPen(QColor(*col, int(50 * dim)), 0.8))
            p.drawLines([QLineF(pts[i, 0], pts[i, 1], pts[i + half, 0], pts[i + half, 1]) for i in range(0, half, 3)])
        if self.state == LISTEN and now - self.since > 0.7:  # sound coming in
            for k2 in range(3):
                ph = (now * 0.9 + k2 / 3) % 1
                p.setPen(QPen(QColor(*col, int(140 * (1 - ph) * dim)), 1.2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                r = scale * (0.8 + ph * 0.35)
                p.drawArc(QRectF(cx - scale * 0.15 - r, cy - r, 2 * r, 2 * r), int(140 * 16), int(80 * 16))
        rt = now - self.ripple_at
        if self.state == DONE and rt < 1.2:
            r = scale * (0.9 + rt * 0.6)
            p.setPen(QPen(QColor(*col, int(150 * (1 - rt / 1.2) * dim)), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, cy), r, r)

        # the points (with a soft halo when there's room), batched: one path per brightness level
        p.setPen(Qt.PenStyle.NoPen)
        base_r = 1.35 if small else 1.7 * max(0.6, w / 300)  # visible on white even in the pill
        radius = base_r * np.maximum(0.5, self.depth)
        bright = np.clip((0.5 + 0.45 * np.minimum(self.depth, 1.3)) * dim + flash, 0, 1)
        if think:  # the bright head running round each ring
            radius = np.where(self.head, radius * 1.8, radius)
            bright = np.where(self.head, 1.0, bright * 0.85)
        level = np.clip((bright * 4).astype(int), 0, 4)
        if not small or think:
            halo = QPainterPath()
            for i in (np.nonzero(self.head)[0] if think else range(self.n)):
                halo.addEllipse(QPointF(*pts[i]), radius[i] * 2.6, radius[i] * 2.6)
            p.fillPath(halo, QColor(*col, int(45 * dim)))
        for lv in np.unique(level):
            dots = QPainterPath()
            for i in np.nonzero(level == lv)[0]:
                dots.addEllipse(QPointF(*pts[i]), radius[i], radius[i])
            p.fillPath(dots, QColor(*col, int(255 * min(1, (lv + 1) / 5))))
        if self.state == SLEEP and not small:
            for k2 in range(3):
                ph = (now * 0.25 + k2 / 3) % 1
                zx, zy, s = cx + scale * (0.35 + ph * 0.35), cy - scale * (0.1 + ph * 0.8), scale * (0.06 + ph * 0.06)
                p.setPen(QPen(QColor(*col, int(180 * (1 - ph) * dim)), 1.4))
                p.drawPolyline([QPointF(zx, zy), QPointF(zx + s, zy), QPointF(zx, zy + s), QPointF(zx + s, zy + s)])
        p.restore()
