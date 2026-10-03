"""Off-policy estimators.

Every estimator here answers the same question — what would policy pi_e have
earned, given a log collected under pi_log — and every one of them returns an
OPEResult rather than a number. That is deliberate: the expected failure mode
for this rung is an enormous-variance estimate reported as a finding because
nobody checked whether it was trustworthy, so there is no code path that hands
back a point estimate without its interval, its effective sample size and a
verdict.

All five share one shape: an estimate is sum(numerator) / sum(denominator) over
the log, and the interval comes from resampling those pairs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np

from payroute.decisionlog import DecisionRecord

# Below this, the estimate is a report about a handful of transactions whatever
# the row count says.
ESS_UNTRUSTWORTHY = 100.0
ESS_SUSPECT = 1_000.0

# 400 measurably understates the tail on a heavily skewed log: on the example
# in docs/bugs.md the interval widens from 1.1453 to 1.1597 (1.3%) going from
# 400 to 5,000 resamples. 1,000 recovers most of that at a fraction of the cost,
# and the understatement runs in the flattering direction, which is the one to
# be careful about.
BOOTSTRAP_RESAMPLES = 1_000
Z95 = 1.959963984540054
_ZERO = 1e-12


class NoOverlapError(ValueError):
    """The target policy takes actions the logging policy never could.

    Not a variance problem that more data would fix: the log contains no
    information at all about those actions, so the target's value is not
    identified and any number returned would be invented.
    """


@dataclass(frozen=True)
class OPEResult:
    estimator: str
    estimate: float
    ci: tuple[float, float]
    normal_ci: tuple[float, float]
    ess: float
    n: int
    max_weight: float
    trust: str
    warnings: tuple[str, ...]

    def __str__(self) -> str:
        return (
            f"{self.estimator}: {self.estimate:.4f} "
            f"[{self.ci[0]:.4f}, {self.ci[1]:.4f}]  ESS={self.ess:,.0f}/{self.n:,}  {self.trust}"
        )


RewardModel = Callable[[DecisionRecord, str], float]


def _records(log: Iterable[DecisionRecord]) -> list[DecisionRecord]:
    records = list(log)
    if not records:
        raise ValueError("cannot evaluate an empty log")
    incomplete = [r.txn_id for r in records if not r.is_complete]
    if incomplete:
        raise ValueError(f"{len(incomplete)} record(s) have no outcome, first: {incomplete[0]}")
    return records


def _target_probs(target, records: Sequence[DecisionRecord]) -> list[Mapping[str, float]]:
    return [target.action_probs(r.context) for r in records]


def _check_overlap(records, target_probs) -> None:
    """The check that makes logging the full action-probability vector pay off.

    Knowing only the chosen action's propensity, we could not tell whether the
    logging policy COULD have taken the action the target wants — only that it
    did not.
    """
    violations = 0
    example = None
    for record, probs in zip(records, target_probs):
        for action, p_target in probs.items():
            if p_target > _ZERO and record.action_probs.get(action, 0.0) <= _ZERO:
                violations += 1
                if example is None:
                    example = (record.txn_id, action)
                break
    if violations:
        txn_id, action = example
        raise NoOverlapError(
            f"no overlap on {violations:,} of {len(records):,} records: the target policy takes "
            f"actions the logging policy assigned probability zero (e.g. {txn_id} -> {action!r}). "
            "The target's value is not identified from this log — more data would not help, "
            "because the log contains no information about those actions."
        )


def _weights(records, target_probs) -> np.ndarray:
    return np.array(
        [probs.get(r.action, 0.0) / r.propensity for r, probs in zip(records, target_probs)]
    )


def _effective_sample_size(weights: np.ndarray) -> float:
    total = float(weights.sum())
    squared = float((weights**2).sum())
    if squared <= 0.0:
        return 0.0
    return total * total / squared


_RANK = {"ok": 0, "suspect": 1, "untrustworthy": 2}


def _verdict(ess, n, max_weight, extra, escalations=()) -> tuple[str, tuple[str, ...]]:
    warnings = list(extra)
    trust = "ok"
    if ess < ESS_UNTRUSTWORTHY:
        trust = "untrustworthy"
        warnings.append(
            f"effective sample size is {ess:,.1f} out of {n:,} records — this estimate rests on "
            f"about {ess:,.0f} transactions' worth of evidence, not {n:,}"
        )
    elif ess < ESS_SUSPECT:
        trust = "suspect"
        warnings.append(
            f"effective sample size is {ess:,.1f} out of {n:,} records — treat the interval as a "
            "lower bound on the real uncertainty"
        )
    if max_weight > n / 10.0:
        trust = "untrustworthy" if trust != "ok" else "suspect"
        warnings.append(
            f"a single record carries an importance weight of {max_weight:,.1f}, more than a tenth "
            f"of the {n:,} records"
        )
    # Bias sources that ESS cannot see. Clipping is the important one: it makes
    # the weights uniform, so every variance-based diagnostic improves while
    # the estimate gets worse.
    for level, message in escalations:
        warnings.append(message)
        if _RANK[level] > _RANK[trust]:
            trust = level
    return trust, tuple(warnings)


def _bootstrap_ci(numerator: np.ndarray, denominator: np.ndarray, seed: int) -> tuple[float, float]:
    """Percentile interval.

    Used in preference to the normal approximation because the importance-weight
    distribution is heavy-tailed exactly when the estimate is fragile, and the
    normal interval is at its most misleading precisely there.
    """
    rng = np.random.default_rng(seed)
    n = len(numerator)
    estimates = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        if n >= 1_000:
            # Poisson bootstrap: asymptotically equivalent and avoids
            # materialising a resampled copy of a large log each time.
            multiplicity = rng.poisson(1.0, n)
        else:
            multiplicity = np.bincount(rng.integers(0, n, n), minlength=n)
        denom = float(multiplicity @ denominator)
        if abs(denom) <= _ZERO:
            continue
        estimates.append(float(multiplicity @ numerator) / denom)
    if not estimates:
        value = float(numerator.sum() / denominator.sum())
        return (value, value)
    return (float(np.percentile(estimates, 2.5)), float(np.percentile(estimates, 97.5)))


def _normal_ci(numerator, denominator, estimate) -> tuple[float, float]:
    """The naive interval, reported alongside the bootstrap one so it can be
    seen failing rather than merely warned about."""
    n = len(numerator)
    mean_denominator = float(denominator.mean())
    if abs(mean_denominator) <= _ZERO or n < 2:
        return (estimate, estimate)
    influence = (numerator - estimate * denominator) / mean_denominator
    se = float(influence.std(ddof=1)) / math.sqrt(n)
    return (estimate - Z95 * se, estimate + Z95 * se)


def _result(name, numerator, denominator, weights, records, extra_warnings, seed,
            escalations=()) -> OPEResult:
    """`weights` must always be the RAW importance weights.

    Effective sample size and the maximum weight describe the (log, target)
    pair — how much usable evidence about this target the log contains. They are
    not properties of an estimator's internal arithmetic. Computing them on
    modified weights, as an earlier version did for clipped_ips, makes the
    diagnostic improve exactly as the estimate degrades.
    """
    n = len(records)
    total_denominator = float(denominator.sum())
    if abs(total_denominator) <= _ZERO:
        raise ValueError(f"{name}: the importance weights sum to zero — nothing to estimate from")

    estimate = float(numerator.sum()) / total_denominator
    ess = _effective_sample_size(weights)
    max_weight = float(weights.max()) if len(weights) else 0.0
    trust, warnings = _verdict(ess, n, max_weight, extra_warnings, escalations)

    return OPEResult(
        estimator=name,
        estimate=estimate,
        ci=_bootstrap_ci(numerator, denominator, seed),
        normal_ci=_normal_ci(numerator, denominator, estimate),
        ess=ess,
        n=n,
        max_weight=max_weight,
        trust=trust,
        warnings=warnings,
    )


def _rewards(records) -> np.ndarray:
    return np.array([float(r.outcome.success) for r in records])


# ---------------------------------------------------------------------------
# the estimators
# ---------------------------------------------------------------------------


def ips(log, target, seed: int = 0) -> OPEResult:
    """Inverse propensity scoring: reweight the log, ignore any model.

    Unbiased when the propensities are right and overlap holds. Its variance is
    the problem — p sits in a denominator, so a decision logged with p = 0.0033
    carries a weight of 300. Note also that IPS is NOT bounded by the range of
    the rewards it averages: a success-rate estimate above 1.0 is possible, and
    is an unusually legible sign that the estimate should be discarded.
    """
    records = _records(log)
    probs = _target_probs(target, records)
    _check_overlap(records, probs)
    weights = _weights(records, probs)
    numerator = weights * _rewards(records)
    return _result("ips", numerator, np.ones(len(records)), weights, records, [], seed)


def snips(log, target, seed: int = 0) -> OPEResult:
    """Self-normalised IPS: divide by the weight mass you actually drew.

    In expectation the weights average to 1; in a finite sample they do not, and
    IPS scales the whole estimate by that error. SNIPS cancels it. Slightly
    biased, consistent, usually far less variable, and always inside the range
    of the observed rewards — which is its advantage and also the reason it
    hides the pathology that IPS advertises.
    """
    records = _records(log)
    probs = _target_probs(target, records)
    _check_overlap(records, probs)
    weights = _weights(records, probs)
    numerator = weights * _rewards(records)
    return _result("snips", numerator, weights, weights, records, [], seed)


def clipped_ips(log, target, clip: float = 20.0, seed: int = 0) -> OPEResult:
    """IPS with the weights capped. Buys variance reduction with bias.

    What practitioners actually deploy. The bias is not hypothetical: on the
    toy log, clipping at 1.5 moves the estimate from 1.00 to 0.75, and that
    movement is exactly what was paid.
    """
    records = _records(log)
    probs = _target_probs(target, records)
    _check_overlap(records, probs)
    raw = _weights(records, probs)
    weights = np.minimum(raw, clip)
    numerator = weights * _rewards(records)

    # The fraction of RECORDS clipped is a reassuring and useless number: at low
    # exploration, clipping 0.2% of records can remove a quarter of the
    # estimate. The fraction of WEIGHT MASS removed is the one that matters.
    total_raw = float(raw.sum())
    clipped_mass = (total_raw - float(weights.sum())) / total_raw if total_raw > _ZERO else 0.0
    clipped_count = int((raw > clip).sum())

    escalations = []
    if clipped_mass > 0.0:
        level = "untrustworthy" if clipped_mass > 0.20 else ("suspect" if clipped_mass > 0.05 else "ok")
        message = (
            f"clipping at {clip:g} removed {clipped_mass:.1%} of the total importance weight "
            f"(only {clipped_count:,} of {len(records):,} records, {clipped_count / len(records):.2%}) — "
            "the estimate is biased downward by roughly that much, and no interval here includes it"
        )
        if level == "ok":
            escalations.append(("ok", message))
        else:
            escalations.append((level, message))

    return _result("clipped_ips", numerator, np.ones(len(records)), raw, records, [], seed,
                   escalations=escalations)


def direct_method(log, target, reward_model: RewardModel, seed: int = 0) -> OPEResult:
    """Fit a reward model, ask it what the target would have earned.

    Low variance, and biased whenever the model is wrong — which is worst
    exactly where the log has least coverage, because that is where it is
    extrapolating. The bias does not shrink with more data from the same log.
    DM never reads a propensity, so it cannot know which of its own predictions
    are extrapolation: its interval stays narrow while its error grows.
    """
    records = _records(log)
    probs = _target_probs(target, records)
    values = np.array(
        [
            sum(p * reward_model(record, action) for action, p in prob.items() if p > _ZERO)
            for record, prob in zip(records, probs)
        ]
    )
    ones = np.ones(len(records))
    weights = _weights(records, probs)
    extra = [
        "direct_method ignores propensities: its interval reflects only the spread of its own "
        "predictions, so a narrow interval here says nothing about whether the estimate is close. "
        "The effective sample size shown describes the log, not this estimator's uncertainty."
    ]
    uncovered = sum(
        1
        for record, prob in zip(records, probs)
        if any(p > _ZERO and record.action_probs.get(a, 0.0) <= _ZERO for a, p in prob.items())
    )
    if uncovered:
        extra.append(
            f"{uncovered:,} of {len(records):,} records have no overlap with the target policy — "
            "for those, this estimate is pure extrapolation and nothing in the log can contradict it"
        )
    return _result("direct_method", values, ones, weights, records, extra, seed)


def doubly_robust(log, target, reward_model: RewardModel, seed: int = 0) -> OPEResult:
    """The model's estimate, corrected by importance-weighted residuals.

    Unbiased if EITHER the model or the propensities are right. The variance
    argument is the more useful one though: the weight multiplies (r - r_hat)
    rather than r, so if the model is any good a weight of 300 multiplies
    something near zero. The model carries the signal; the weights only correct
    what is left.
    """
    records = _records(log)
    probs = _target_probs(target, records)
    _check_overlap(records, probs)
    weights = _weights(records, probs)

    baseline = np.array(
        [
            sum(p * reward_model(record, action) for action, p in prob.items() if p > _ZERO)
            for record, prob in zip(records, probs)
        ]
    )
    predicted = np.array([reward_model(record, record.action) for record in records])
    numerator = baseline + weights * (_rewards(records) - predicted)
    return _result("doubly_robust", numerator, np.ones(len(records)), weights, records, [], seed)
