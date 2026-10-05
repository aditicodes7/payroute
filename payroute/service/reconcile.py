"""Detecting the two projections drifting apart.

Redis health and the Postgres decision log are both derived from the same
ordered outcome stream, so they can lag one another but should not disagree.
You cannot prevent two stores from diverging; you can make divergence
detectable and bounded, and this is the detector.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from payroute.service.state import StoreUnavailable


@dataclass(frozen=True)
class Drift:
    gateway: str
    stored: float
    recomputed: float
    delta: float


@dataclass(frozen=True)
class DriftReport:
    max_drift: float
    drifted: list[Drift]
    threshold: float

    def __str__(self) -> str:
        if not self.drifted:
            return f"no drift above {self.threshold:.3f} (max observed {self.max_drift:.4f})"
        detail = ", ".join(
            f"{d.gateway} stored {d.stored:.4f} vs recomputed {d.recomputed:.4f} "
            f"(delta {d.delta:+.4f})"
            for d in self.drifted
        )
        return f"{len(self.drifted)} gateway(s) drifted above {self.threshold:.3f}: {detail}"


def compare_health(store, recomputed: Mapping[str, float], threshold: float = 0.05) -> DriftReport:
    """Compare the live state store against health recomputed from the log."""
    snapshot = store.read(list(recomputed))

    drifts = []
    worst = 0.0
    for gateway, expected in recomputed.items():
        stored = snapshot.values[gateway]["health"]
        delta = stored - expected
        worst = max(worst, abs(delta))
        if abs(delta) > threshold:
            drifts.append(Drift(gateway, stored, expected, delta))

    return DriftReport(max_drift=worst, drifted=drifts, threshold=threshold)
