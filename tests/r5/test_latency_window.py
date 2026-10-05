"""R5: the per-gateway latency window.

The constraint is a p95, which is a property of a distribution rather than of
one decision. The feasible-set design turns it into something checkable per
decision: keep only gateways whose RECENT p95 is within budget. Then the fleet
p95 is within budget by construction, because a mixture of distributions each
with P(latency > B) <= 0.05 also has P(latency > B) <= 0.05.

Time-bounded, not count-bounded, for the same reason as R4: arrivals are
diurnal, and a count-based window would cover a different span at 03:00 than at
15:00.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

T0 = datetime(2026, 3, 2, 12, 0, 0, tzinfo=timezone.utc)
GATEWAYS = ["atlas", "beacon", "cirrus"]


def _window(window_minutes=10.0, prior_p95_ms=250.0):
    from payroute.features.latency import LatencyConfig, LatencyWindow

    return LatencyWindow(
        GATEWAYS, LatencyConfig(window_minutes=window_minutes, prior_p95_ms=prior_p95_ms)
    )


def test_p95_of_a_known_sample_set():
    window = _window()
    for i in range(100):
        window.observe("atlas", latency_ms=float(i), at=T0 + timedelta(seconds=i))
    # 100 samples 0..99: the 95th percentile sits at 95 by nearest-rank
    assert window.p95_at(T0 + timedelta(seconds=100))["atlas"] == pytest.approx(95.0, abs=1.0)


def test_old_samples_fall_out_of_the_window():
    window = _window(window_minutes=5.0)
    for i in range(50):
        window.observe("atlas", latency_ms=5_000.0, at=T0 + timedelta(seconds=i))
    for i in range(50):
        window.observe("atlas", latency_ms=100.0, at=T0 + timedelta(minutes=10, seconds=i))

    assert window.p95_at(T0 + timedelta(minutes=11))["atlas"] < 200.0


def test_an_unseen_gateway_is_given_the_prior_and_stays_usable():
    """Cold start must be OPTIMISTIC or the constraint becomes a trap: a gateway
    with no latency data would be excluded, and being excluded it would never
    gather any. Permanent exclusion is not a constraint, it is a deletion."""
    window = _window(prior_p95_ms=250.0)
    assert window.p95_at(T0)["beacon"] == pytest.approx(250.0)


def test_a_handful_of_samples_is_not_trusted():
    """The nearest-rank p95 of five samples is their maximum, which sits around
    the 83rd percentile in expectation — so a sparsely sampled gateway would be
    handed an optimistic p95 and admitted to the feasible set on the strength
    of it. Below the minimum, the prior is used instead."""
    window = _window(prior_p95_ms=250.0)
    for i in range(5):
        window.observe("atlas", latency_ms=9_000.0, at=T0 + timedelta(seconds=i))
    assert window.p95_at(T0 + timedelta(seconds=10))["atlas"] == pytest.approx(250.0)

    for i in range(5, 40):
        window.observe("atlas", latency_ms=9_000.0, at=T0 + timedelta(seconds=i))
    assert window.p95_at(T0 + timedelta(seconds=60))["atlas"] == pytest.approx(9_000.0)


def test_samples_are_invisible_before_they_are_observed():
    window = _window()
    for i in range(40):
        window.observe("atlas", latency_ms=9_000.0, at=T0 + timedelta(minutes=5, seconds=i))
    assert window.p95_at(T0 + timedelta(minutes=1))["atlas"] == pytest.approx(250.0)
    assert window.p95_at(T0 + timedelta(minutes=6))["atlas"] > 1_000.0


def test_reading_the_same_instant_twice_gives_the_same_answer():
    window = _window()
    for i in range(30):
        window.observe("atlas", latency_ms=float(100 + i), at=T0 + timedelta(seconds=i))
    t = T0 + timedelta(minutes=1)
    assert window.p95_at(t) == window.p95_at(t)


def test_the_window_is_bounded_in_memory():
    """A ring buffer, not an unbounded list: at R6 this lives in Redis and is
    read on every request."""
    window = _window(window_minutes=60.0)
    for i in range(50_000):
        window.observe("atlas", latency_ms=float(i % 500), at=T0 + timedelta(milliseconds=i))
    assert window.sample_count("atlas") <= window.config.max_samples
