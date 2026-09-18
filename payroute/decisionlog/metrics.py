"""Measurement over a decision log.

Rule 10 of this project is enforced by the shape of the return types: there is
no way to get a point estimate out of here without also getting an interval and
a sample size, because a point estimate with no uncertainty is not a result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from payroute.decisionlog.record import DecisionRecord

Z95 = 1.959963984540054


@dataclass(frozen=True)
class Summary:
    n: int
    success_rate: float
    success_ci: tuple[float, float]
    net_revenue_per_1k: float
    net_revenue_ci: tuple[float, float]


@dataclass(frozen=True)
class PairedDifference:
    """b minus a, computed transaction by transaction."""

    n: int
    success_rate_delta: float
    success_ci: tuple[float, float]
    net_revenue_per_1k_delta: float
    net_revenue_ci: tuple[float, float]


def _mean_and_ci(values: list[float], scale: float = 1.0) -> tuple[float, tuple[float, float]]:
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1) if n > 1 else 0.0
    half = Z95 * math.sqrt(variance / n)
    return mean * scale, ((mean - half) * scale, (mean + half) * scale)


def summarize(log: Iterable[DecisionRecord]) -> Summary:
    records = list(log)
    if not records:
        raise ValueError("cannot summarize an empty log")
    incomplete = [r.txn_id for r in records if not r.is_complete]
    if incomplete:
        raise ValueError(f"{len(incomplete)} record(s) have no outcome, first: {incomplete[0]}")

    successes = [float(r.outcome.success) for r in records]
    revenue = [float(r.outcome.net_revenue_paise) for r in records]

    success_rate, success_ci = _mean_and_ci(successes)
    revenue_per_1k, revenue_ci = _mean_and_ci(revenue, scale=1_000.0)
    return Summary(len(records), success_rate, success_ci, revenue_per_1k, revenue_ci)


def paired_difference(a: Iterable[DecisionRecord], b: Iterable[DecisionRecord]) -> PairedDifference:
    """Compare two policies transaction by transaction.

    Pairing is only meaningful if both logs saw the same transactions, so that
    is checked rather than assumed: pairing mismatched logs produces a number
    that looks fine and means nothing.

    The payoff is variance. Most of the spread in net revenue comes from the
    amount distribution, which is IDENTICAL in both logs and cancels in the
    difference — so the interval on the difference is far tighter than the
    intervals on the two means.
    """
    seed_a, seed_b = getattr(a, "seed", None), getattr(b, "seed", None)
    if seed_a is not None and seed_b is not None and seed_a != seed_b:
        raise ValueError(
            f"cannot pair logs from different runs: seeds {seed_a} and {seed_b}. "
            "Paired comparison only means anything if both policies saw the same transactions."
        )

    left, right = list(a), list(b)
    if len(left) != len(right):
        raise ValueError(f"cannot pair logs of different lengths: {len(left)} vs {len(right)}")
    mismatched = [i for i, (x, y) in enumerate(zip(left, right)) if x.txn_id != y.txn_id]
    if mismatched:
        i = mismatched[0]
        raise ValueError(
            f"logs did not see the same transactions: position {i} is "
            f"{left[i].txn_id} vs {right[i].txn_id} ({len(mismatched)} mismatches)"
        )

    success_deltas = [float(y.outcome.success) - float(x.outcome.success) for x, y in zip(left, right)]
    revenue_deltas = [
        float(y.outcome.net_revenue_paise - x.outcome.net_revenue_paise) for x, y in zip(left, right)
    ]

    success_delta, success_ci = _mean_and_ci(success_deltas)
    revenue_delta, revenue_ci = _mean_and_ci(revenue_deltas, scale=1_000.0)
    return PairedDifference(len(left), success_delta, success_ci, revenue_delta, revenue_ci)
