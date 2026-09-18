"""R2: the featuriser, and the boundary that stops it leaking.

The expected failure mode for this rung is a feature that encodes something
only knowable after the transaction completes. The defence here is structural:
the featuriser's only input is the dict that `context_of()` produces, so
leaking requires adding a field to that one function — a visible, reviewable
line rather than an accident.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from payroute.policies.runner import context_of


class RecordingDict(dict):
    """A dict that remembers which keys were read, so a test can assert what the
    featuriser actually looked at rather than what it claims to look at."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.accessed: set[str] = set()

    def __getitem__(self, key):
        self.accessed.add(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.accessed.add(key)
        return super().get(key, default)


def _featuriser(cfg):
    from payroute.features import Featuriser

    return Featuriser.from_config(cfg)


def _context(**overrides):
    base = {
        "method": "upi",
        "issuer": "hdfc",
        "amount_paise": 35_000,
        "merchant_category": "food_delivery",
        "hour_of_day": 13,
    }
    base.update(overrides)
    return base


def test_feature_names_match_the_vector_width(clean_world):
    f = _featuriser(clean_world)
    x = f.transform(_context())
    assert x.shape == (f.dim,)
    assert len(f.names) == f.dim
    assert f.dim == 3 + 5 + 4 + 1 + 2 + 1, "3 methods, 5 issuers, 4 categories, amount, 2 time, bias"


def test_one_hots_are_set_for_the_right_categories(clean_world):
    f = _featuriser(clean_world)
    x = f.transform(_context(method="card", issuer="sbi", merchant_category="travel"))
    named = dict(zip(f.names, x))
    assert named["method=card"] == 1.0
    assert named["method=upi"] == 0.0
    assert named["issuer=sbi"] == 1.0
    assert named["issuer=hdfc"] == 0.0
    assert named["category=travel"] == 1.0
    assert named["bias"] == 1.0


def test_unknown_category_values_raise_rather_than_silently_encoding_zero(clean_world):
    """A silent all-zero one-hot is how a schema change becomes a slow, invisible
    degradation instead of a crash."""
    f = _featuriser(clean_world)
    with pytest.raises(KeyError):
        f.transform(_context(issuer="unknown_bank"))


def test_amount_is_log_scaled_and_standardised(clean_world):
    """Unscaled, log-amount would range over +/-8 while every one-hot is 0 or 1,
    so it would dominate both the ridge penalty and the confidence width
    x^T A^-1 x — and the exploration bonus would stop meaning anything."""
    f = _featuriser(clean_world)
    i = f.names.index("log_amount_z")

    small = f.transform(_context(amount_paise=10_000))[i]
    large = f.transform(_context(amount_paise=100_000))[i]

    assert large > small
    # a 10x amount is a fixed distance in log space, divided by the scale
    assert (large - small) == pytest.approx(math.log(10) / f.amount_log_std, rel=1e-9)
    # and typical amounts should land within a few standard deviations
    for amount in (5_000, 35_000, 180_000, 450_000):
        assert abs(f.transform(_context(amount_paise=amount))[i]) < 6.0


def test_hour_is_encoded_cyclically(clean_world):
    """23:00 and 00:00 are an hour apart, not 23 hours apart. A raw integer hour
    would tell the model the busiest and quietest parts of the night are the
    most distant points in the feature space."""
    f = _featuriser(clean_world)
    idx = [f.names.index("hour_sin"), f.names.index("hour_cos")]

    def point(hour):
        return f.transform(_context(hour_of_day=hour))[idx]

    wrap = np.linalg.norm(point(23) - point(0))
    across = np.linalg.norm(point(0) - point(6))
    assert wrap < across


def test_featurising_is_deterministic(clean_world):
    f = _featuriser(clean_world)
    a = f.transform(_context())
    b = f.transform(_context())
    assert np.array_equal(a, b)


def test_featuriser_reads_only_keys_that_context_of_provides(clean_world):
    """The leak guard, as a fact about what the code touched rather than a
    promise about what it intends to touch."""
    from payroute.sim import Simulator

    txn = next(iter(Simulator(clean_world, seed=1).transactions(1)))
    allowed = set(context_of(txn))

    recording = RecordingDict(_context())
    _featuriser(clean_world).transform(recording)

    assert recording.accessed, "the featuriser read nothing — is it ignoring its input?"
    leaked = recording.accessed - allowed
    assert not leaked, f"featuriser read keys that context_of does not provide: {leaked}"


def test_context_of_exposes_nothing_known_only_after_the_attempt(clean_world):
    """Guards the guard above: the leak test is only as good as context_of."""
    from payroute.sim import Simulator

    txn = next(iter(Simulator(clean_world, seed=1).transactions(1)))
    keys = set(context_of(txn))
    forbidden = ("success", "outcome", "latency", "fee", "revenue", "reward", "prob", "health", "severity")
    offending = [k for k in keys if any(s in k.lower() for s in forbidden)]
    assert not offending, f"context_of leaks post-decision information: {offending}"


def test_featuriser_refuses_a_raw_transaction(clean_world):
    """Handing it the Transaction would put the whole simulator one attribute
    access away. It takes a plain mapping, and only a plain mapping."""
    from payroute.sim import Simulator

    txn = next(iter(Simulator(clean_world, seed=1).transactions(1)))
    with pytest.raises(TypeError):
        _featuriser(clean_world).transform(txn)


def test_no_gateway_health_feature_exists_yet(clean_world):
    """R2 is static context only, deliberately. R4 adds time-decayed health
    features, and that addition is only a measurable delta if R2 did not
    quietly include one."""
    f = _featuriser(clean_world)
    assert not [n for n in f.names if "health" in n or "rate" in n or "decay" in n]
