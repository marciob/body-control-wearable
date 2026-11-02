"""Rolling heart-rate-variability metrics from R-R intervals.

The window holds the R-R intervals of the last `window_s` seconds.
An interval is rejected when it is outside [min_rr, max_rr] ms,
or when it differs from the previous raw interval by more than `max_jump`.
"""

from __future__ import annotations

import math
from collections import deque


class RollingHRV:
    def __init__(
        self,
        window_s: float = 60.0,
        min_rr: int = 300,
        max_rr: int = 2000,
        max_jump: float = 0.20,
        min_count: int = 10,
    ) -> None:
        self.window_s = window_s
        self.min_rr = min_rr
        self.max_rr = max_rr
        self.max_jump = max_jump
        self.min_count = min_count
        self._win: deque[tuple[float, int]] = deque()
        self._prev_raw: int | None = None
        self.accepted = 0
        self.rejected = 0

    def add(self, ts: float, rr_ms: int) -> bool:
        """Add one interval. Return True if it was accepted into the window."""
        in_range = self.min_rr <= rr_ms <= self.max_rr
        prev = self._prev_raw
        if in_range:
            # Compare only against the last in-range interval, so one
            # out-of-range value cannot block the next good one.
            self._prev_raw = rr_ms
        ok = in_range
        if ok and prev is not None:
            ok = abs(rr_ms - prev) / prev <= self.max_jump
        self._trim(ts)
        if not ok:
            self.rejected += 1
            return False
        self._win.append((ts, rr_ms))
        self.accepted += 1
        return True

    def _trim(self, now: float) -> None:
        cutoff = now - self.window_s
        while self._win and self._win[0][0] < cutoff:
            self._win.popleft()

    @property
    def count(self) -> int:
        return len(self._win)

    def _values(self) -> list[int]:
        return [rr for _, rr in self._win]

    def rmssd(self) -> float | None:
        """Root mean square of successive differences, in ms."""
        v = self._values()
        if len(v) < self.min_count:
            return None
        diffs = [(b - a) ** 2 for a, b in zip(v, v[1:])]
        return math.sqrt(sum(diffs) / len(diffs))

    def sdnn(self) -> float | None:
        """Standard deviation of the intervals, in ms."""
        v = self._values()
        if len(v) < self.min_count:
            return None
        mean = sum(v) / len(v)
        return math.sqrt(sum((x - mean) ** 2 for x in v) / (len(v) - 1))

    def mean_hr(self) -> float | None:
        """Mean heart rate over the window, in bpm, from the intervals."""
        v = self._values()
        if not v:
            return None
        return 60000.0 * len(v) / sum(v)
