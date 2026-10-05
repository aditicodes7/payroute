"""R7: what the learned retry policy recovers, and what it costs.

Trained on one seed, evaluated on another. The headline is the recovered
transaction rate against a fixed retry-once policy — but reported beside net
revenue, because a policy that recovers more while spending more on retries is
not obviously better.

Run:  ./.venv/bin/python experiments/r7_retry.py
"""

from __future__ import annotations

import csv
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.default_world import START, build_default_world
from payroute.policies import ConstrainedLinUCB
from payroute.retry import (
    AlwaysRetry,
    FixedRetryOnce,
    NeverRetry,
    TabularQ,
    all_states,
    build_state,
    run_with_retries,
)
from payroute.sim import Simulator, sample_gateway_incidents, sample_issuer_incidents
from payroute.sim.failures import FAILURE_REASONS

RESULTS = Path(__file__).resolve().parents[1] / "results"
N = 60_000
TRAIN_N = 120_000
EVAL_SEED, TRAIN_SEED = 5, 101
HORIZON = timedelta(hours=26)


def make_sim(cfg, seed):
    sim = Simulator(cfg, seed=seed)
    sim.inject_all(sample_gateway_incidents(cfg, seed=4242, start=START, horizon=HORIZON))
    sim.inject_all(sample_issuer_incidents(cfg, seed=4243, start=START, horizon=HORIZON))
    return sim


def routing(cfg):
    return ConstrainedLinUCB.for_config(cfg, p95_budget_ms=420.0, epsilon=0.05, seed=11)


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    cfg = build_default_world()

    learner = TabularQ(all_states(cfg.method_mix), alpha=0.1, gamma=1.0, epsilon=0.2, seed=3)
    run_with_retries(make_sim(cfg, TRAIN_SEED), routing(cfg), learner, TRAIN_N)
    learner.epsilon = 0.0

    contenders = {
        "never_retry": NeverRetry(),
        "fixed_retry_once": FixedRetryOnce(),
        "always_retry": AlwaysRetry(),
        "learned (Q)": learner,
    }

    rows = []
    print(f"\nR7 RETRY POLICIES — n = {N:,}, same transactions, incidents on")
    print(f"{'policy':<20}{'success':>10}{'recovered':>12}{'attempts/txn':>15}"
          f"{'retry cost/1k':>16}{'net rev/1k':>14}")
    print("-" * 87)
    for label, policy in contenders.items():
        result = run_with_retries(make_sim(cfg, EVAL_SEED), routing(cfg), policy, N)
        rows.append({"policy": label, **result.__dict__})
        print(f"{label:<20}{result.success_rate:>10.2%}{result.recovered_rate:>12.2%}"
              f"{result.attempts_per_transaction:>15.3f}{result.retry_cost_per_1k:>16,.0f}"
              f"{result.net_revenue_per_1k:>14,.0f}")

    with (RESULTS / "r7_retry.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWHAT IT LEARNED — greedy action at attempt 1, by failure reason")
    print(f"{'reason':<22}{'upi':<26}{'card':<26}")
    print("-" * 74)
    for reason in FAILURE_REASONS:
        cells = []
        for method in ("upi", "card"):
            state = build_state(1, method, reason)
            action = learner.greedy(state)
            best = learner.q(state, action)
            cells.append(f"{action} ({best:+,.0f})")
        print(f"{reason:<22}{cells[0]:<26}{cells[1]:<26}")

    print(f"\nwritten to {RESULTS}/r7_retry.csv")


if __name__ == "__main__":
    main()
