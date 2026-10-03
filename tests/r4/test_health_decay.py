"""R4: the time-decayed health store.

Two things carry this rung. The decay must be in WALL-CLOCK time, not in
transaction counts, because arrivals are diurnal — 1,000 transactions span 33
minutes at the 03:00 trough and 5 minutes at the 15:00 peak, so a count-based
window would react at a different speed depending on the hour and "time to
react" would stop meaning anything.

And the prior must not decay. After a quiet period an exponentially weighted
count shrinks toward zero, at which point a single failure can swing health from
0.95 to near zero and the policy abandons a working gateway on one unlucky
transaction. That is the predicted failure for this rung.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

T0 = datetime(2026, 3, 2, 12, 0, 0, tzinfo=timezone.utc)
GATEWAYS = ["atlas", "beacon", "cirrus"]


def _store(half_life_minutes=10.0, prior_successes=9.0, prior_trials=10.0, delay=timedelta(0)):
    from payroute.features.health import HealthConfig, TerminalHealth

    return TerminalHealth(
        GATEWAYS,
        HealthConfig(
            half_life_minutes=half_life_minutes,
            prior_successes=prior_successes,
            prior_trials=prior_trials,
            feedback_delay=delay,
        ),
    )


def test_cold_start_health_is_the_prior_mean():
    store = _store(prior_successes=9.0, prior_trials=10.0)
    health = store.health_at(T0)
    assert set(health) == set(GATEWAYS)
    for value in health.values():
        assert value == pytest.approx(0.9)


def test_a_single_observation_moves_health_only_a_little():
    """The guard against the overreaction failure. With a prior worth 10
    observations, one failure must move health by roughly 1/11th of the gap, not
    collapse it."""
    store = _store(prior_successes=9.0, prior_trials=10.0)
    store.observe("atlas", success=False, at=T0)
    assert store.health_at(T0)["atlas"] == pytest.approx(9.0 / 11.0)


def test_decay_matches_the_hand_computed_value():
    """One success, then one half-life of silence, then read.

    The evidence halves: S = 1 -> 0.5, N = 1 -> 0.5, so
    health = (0.5 + 9) / (0.5 + 10) = 9.5 / 10.5.
    """
    store = _store(half_life_minutes=10.0, prior_successes=9.0, prior_trials=10.0)
    store.observe("atlas", success=True, at=T0)
    value = store.health_at(T0 + timedelta(minutes=10))["atlas"]
    assert value == pytest.approx(9.5 / 10.5)


def test_decay_is_in_wall_clock_time_not_in_observations():
    """Ten observations compressed into one minute must leave far more evidence
    standing than the same ten spread over an hour."""
    fast, slow = _store(half_life_minutes=10.0), _store(half_life_minutes=10.0)
    for i in range(10):
        fast.observe("atlas", success=False, at=T0 + timedelta(seconds=6 * i))
        slow.observe("atlas", success=False, at=T0 + timedelta(minutes=6 * i))

    read_at = T0 + timedelta(minutes=60)
    assert fast.health_at(read_at)["atlas"] < slow.health_at(read_at)["atlas"]


def test_the_prior_does_not_decay():
    """After a long silence health must return to the prior mean, not to 0/0.

    If the prior decayed with everything else, the store would end up with no
    evidence at all and the next single observation would dominate completely.
    """
    store = _store(half_life_minutes=5.0, prior_successes=9.0, prior_trials=10.0)
    for i in range(50):
        store.observe("atlas", success=False, at=T0 + timedelta(seconds=i))

    assert store.health_at(T0 + timedelta(minutes=1))["atlas"] < 0.3
    assert store.health_at(T0 + timedelta(hours=6))["atlas"] == pytest.approx(0.9, abs=1e-3)


def test_health_tracks_a_sustained_drop():
    store = _store(half_life_minutes=5.0)
    at = T0
    for _ in range(200):
        store.observe("atlas", success=False, at=at)
        at += timedelta(seconds=3)
    assert store.health_at(at)["atlas"] < 0.2


def test_observing_one_gateway_leaves_the_others_untouched():
    store = _store()
    for i in range(50):
        store.observe("atlas", success=False, at=T0 + timedelta(seconds=i))
    health = store.health_at(T0 + timedelta(minutes=1))
    assert health["atlas"] < 0.5
    assert health["beacon"] == pytest.approx(0.9)
    assert health["cirrus"] == pytest.approx(0.9)


def test_a_shorter_half_life_reacts_faster():
    quick, slow = _store(half_life_minutes=2.0), _store(half_life_minutes=30.0)
    at = T0
    for _ in range(60):
        quick.observe("atlas", success=False, at=at)
        slow.observe("atlas", success=False, at=at)
        at += timedelta(seconds=10)
    assert quick.health_at(at)["atlas"] < slow.health_at(at)["atlas"]


def test_reading_health_twice_at_the_same_instant_gives_the_same_answer():
    """Reads must not mutate state, or the feature a policy sees would depend on
    how many times anything else happened to look at it."""
    store = _store()
    store.observe("atlas", success=False, at=T0)
    t = T0 + timedelta(minutes=3)
    assert store.health_at(t) == store.health_at(t)
