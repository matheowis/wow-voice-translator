"""/say volume from the distance to the speaker."""

from __future__ import annotations

from typing import Optional, Sequence

DEFAULT_CURVE = ((0, 1.0), (5, 1.0), (10, 0.75), (15, 0.5), (20, 0.25), (30, 0.0))


def gain_for_distance(distance: float, curve: Sequence[Sequence[float]] = DEFAULT_CURVE) -> float:
    """Linear interpolation over (yards, gain) points; outside the points the edge value holds."""
    pts = sorted((float(d), float(g)) for d, g in curve)
    if not pts:
        return 1.0
    if distance <= pts[0][0]:
        return pts[0][1]
    for (d0, g0), (d1, g1) in zip(pts, pts[1:]):
        if distance <= d1:
            if d1 == d0:
                return g1
            return g0 + (g1 - g0) * (distance - d0) / (d1 - d0)
    return pts[-1][1]


def say_gain(distance: Optional[float], curve: Sequence[Sequence[float]] = DEFAULT_CURVE,
             unknown_policy: str = "play_quiet", quiet_gain: float = 0.35) -> float:
    """Gain for a /say line; 0 means "do not synthesise at all"."""
    if distance is None:
        return {"play_normal": 1.0, "play_quiet": quiet_gain, "mute": 0.0}.get(unknown_policy, quiet_gain)
    return max(0.0, min(1.0, gain_for_distance(distance, curve)))
