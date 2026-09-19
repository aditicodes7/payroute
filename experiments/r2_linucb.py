"""R2: LinUCB against the R1 baselines, and the price of being able to measure.

Three outputs:

  1. LinUCB vs all five baselines, paired on identical transactions.
  2. The regret curve — exact, because the simulator knows the true best arm.
  3. An epsilon sweep, which is the axis R3's headline chart is built on:
     as exploration falls, performance improves and the log becomes
     progressively less able to evaluate anything else.

Run:  ./.venv/bin/python experiments/r2_linucb.py
"""

from __future__ import annotations

import csv
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from _style import INK, SERIES, SHADE, SURFACE, direct_labels, style_axis
from config.default_world import START, build_default_world
from payroute.decisionlog.metrics import paired_difference, summarize
from payroute.features import Featuriser
from payroute.ope.regret import cumulative_regret
from payroute.policies import (
    AlwaysCheapest,
    BestPerSegment,
    HistoricallyBest,
    LinUCB,
    RoundRobin,
    UniformRandom,
)
from payroute.policies.runner import run_policy
from payroute.sim import Simulator, sample_gateway_incidents, sample_issuer_incidents

RESULTS = Path(__file__).resolve().parents[1] / "results"

N = 150_000
EVAL_SEED = 20260918
TRAIN_SEED = 101
HORIZON = timedelta(hours=30)


def make_sim(cfg, with_incidents=True):
    sim = Simulator(cfg, seed=EVAL_SEED)
    if with_incidents:
        sim.inject_all(sample_gateway_incidents(cfg, seed=4242, start=START, horizon=HORIZON))
        sim.inject_all(sample_issuer_incidents(cfg, seed=4243, start=START, horizon=HORIZON))
    return sim


def linucb(cfg, epsilon):
    return LinUCB(cfg.gateway_names, Featuriser.from_config(cfg), alpha=1.0, epsilon=epsilon, seed=11)


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    cfg = build_default_world()

    training = run_policy(
        Simulator(cfg, seed=TRAIN_SEED), UniformRandom(cfg.gateway_names, seed=TRAIN_SEED), 120_000
    )

    contenders = {
        "round_robin": lambda: RoundRobin(cfg.gateway_names),
        "uniform_random": lambda: UniformRandom(cfg.gateway_names, seed=7),
        "always_cheapest": lambda: AlwaysCheapest(cfg),
        "historically_best": lambda: HistoricallyBest.fit(training),
        "best_per_segment": lambda: BestPerSegment.fit(training),
        "linucb(eps=0.05)": lambda: linucb(cfg, 0.05),
    }

    logs = {name: run_policy(make_sim(cfg), build(), N) for name, build in contenders.items()}

    print(f"\nR2 vs R1 BASELINES — incidents scenario, n = {N:,}, 95% intervals")
    print(f"{'policy':<20}{'success rate':>22}{'net revenue / 1k':>22}")
    print("-" * 64)
    for name, log in logs.items():
        s = summarize(log)
        print(
            f"{name:<20}{s.success_rate:>12.2%} [{s.success_ci[0]:.2%},{s.success_ci[1]:.2%}]"
            f"{s.net_revenue_per_1k:>14,.0f}"
        )

    print(f"\nPAIRED: linucb(eps=0.05) minus each baseline (same transactions)")
    print(f"{'baseline':<20}{'success delta':>26}{'net revenue delta':>22}")
    print("-" * 68)
    for name in contenders:
        if name.startswith("linucb"):
            continue
        d = paired_difference(logs[name], logs["linucb(eps=0.05)"])
        print(
            f"{name:<20}{d.success_rate_delta:>+12.2%} "
            f"[{d.success_ci[0]:+.2%},{d.success_ci[1]:+.2%}]{d.net_revenue_per_1k_delta:>+14,.0f}"
        )

    # --- epsilon sweep: the axis R3 is built on --------------------------
    print(f"\nEPSILON SWEEP — what exploration costs, and what it buys")
    print(f"{'epsilon':>9}{'success':>11}{'net rev / 1k':>15}{'regret/txn (tail)':>20}{'min propensity':>17}")
    print("-" * 72)

    sweep_rows = []
    sweep_logs = {}
    for epsilon in (0.0, 0.01, 0.05, 0.10, 0.30):
        sim = make_sim(cfg)
        log = run_policy(sim, linucb(cfg, epsilon), N)
        sweep_logs[epsilon] = (sim, log)
        s = summarize(log)
        curve = cumulative_regret(sim, log)
        tail = (curve[-1] - curve[-N // 5]) / (N // 5)
        min_p = min(r.propensity for r in log)
        sweep_rows.append(
            {
                "epsilon": epsilon,
                "success_rate": s.success_rate,
                "net_rev_per_1k": s.net_revenue_per_1k,
                "tail_regret_per_txn": tail,
                "min_propensity": min_p,
                "total_regret": curve[-1],
            }
        )
        print(f"{epsilon:>9.2f}{s.success_rate:>11.2%}{s.net_revenue_per_1k:>15,.0f}{tail:>20.5f}{min_p:>17.4f}")

    with (RESULTS / "r2_epsilon_sweep.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(sweep_rows[0]))
        writer.writeheader()
        writer.writerows(sweep_rows)

    # --- regret curve ----------------------------------------------------
    curves = {
        "round_robin": cumulative_regret(make_sim(cfg), logs["round_robin"]),
        "best_per_segment": cumulative_regret(make_sim(cfg), logs["best_per_segment"]),
        "linucb(eps=0.05)": cumulative_regret(*sweep_logs[0.05]),
    }

    fig, ax = plt.subplots(figsize=(10.5, 5.6))
    fig.patch.set_facecolor(SURFACE)
    style_axis(
        ax,
        x_formatter=lambda v, _: f"{int(v / 1000)}k",
        y_formatter=lambda v, _: f"{int(v):,}",
    )

    step = 200
    ends = []
    for (name, curve), colour in zip(curves.items(), SERIES):
        xs = list(range(0, len(curve), step))
        ys = [curve[i] for i in xs]
        ax.plot(xs, ys, color=colour, linewidth=2.0, solid_capstyle="round", label=name)
        ax.plot([xs[-1]], [ys[-1]], marker="o", markersize=5, color=colour, zorder=3)
        ends.append([name, colour, xs[-1], ys[-1]])

    top = max(c[-1] for c in curves.values())
    ax.set_xlim(0, N * 1.22)
    ax.set_ylim(0, top * 1.08)
    direct_labels(ax, ends, min_gap=top * 0.06, x_offset=N * 0.015)

    ax.set_xlabel("transactions", color=INK, fontsize=9.5, labelpad=8)
    ax.set_ylabel("cumulative regret (successes forgone)", color=INK, fontsize=9.5)
    fig.suptitle(
        "R2 — cumulative regret: a flattening curve is a policy that has finished learning",
        x=0.055, y=0.97, ha="left", color=INK, fontsize=12.5,
    )
    legend = fig.legend(loc="upper left", bbox_to_anchor=(0.052, 0.915), frameon=False, ncol=3,
                        fontsize=9.5, handlelength=1.6, columnspacing=1.4)
    for text in legend.get_texts():
        text.set_color(INK)
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.86))
    fig.savefig(RESULTS / "r2_regret.png", dpi=200, facecolor=SURFACE)
    plt.close(fig)

    with (RESULTS / "r2_regret.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["transactions", *curves])
        for i in range(0, N, step):
            writer.writerow([i, *(f"{curves[name][i]:.3f}" for name in curves)])

    print(f"\nwritten to {RESULTS}/")


if __name__ == "__main__":
    main()
