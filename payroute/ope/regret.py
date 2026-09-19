"""Regret, computed exactly rather than estimated.

Because the world is synthetic we know the true success probability of every
arm for every context, so instantaneous regret

    p*(x) - p_chosen(x)

is an exact quantity, not an estimate with its own error bars. That is a luxury
this project has and a real deployment never does, and it is the same luxury
that makes R3 possible at all.
"""

from __future__ import annotations

from typing import Sequence

from payroute.decisionlog import DecisionRecord
from payroute.sim import Simulator


def instantaneous_regret(sim: Simulator, log: Sequence[DecisionRecord]) -> list[float]:
    """Per-decision regret for a log produced by running `sim`.

    The simulator is replayed from its seed rather than rewound, so the
    transactions are regenerated exactly. Every record's txn_id is checked
    against the replayed transaction: since ids carry the seed, a mismatch
    means the log and the simulator are from different runs, which would
    silently produce a plausible and meaningless number.
    """
    records = list(log)
    replay = sim.fresh()
    oracle = replay.oracle
    names = replay.config.gateway_names

    regret: list[float] = []
    for txn, record in zip(replay.transactions(len(records)), records):
        if txn.txn_id != record.txn_id:
            raise ValueError(
                f"log does not match this simulator: expected {txn.txn_id}, got {record.txn_id}"
            )
        probs = {name: oracle.true_success_prob(txn, name) for name in names}
        regret.append(max(probs.values()) - probs[record.action])
    return regret


def cumulative_regret(sim: Simulator, log: Sequence[DecisionRecord]) -> list[float]:
    total = 0.0
    out = []
    for step in instantaneous_regret(sim, log):
        total += step
        out.append(total)
    return out
