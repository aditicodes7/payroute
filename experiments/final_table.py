"""The measurement table across every rung, against the R1 baselines.

One scenario, one seed, one transaction count, every policy. Paired against
round_robin, so the interval on each difference is far tighter than on either
mean — the common-random-number design from R0 paying off one last time.

Run:  ./.venv/bin/python experiments/final_table.py
"""

from __future__ import annotations

import csv
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.default_world import START, build_default_world
from payroute.decisionlog.metrics import paired_difference, summarize
from payroute.features import Featuriser
from payroute.policies import (
    AdaptiveLinUCB,
    AlwaysCheapest,
    BestPerSegment,
    ConstrainedLinUCB,
    HistoricallyBest,
    LinUCB,
    RoundRobin,
    UniformRandom,
)
from payroute.policies.runner import run_policy
from payroute.sim import Simulator, sample_gateway_incidents, sample_issuer_incidents

RESULTS = Path(__file__).resolve().parents[1] / "results"
N = 100_000
EVAL_SEED, TRAIN_SEED = 20260918, 101
HORIZON = timedelta(hours=30)


def make_sim(cfg):
    sim = Simulator(cfg, seed=EVAL_SEED)
    sim.inject_all(sample_gateway_incidents(cfg, seed=4242, start=START, horizon=HORIZON))
    sim.inject_all(sample_issuer_incidents(cfg, seed=4243, start=START, horizon=HORIZON))
    return sim


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    cfg = build_default_world()
    training = run_policy(
        Simulator(cfg, seed=TRAIN_SEED), UniformRandom(cfg.gateway_names, seed=TRAIN_SEED), 120_000
    )

    policies = [
        ("R1", "round_robin", lambda: RoundRobin(cfg.gateway_names)),
        ("R1", "uniform_random", lambda: UniformRandom(cfg.gateway_names, seed=7)),
        ("R1", "always_cheapest", lambda: AlwaysCheapest(cfg)),
        ("R1", "historically_best", lambda: HistoricallyBest.fit(training)),
        ("R1", "best_per_segment", lambda: BestPerSegment.fit(training)),
        ("R2", "linucb", lambda: LinUCB(cfg.gateway_names, Featuriser.from_config(cfg),
                                        alpha=1.0, epsilon=0.05, seed=11)),
        ("R4", "adaptive_linucb", lambda: AdaptiveLinUCB.for_config(
            cfg, half_life_minutes=5.0, epsilon=0.05, seed=11)),
        ("R5", "constrained_linucb", lambda: ConstrainedLinUCB.for_config(
            cfg, p95_budget_ms=420.0, half_life_minutes=5.0, epsilon=0.05, seed=11)),
    ]

    logs = {}
    rows = []
    for rung, name, build in policies:
        log = run_policy(make_sim(cfg), build(), N)
        logs[name] = log
        summary = summarize(log)
        latencies = sorted(r.outcome.latency_ms for r in log)
        rows.append({
            "rung": rung, "policy": name, "n": summary.n,
            "success_rate": summary.success_rate,
            "success_lo": summary.success_ci[0], "success_hi": summary.success_ci[1],
            "net_rev_per_1k": summary.net_revenue_per_1k,
            "p95_latency_ms": latencies[int(len(latencies) * 0.95)],
        })

    baseline = logs["round_robin"]
    for row in rows:
        if row["policy"] == "round_robin":
            row["success_delta"] = 0.0
            row["net_rev_delta"] = 0.0
            row["delta_lo"] = row["delta_hi"] = 0.0
            continue
        diff = paired_difference(baseline, logs[row["policy"]])
        row["success_delta"] = diff.success_rate_delta
        row["net_rev_delta"] = diff.net_revenue_per_1k_delta
        row["delta_lo"] = diff.net_revenue_ci[0]
        row["delta_hi"] = diff.net_revenue_ci[1]

    with (RESULTS / "final_table.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nPAYROUTE — all rungs, n = {N:,}, identical transactions, incidents on")
    print(f"{'rung':<6}{'policy':<22}{'success':>18}{'net rev/1k':>13}{'vs RR':>12}"
          f"{'p95':>9}")
    print("-" * 80)
    for row in rows:
        success = f"{row['success_rate']:.2%} [{row['success_lo']:.2%},{row['success_hi']:.2%}]"
        delta = "—" if row["policy"] == "round_robin" else f"{row['net_rev_delta']:+,.0f}"
        print(f"{row['rung']:<6}{row['policy']:<22}{success:>18}{row['net_rev_per_1k']:>13,.0f}"
              f"{delta:>12}{row['p95_latency_ms']:>8.0f}ms")

    print(f"\nwritten to {RESULTS}/final_table.csv")


if __name__ == "__main__":
    main()
