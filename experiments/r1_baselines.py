"""R1: the baseline table — the numbers every later rung has to beat.

Each policy is run twice over the SAME transactions: once on a clean world, and
once on a world with a sampled incident schedule. The gap between those two
columns is the space every later rung competes in. A policy that cannot react
is perfectly fine when nothing is broken.

Fitted baselines (historically_best, best_per_segment) are fitted on a separate
clean training run under a different seed — fitting them on the run they are
scored against would let them peek at the noise in their own test set.

Run:  ./.venv/bin/python experiments/r1_baselines.py
"""

from __future__ import annotations

import csv
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.default_world import START, build_default_world
from payroute.decisionlog.metrics import paired_difference, summarize
from payroute.policies import (
    AlwaysCheapest,
    BestPerSegment,
    HistoricallyBest,
    RoundRobin,
    UniformRandom,
)
from payroute.policies.runner import run_policy
from payroute.sim import Simulator, sample_gateway_incidents, sample_issuer_incidents

RESULTS = Path(__file__).resolve().parents[1] / "results"

N = 200_000
EVAL_SEED = 20260918
TRAIN_SEED = 101
HORIZON = timedelta(hours=34)


def make_sim(cfg, with_incidents: bool):
    sim = Simulator(cfg, seed=EVAL_SEED)
    if with_incidents:
        sim.inject_all(sample_gateway_incidents(cfg, seed=4242, start=START, horizon=HORIZON))
        sim.inject_all(sample_issuer_incidents(cfg, seed=4243, start=START, horizon=HORIZON))
    return sim


def build_policies(cfg):
    """Fit the two learned baselines on their own clean training run."""
    training = run_policy(
        Simulator(cfg, seed=TRAIN_SEED), UniformRandom(cfg.gateway_names, seed=TRAIN_SEED), 120_000
    )
    return [
        RoundRobin(cfg.gateway_names),
        UniformRandom(cfg.gateway_names, seed=7),
        AlwaysCheapest(cfg),
        HistoricallyBest.fit(training),
        BestPerSegment.fit(training),
    ]


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    cfg = build_default_world()

    rows = []
    logs = {}
    for scenario, with_incidents in (("clean", False), ("incidents", True)):
        for policy in build_policies(cfg):
            log = run_policy(make_sim(cfg, with_incidents), policy, N)
            summary = summarize(log)
            logs[(scenario, policy.policy_id)] = log
            rows.append(
                {
                    "scenario": scenario,
                    "policy": policy.policy_id,
                    "n": summary.n,
                    "success_rate": summary.success_rate,
                    "success_lo": summary.success_ci[0],
                    "success_hi": summary.success_ci[1],
                    "net_rev_per_1k": summary.net_revenue_per_1k,
                    "net_rev_lo": summary.net_revenue_ci[0],
                    "net_rev_hi": summary.net_revenue_ci[1],
                }
            )

    with (RESULTS / "r1_baselines.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    for scenario in ("clean", "incidents"):
        print(f"\n{scenario.upper()}  (n = {N:,} transactions, 95% intervals)")
        print(f"{'policy':<20}{'success rate':>22}{'net revenue / 1k txn (paise)':>34}")
        print("-" * 76)
        for row in (r for r in rows if r["scenario"] == scenario):
            sr = f"{row['success_rate']:.2%}  [{row['success_lo']:.2%}, {row['success_hi']:.2%}]"
            nr = f"{row['net_rev_per_1k']:>10,.0f}  [{row['net_rev_lo']:,.0f}, {row['net_rev_hi']:,.0f}]"
            print(f"{row['policy']:<20}{sr:>22}{nr:>34}")

    print("\nPAIRED vs round_robin, incidents scenario (same transactions, 95% intervals)")
    print(f"{'policy':<20}{'success delta':>22}{'net revenue delta / 1k':>28}")
    print("-" * 70)
    baseline = logs[("incidents", "round_robin")]
    for policy_id in ("uniform_random", "always_cheapest", "historically_best", "best_per_segment"):
        diff = paired_difference(baseline, logs[("incidents", policy_id)])
        sd = f"{diff.success_rate_delta:+.2%}  [{diff.success_ci[0]:+.2%}, {diff.success_ci[1]:+.2%}]"
        nd = (
            f"{diff.net_revenue_per_1k_delta:+,.0f}  "
            f"[{diff.net_revenue_ci[0]:+,.0f}, {diff.net_revenue_ci[1]:+,.0f}]"
        )
        print(f"{policy_id:<20}{sd:>22}{nd:>28}")

    print("\nTRAFFIC SHARE INTO A DEGRADED GATEWAY (does any baseline react?)")
    print(f"{'policy':<20}{'share to atlas':>18}")
    print("-" * 38)
    for policy_id in ("round_robin", "uniform_random", "always_cheapest", "historically_best", "best_per_segment"):
        log = logs[("incidents", policy_id)]
        share = sum(r.action == "atlas" for r in log) / len(log)
        print(f"{policy_id:<20}{share:>17.1%}")

    print(f"\nwritten to {RESULTS}/r1_baselines.csv")


if __name__ == "__main__":
    main()
