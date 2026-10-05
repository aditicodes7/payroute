"""R5: what the constraint costs, and when reliability starts paying for itself.

Two parts.

A. The constrained policy against R4's success-oriented one, paired on identical
   transactions, with the realised p95 and the infeasible rate beside it.

B. A fee-spread sweep. Margin per successful transaction currently varies by 44%
   across gateways while success varies by at most 13%, so the revenue optimum
   is fee-dominated. Rather than retune the world after four rungs of results,
   this locates the spread at which the success term starts to outweigh the fee
   term — computed analytically from the oracle's true probabilities, so it
   needs no policy runs.

Run:  ./.venv/bin/python experiments/r5_constrained.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from _style import INK, INK_MUTED, SERIES, SURFACE, style_axis
from config.default_world import build_default_world
from payroute.decisionlog.metrics import paired_difference, summarize
from payroute.policies import AdaptiveLinUCB, ConstrainedLinUCB
from payroute.policies.objective import expected_net_revenue
from payroute.policies.runner import run_policy
from payroute.sim import Simulator

RESULTS = Path(__file__).resolve().parents[1] / "results"

N = 40_000
SEED = 5
BUDGET_MS = 420.0
SPREADS = [0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0]


def part_a(cfg):
    constrained = ConstrainedLinUCB.for_config(
        cfg, p95_budget_ms=BUDGET_MS, half_life_minutes=5.0, epsilon=0.05, seed=11
    )
    success_only = AdaptiveLinUCB.for_config(cfg, half_life_minutes=5.0, epsilon=0.05, seed=11)

    c_log = run_policy(Simulator(cfg, seed=SEED), constrained, N)
    s_log = run_policy(Simulator(cfg, seed=SEED), success_only, N)

    print(f"\nA. CONSTRAINED vs SUCCESS-ORIENTED  (n = {N:,}, same transactions)")
    print(f"{'policy':<22}{'success':>10}{'net rev / 1k':>15}{'realised p95':>15}{'infeasible':>13}")
    print("-" * 75)
    for label, log in (("constrained (R5)", c_log), ("success-oriented (R4)", s_log)):
        summary = summarize(log)
        latencies = sorted(r.outcome.latency_ms for r in log)
        p95 = latencies[int(len(latencies) * 0.95)]
        infeasible = sum(1 for r in log if r.constraint_status == "infeasible") / len(log)
        marker = "" if label.startswith("constrained") else "  (unconstrained)"
        print(f"{label:<22}{summary.success_rate:>10.2%}{summary.net_revenue_per_1k:>15,.0f}"
              f"{p95:>13.0f}ms{infeasible:>12.2%}{marker}")

    diff = paired_difference(s_log, c_log)
    print(f"\n   paired, constrained minus success-oriented:")
    print(f"     success    {diff.success_rate_delta:+.2%} [{diff.success_ci[0]:+.2%}, {diff.success_ci[1]:+.2%}]")
    print(f"     net rev/1k {diff.net_revenue_per_1k_delta:+,.0f} "
          f"[{diff.net_revenue_ci[0]:+,.0f}, {diff.net_revenue_ci[1]:+,.0f}]")

    held = sum(1 for r in c_log if r.constraint_status == "satisfied") / len(c_log)
    print(f"\n   the latency constraint held on {held:.2%} of decisions")
    return c_log


def part_b(cfg):
    """How often does the money disagree with reliability, as the fee spread
    varies? Computed from the oracle's true probabilities — no policy involved,
    so this measures the PROBLEM rather than any solution to it."""
    sim = Simulator(cfg, seed=SEED)
    txns = list(sim.transactions(20_000))
    oracle = sim.fresh().oracle

    base = {g.name: g.fee.bps for g in cfg.gateways}
    flats = {g.name: g.fee.flat_paise for g in cfg.gateways}
    mean_bps = sum(base.values()) / len(base)

    rows = []
    print(f"\nB. FEE-SPREAD SWEEP — when does reliability start paying for itself?")
    print(f"{'spread':>8}{'fee bps':>26}{'money disagrees':>18}{'revenue gap/1k':>17}")
    print("-" * 71)

    for k in SPREADS:
        fees = {g: mean_bps + k * (base[g] - mean_bps) for g in base}
        disagree = 0
        gap = 0.0
        for txn in txns:
            probs = {g: oracle.true_success_prob(txn, g) for g in base}
            by_money = max(probs, key=lambda g: expected_net_revenue(
                txn.amount_paise, cfg.take_rate_bps, flats[g], fees[g], probs[g]))
            by_success = max(probs, key=lambda g: probs[g])
            if by_money != by_success:
                disagree += 1
            gap += expected_net_revenue(txn.amount_paise, cfg.take_rate_bps, flats[by_money],
                                        fees[by_money], probs[by_money]) - \
                   expected_net_revenue(txn.amount_paise, cfg.take_rate_bps, flats[by_success],
                                        fees[by_success], probs[by_success])
        rate = disagree / len(txns)
        gap_per_1k = gap / len(txns) * 1_000
        rows.append({"spread": k, "disagreement_rate": rate, "revenue_gap_per_1k": gap_per_1k,
                     **{f"bps_{g}": round(fees[g], 1) for g in base}})
        shown = " / ".join(f"{fees[g]:.0f}" for g in cfg.gateway_names)
        print(f"{k:>8.2f}{shown:>26}{rate:>17.1%}{gap_per_1k:>17,.0f}")

    with (RESULTS / "r5_fee_spread.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    fig, (top, bottom) = plt.subplots(2, 1, figsize=(10.0, 7.4), sharex=True)
    fig.patch.set_facecolor(SURFACE)
    style_axis(top, y_formatter=lambda v, _: f"{v:.0%}")
    style_axis(bottom, y_formatter=lambda v, _: f"{v:,.0f}")

    xs = [r["spread"] for r in rows]
    top.plot(xs, [r["disagreement_rate"] for r in rows], color=SERIES[0], linewidth=2.0,
             marker="o", markersize=5)
    bottom.plot(xs, [r["revenue_gap_per_1k"] for r in rows], color=SERIES[1], linewidth=2.0,
                marker="o", markersize=5)
    for ax in (top, bottom):
        ax.axvline(1.0, color=INK_MUTED, linewidth=1.0, linestyle=":")
    top.annotate("this project's world", xy=(1.05, top.get_ylim()[0] + (top.get_ylim()[1] - top.get_ylim()[0]) * 0.12),
                 color=INK_MUTED, fontsize=9)

    top.set_title("share of transactions where the revenue-optimal gateway is NOT the most reliable one",
                  color=INK, fontsize=10.5, loc="left", pad=8)
    bottom.set_title("revenue left on the table by routing for success rate alone (paise per 1,000 txns)",
                     color=INK, fontsize=10.5, loc="left", pad=8)
    bottom.set_xlabel("fee spread, as a multiple of this project's configured spread",
                      color=INK_MUTED, fontsize=9.5, labelpad=8)
    # The question this was built to answer was "at what spread does reliability
    # stop being the right objective?". The answer is that it never was: even at
    # zero bps spread the flat fee alone makes them disagree 17.8% of the time.
    fig.suptitle("R5 — routing for success rate is never revenue-optimal, and the gap grows with the fee spread",
                 x=0.055, y=0.975, ha="left", color=INK, fontsize=11.5)
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.93))
    fig.savefig(RESULTS / "r5_fee_spread.png", dpi=200, facecolor=SURFACE)
    plt.close(fig)


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    cfg = build_default_world()
    if "--part-b" not in sys.argv:
        part_a(cfg)
    part_b(cfg)
    print(f"\nwritten to {RESULTS}/")


if __name__ == "__main__":
    main()
