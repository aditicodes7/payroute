"""R4's headline measurement: how long a policy takes to route away from a
degraded gateway, and how jumpy it is when nothing is wrong.

"When did it react?" needs pinning down before it can be measured, so:

  onset     minutes from incident start until the share of traffic to the
            degraded gateway falls below half its pre-incident share AND stays
            below for a sustained window. Without the persistence requirement,
            one noisy bin claims a reaction that did not happen — and with
            background drift switched on, noisy bins are guaranteed.

  recovery  minutes from incident end until the share returns to within a band
            of its pre-incident level.

A policy with no mechanism to react must report None rather than a number
produced by noise.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Sequence

from payroute.decisionlog.record import DecisionRecord


@dataclass(frozen=True)
class Reaction:
    reacted_after: timedelta | None
    recovered_after: timedelta | None
    share_before: float
    share_during_min: float
    bins_during: int


def _binned_shares(log, gateway, bin_minutes, min_count):
    width = bin_minutes * 60
    buckets: dict[int, list[int]] = {}
    for record in log:
        key = int(record.ts.timestamp() // width)
        entry = buckets.setdefault(key, [0, 0])
        entry[0] += int(record.action == gateway)
        entry[1] += 1
    return [
        (key * width, hits / total, total)
        for key, (hits, total) in sorted(buckets.items())
        if total >= min_count
    ]


def _sustained(series, index, predicate, persistence):
    return all(
        index + k < len(series) and predicate(series[index + k][1]) for k in range(persistence)
    )


def measure_reaction(
    log: Iterable[DecisionRecord],
    gateway: str,
    start: datetime,
    end: datetime,
    bin_minutes: float = 5.0,
    drop_threshold: float = 0.5,
    persistence_bins: int = 2,
    recovery_band: float = 0.25,
    min_count: int = 50,
) -> Reaction:
    series = _binned_shares(log, gateway, bin_minutes, min_count)
    if not series:
        raise ValueError("no bins with enough transactions to measure anything")

    start_s, end_s = start.timestamp(), end.timestamp()
    before = [s for t, s, _ in series if t < start_s]
    during = [(t, s) for t, s, _ in series if start_s <= t < end_s]
    if not before:
        raise ValueError("no transactions before the incident to establish a baseline")

    share_before = sum(before) / len(before)
    share_during_min = min((s for _, s in during), default=float("nan"))

    reacted_after = None
    threshold = drop_threshold * share_before
    for index, (t, _, _) in enumerate(series):
        if not (start_s <= t < end_s):
            continue
        if _sustained(series, index, lambda s: s < threshold, persistence_bins):
            reacted_after = timedelta(seconds=t - start_s)
            break

    recovered_after = None
    floor = share_before * (1.0 - recovery_band)
    for index, (t, _, _) in enumerate(series):
        if t < end_s:
            continue
        if _sustained(series, index, lambda s: s >= floor, persistence_bins):
            recovered_after = timedelta(seconds=t - end_s)
            break

    return Reaction(reacted_after, recovered_after, share_before, share_during_min, len(during))


def routing_jitter(log, bin_minutes: float = 5.0, min_count: int = 50) -> float:
    """Bin-to-bin movement in the routing mix, averaged over gateways.

    The standard deviation of FIRST DIFFERENCES rather than of levels. A
    bandit's share of traffic moves a long way over a run as it stops
    exploring, and that slow trend swamps the short-term chasing-the-noise
    behaviour a short half-life is supposed to exhibit. Differencing removes
    it. See docs/bugs.md.
    """
    records = list(log)
    gateways = sorted({r.action for r in records})
    spreads = []
    for gateway in gateways:
        shares = [s for _, s, _ in _binned_shares(records, gateway, bin_minutes, min_count)]
        if len(shares) > 2:
            spreads.append(statistics.pstdev([b - a for a, b in zip(shares, shares[1:])]))
    if not spreads:
        raise ValueError("not enough bins to measure jitter")
    return sum(spreads) / len(spreads)


def routing_instability(log, bin_minutes: float = 5.0, min_count: int = 50) -> float:
    """How much the routing mix moves around, averaged over gateways.

    Measured on CLEAN periods, this is the cost of a short half-life: every
    diversion away from a healthy gateway is a false alarm paid for in revenue.
    Reporting time-to-react without it makes "shorter is better" look true.
    """
    records = list(log)
    gateways = sorted({r.action for r in records})
    spreads = []
    for gateway in gateways:
        shares = [s for _, s, _ in _binned_shares(records, gateway, bin_minutes, min_count)]
        if len(shares) > 1:
            spreads.append(statistics.pstdev(shares))
    if not spreads:
        raise ValueError("not enough bins to measure instability")
    return sum(spreads) / len(spreads)
