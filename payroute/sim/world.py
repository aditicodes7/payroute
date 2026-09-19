"""The R0 simulator.

Design notes worth knowing before reading the code:

RNG STREAMS. Context generation, outcome draws, and the incident/drift
processes each get their own independent stream. If they shared one, two
policies that choose differently would consume different numbers of draws, the
streams would desynchronise, and the policies would end up running on different
traffic — a comparison that has quietly stopped being a comparison, with
nothing in the output to say so.

COMMON RANDOM NUMBERS. Each transaction carries one uniform draw `u`, fixed at
generation time. The outcome under gateway g is `u < p(g, context, t)`. So
every counterfactual is well defined: "what would gateway C have done for this
transaction?" is answerable, which is exactly the question real logged data can
never answer and exactly what R3 needs to have a ground truth. It also gives
paired policy comparisons with far lower variance than independent runs.
The assumption it embeds: a transaction that "would have failed anywhere" is a
coherent object. Defensible for bank-side failures, but it IS an assumption
about the correlation structure of counterfactual outcomes.

THE ORACLE. The simulator knows every true probability. Policies must not. The
`Transaction` handed to a policy carries context only; the oracle is a separate
object reachable from the Simulator, which the evaluation harness holds and a
policy never sees.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterator, Sequence

import numpy as np

from payroute.sim.config import SimulatorConfig
from payroute.sim.incidents import GatewayIncident, IssuerIncident, _Incident

_BLOCK = 8192  # transactions generated per vectorised batch


@dataclass(frozen=True)
class Transaction:
    """Everything a policy is allowed to see, and nothing else.

    `_u` and `_latency_z` are the pre-drawn randomness that fixes this
    transaction's outcome under every gateway. They are private because they
    are not context — but note that they carry no information about which
    gateway is better, so seeing them would not help a policy cheat. The
    information a leak would actually expose lives in the oracle.
    """

    txn_id: str
    ts: datetime
    method: str
    issuer: str
    amount_paise: int
    merchant_category: str
    _u: float = field(repr=False, compare=True)
    _latency_z: tuple[float, ...] = field(repr=False, compare=True)

    def as_dict(self) -> dict:
        return {
            "txn_id": self.txn_id,
            "ts": self.ts,
            "method": self.method,
            "issuer": self.issuer,
            "amount_paise": self.amount_paise,
            "merchant_category": self.merchant_category,
        }


@dataclass(frozen=True)
class Outcome:
    gateway: str
    success: bool
    latency_ms: float
    fee_paise: int
    net_revenue_paise: int


class Oracle:
    """Ground truth. Held by the evaluation harness, never by a policy."""

    def __init__(self, sim: "Simulator") -> None:
        self._sim = sim

    def true_success_prob(self, txn: Transaction, gateway: str) -> float:
        return self._sim._success_prob(txn, gateway)

    def counterfactual(self, txn: Transaction, gateway: str) -> Outcome:
        """What WOULD have happened on this gateway, whether or not we chose it."""
        return self._sim.resolve(txn, gateway)

    def true_policy_value(
        self,
        policy: Callable[[Transaction], str],
        transactions: Sequence[Transaction],
    ) -> float:
        """Exact expected net revenue per transaction under `policy`.

        Computed from the true probabilities rather than from sampled outcomes,
        so it carries no Monte-Carlo error. This is the number every OPE
        estimator in R3 gets scored against.
        """
        if not transactions:
            raise ValueError("cannot compute a policy value over zero transactions")
        total = 0.0
        for txn in transactions:
            gateway = policy(txn)
            p = self.true_success_prob(txn, gateway)
            value, fee = self._sim._money(txn, gateway)
            total += p * (value - fee)
            if self._sim.config.fee_on_failure:
                total += (1.0 - p) * (-fee)
        return total / len(transactions)


class Simulator:
    def __init__(self, config: SimulatorConfig, seed: int = 0) -> None:
        self.config = config
        self.seed = seed

        # Independent streams. Spawning from one SeedSequence keeps the whole
        # run reproducible from a single integer while guaranteeing the streams
        # do not overlap.
        ctx_seed, outcome_seed, drift_seed = np.random.SeedSequence(seed).spawn(3)
        self._ctx_rng = np.random.default_rng(ctx_seed)
        self._outcome_rng = np.random.default_rng(outcome_seed)
        self._drift_rng = np.random.default_rng(drift_seed)

        self._gateways = list(config.gateways)
        self._names = [g.name for g in self._gateways]
        self._index = {name: i for i, name in enumerate(self._names)}

        self._methods, self._method_cdf = _cdf(config.method_mix)
        self._issuers, self._issuer_cdf = _cdf(config.issuer_mix)
        self._categories, self._category_cdf = _cdf(config.merchant_category_mix)
        self._amount_median = np.array(
            [config.amount.median_paise_by_method[m] for m in self._methods], dtype=float
        )
        self._amount_sigma = np.array(
            [config.amount.log_sigma_by_method[m] for m in self._methods], dtype=float
        )

        self._gateway_incidents: dict[str, list[GatewayIncident]] = {}
        self._issuer_incidents: dict[str, list[IssuerIncident]] = {}

        # AR(1) drift, evolved on a fixed grid so that a lookup at any past
        # timestamp returns the same value it did the first time.
        self._drift_dt = config.drift.grid_seconds
        self._drift_phi = 0.5 ** (self._drift_dt / (config.drift.reversion_half_life_minutes * 60.0))
        self._drift_step_sigma = config.drift.sigma * math.sqrt(1.0 - self._drift_phi**2)
        self._drift_path: list[np.ndarray] = [np.zeros(len(self._gateways))]

        self._clock = config.start_time.timestamp()
        self._counter = 0
        self._buffer: list[Transaction] = []
        self._buffer_pos = 0

        self.oracle = Oracle(self)

    # -- incidents ---------------------------------------------------------

    def inject(self, incident: _Incident) -> None:
        if isinstance(incident, GatewayIncident):
            if incident.gateway not in self._index:
                raise KeyError(f"unknown gateway {incident.gateway!r}")
            self._gateway_incidents.setdefault(incident.gateway, []).append(incident)
        elif isinstance(incident, IssuerIncident):
            if incident.issuer not in self.config.issuer_mix:
                raise KeyError(f"unknown issuer {incident.issuer!r}")
            self._issuer_incidents.setdefault(incident.issuer, []).append(incident)
        else:
            raise TypeError(f"unsupported incident type {type(incident).__name__}")

    def inject_all(self, incidents) -> None:
        for incident in incidents:
            self.inject(incident)

    def fresh(self) -> "Simulator":
        """A new Simulator with the same config, seed and incidents.

        Used to replay a run exactly — for computing regret against a log after
        the fact, without having to keep every Transaction in memory.
        """
        replay = Simulator(self.config, seed=self.seed)
        for incidents in self._gateway_incidents.values():
            replay.inject_all(incidents)
        for incidents in self._issuer_incidents.values():
            replay.inject_all(incidents)
        return replay

    # -- generation --------------------------------------------------------

    def next_transaction(self) -> Transaction:
        if self._buffer_pos >= len(self._buffer):
            self._refill()
        txn = self._buffer[self._buffer_pos]
        self._buffer_pos += 1
        return txn

    def transactions(self, n: int) -> Iterator[Transaction]:
        for _ in range(n):
            yield self.next_transaction()

    def _refill(self) -> None:
        cfg = self.config
        n = _BLOCK
        k = len(self._gateways)

        # Context stream.
        m_idx = np.searchsorted(self._method_cdf, self._ctx_rng.random(n))
        i_idx = np.searchsorted(self._issuer_cdf, self._ctx_rng.random(n))
        c_idx = np.searchsorted(self._category_cdf, self._ctx_rng.random(n))
        z_amount = self._ctx_rng.standard_normal(n)
        gaps = self._ctx_rng.standard_exponential(n)

        # Amount is conditioned on method: UPI skews small, netbanking large.
        amounts = self._amount_median[m_idx] * np.exp(self._amount_sigma[m_idx] * z_amount)
        amounts = np.clip(np.rint(amounts), cfg.amount.min_paise, cfg.amount.max_paise)

        # Outcome stream — drawn now, at generation time, so that nothing about
        # it can depend on what a policy later chooses.
        u_success = self._outcome_rng.random(n)
        z_latency = self._outcome_rng.standard_normal((n, k))

        # Non-homogeneous Poisson arrivals. The rate is re-evaluated per
        # transaction; it varies on an hourly scale while gaps are seconds, so
        # holding it fixed between draws is a negligible approximation.
        base_per_second = cfg.arrival.base_rate_per_minute / 60.0
        amplitude = cfg.arrival.diurnal_amplitude
        peak = cfg.arrival.peak_hour_utc
        t = self._clock
        times = np.empty(n)
        gaps_list = gaps.tolist()
        for i in range(n):
            hour = (t % 86400.0) / 3600.0
            rate = base_per_second * (1.0 + amplitude * math.cos(2.0 * math.pi * (hour - peak) / 24.0))
            t += gaps_list[i] / rate
            times[i] = t
        self._clock = t
        self._ensure_drift_to(t)

        methods = [self._methods[j] for j in m_idx]
        issuers = [self._issuers[j] for j in i_idx]
        categories = [self._categories[j] for j in c_idx]
        amounts_list = amounts.astype(np.int64).tolist()
        times_list = times.tolist()
        u_list = u_success.tolist()
        z_list = z_latency.tolist()

        # The seed is part of the id, not decoration: without it two different
        # runs both start at txn-000000000 and any code that joins or pairs
        # logs by id silently matches unrelated transactions. It is also what
        # makes the id usable as a primary key for R6's decision table.
        start = self._counter
        self._buffer = [
            Transaction(
                txn_id=f"txn-{self.seed}-{start + i:09d}",
                ts=datetime.fromtimestamp(times_list[i], tz=timezone.utc),
                method=methods[i],
                issuer=issuers[i],
                amount_paise=int(amounts_list[i]),
                merchant_category=categories[i],
                _u=u_list[i],
                _latency_z=tuple(z_list[i]),
            )
            for i in range(n)
        ]
        self._counter += n
        self._buffer_pos = 0

    # -- drift -------------------------------------------------------------

    def _ensure_drift_to(self, epoch_seconds: float) -> None:
        if not self.config.drift.enabled:
            return
        needed = int((epoch_seconds - self.config.start_time.timestamp()) // self._drift_dt) + 2
        while len(self._drift_path) < needed:
            previous = self._drift_path[-1]
            shock = self._drift_rng.standard_normal(len(self._gateways)) * self._drift_step_sigma
            self._drift_path.append(previous * self._drift_phi + shock)

    def _drift_offset(self, gateway_index: int, ts: datetime) -> float:
        step = int((ts.timestamp() - self.config.start_time.timestamp()) // self._drift_dt)
        if step < 0:
            step = 0
        if step >= len(self._drift_path):
            self._ensure_drift_to(ts.timestamp())
        return float(self._drift_path[min(step, len(self._drift_path) - 1)][gateway_index])

    # -- outcomes ----------------------------------------------------------

    def _success_prob(self, txn: Transaction, gateway: str) -> float:
        g = self.config.gateway(gateway)
        p = min(1.0, max(0.0, g.base_success_by_method[txn.method] + g.issuer_adjust[txn.issuer]))

        if self.config.drift.enabled:
            p = min(1.0, max(0.0, p + self._drift_offset(self._index[gateway], txn.ts)))

        severity = 0.0
        for incident in self._gateway_incidents.get(gateway, ()):
            severity = max(severity, incident.severity_at(txn.ts))

        issuer_severity = 0.0
        for incident in self._issuer_incidents.get(txn.issuer, ()):
            issuer_severity = max(
                issuer_severity, incident.severity_at(txn.ts) * g.issuer_sensitivity[txn.issuer]
            )

        return p * (1.0 - severity) * (1.0 - issuer_severity)

    def _degradation(self, txn: Transaction, gateway: str) -> float:
        """Total active severity on this gateway for this transaction, in [0, 1).
        Used to inflate latency: a degrading endpoint gets slower before, and
        while, it starts failing."""
        g = self.config.gateway(gateway)
        worst = 0.0
        for incident in self._gateway_incidents.get(gateway, ()):
            worst = max(worst, incident.severity_at(txn.ts))
        for incident in self._issuer_incidents.get(txn.issuer, ()):
            worst = max(worst, incident.severity_at(txn.ts) * g.issuer_sensitivity[txn.issuer])
        return worst

    def _money(self, txn: Transaction, gateway: str) -> tuple[int, int]:
        """(our revenue on success, the gateway's fee) in paise."""
        g = self.config.gateway(gateway)
        value = (self.config.take_rate_bps * txn.amount_paise) // 10_000
        fee = g.fee.flat_paise + (g.fee.bps * txn.amount_paise) // 10_000
        return int(value), int(fee)

    def resolve(self, txn: Transaction, gateway: str) -> Outcome:
        """Resolve this transaction on this gateway.

        Deterministic given the transaction: calling it twice, or calling it for
        a gateway we did not choose, is always legal and always gives the same
        answer. That is what makes a logged decision replayable and what makes
        the counterfactual well defined.
        """
        g = self.config.gateway(gateway)
        idx = self._index[gateway]

        success = txn._u < self._success_prob(txn, gateway)

        latency = g.latency.median_ms * math.exp(g.latency.log_sigma * txn._latency_z[idx])
        latency *= 1.0 + self._degradation(txn, gateway)

        value, fee = self._money(txn, gateway)
        if success:
            charged = fee
            net = value - fee
        elif self.config.fee_on_failure:
            charged = fee
            net = -fee
        else:
            charged = 0
            net = 0

        return Outcome(
            gateway=gateway,
            success=bool(success),
            latency_ms=float(latency),
            fee_paise=int(charged),
            net_revenue_paise=int(net),
        )


def _cdf(mix: dict[str, float]) -> tuple[list[str], np.ndarray]:
    keys = list(mix)
    weights = np.array([mix[k] for k in keys], dtype=float)
    cdf = np.cumsum(weights)
    cdf[-1] = 1.0  # guard against float drift putting a uniform past the end
    return keys, cdf
