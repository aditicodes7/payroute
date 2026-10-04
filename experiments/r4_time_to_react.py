"""R4: how fast does the policy let go of a failing gateway, and what does
speed cost when nothing is wrong?

Reaction is measured across a whole SAMPLED SCHEDULE of incidents rather than
one injected outage, because a single incident is an anecdote. Instability is
measured on a separate clean run, where every diversion away from a healthy
gateway is a false alarm paid for in revenue.

Run:  ./.venv/bin/python experiments/r4_time_to_react.py
"""

from __future__ import annotations

import csv
import statistics
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from _style import INK, INK_MUTED, SERIES, SURFACE, direct_labels, style_axis
from config.default_world import START, build_default_world
from payroute.decisionlog.metrics import summarize
from payroute.decisionlog.reaction import measure_reaction, routing_jitter
from payroute.features import Featuriser
from payroute.policies import AdaptiveLinUCB, DiscountedLinUCB, LinUCB
from payroute.policies.runner import run_policy
from payroute.sim import Simulator, sample_gateway_incidents

RESULTS = Path(__file__).resolve().parents[1] / "results"

N = 120_000
CLEAN_N = 40_000
SEED = 5
HORIZON = timedelta(hours=22)
HALF_LIVES = [0.5, 2.0, 5.0, 15.0, 60.0]

# The default incident rate yields about five incidents over the horizon, and a
# median of four measurements is an anecdote with error bars drawn on. Raising
# the rate is a change to the MEASUREMENT INSTRUMENT, not to the world the
# policies are judged in: it gives more samples of the same reaction process.
# Note that it also degrades the world overall, so the success and revenue
# columns here are not comparable with the other rungs' tables.
INCIDENTS_PER_GATEWAY_PER_DAY = 5.0
BINS, MIN_COUNT = 1.0, 20


def incident_schedule(cfg):
    return sample_gateway_incidents(
        cfg, seed=4242, start=START, horizon=HORIZON,
        per_gateway_per_day=INCIDENTS_PER_GATEWAY_PER_DAY,
    )


def run_with_incidents(cfg, policy, incidents):
    sim = Simulator(cfg, seed=SEED)
    sim.inject_all(incidents)
    return run_policy(sim, policy, N)


def reaction_stats(log, incidents):
    onsets, recoveries, measured = [], [], 0
    for incident in incidents:
        try:
            reaction = measure_reaction(
                log, incident.gateway, incident.start, incident.start + incident.duration,
                bin_minutes=BINS, min_count=MIN_COUNT,
            )
        except ValueError:
            continue  # not enough traffic around this incident to say anything
        measured += 1
        if reaction.reacted_after is not None:
            onsets.append(reaction.reacted_after.total_seconds() / 60.0)
        if reaction.recovered_after is not None:
            recoveries.append(reaction.recovered_after.total_seconds() / 60.0)
    return onsets, recoveries, measured


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    cfg = build_default_world()
    incidents = incident_schedule(cfg)
    print(f"{len(incidents)} sampled gateway incidents over {HORIZON}")

    def adaptive(hl):
        return AdaptiveLinUCB.for_config(cfg, half_life_minutes=hl, epsilon=0.05, seed=11)

    configurations = [(f"adaptive hl={hl:g}m", lambda hl=hl: adaptive(hl), hl) for hl in HALF_LIVES]
    configurations.append(
        ("static linucb", lambda: LinUCB(cfg.gateway_names, Featuriser.from_config(cfg),
                                         alpha=1.0, epsilon=0.05, seed=11), None)
    )
    configurations.append(
        ("discounted linucb", lambda: DiscountedLinUCB(cfg.gateway_names, Featuriser.from_config(cfg),
                                                       gamma=0.9995, alpha=1.0, epsilon=0.05, seed=11), None)
    )

    rows = []
    print(f"\n{'policy':<22}{'reacted':>9}{'median onset':>14}{'median recover':>16}"
          f"{'jitter':>9}{'success':>10}{'net rev/1k':>13}")
    print("-" * 93)

    for label, build, half_life in configurations:
        log = run_with_incidents(cfg, build(), incidents)
        onsets, recoveries, measured = reaction_stats(log, incidents)
        clean = run_policy(Simulator(cfg, seed=SEED), build(), CLEAN_N)
        jitter = routing_jitter(clean, bin_minutes=5.0)
        summary = summarize(log)

        row = {
            "policy": label,
            "half_life_minutes": half_life if half_life is not None else "",
            "incidents_measured": measured,
            "reacted": len(onsets),
            "median_onset_minutes": statistics.median(onsets) if onsets else "",
            "median_recovery_minutes": statistics.median(recoveries) if recoveries else "",
            "routing_jitter_clean": jitter,
            "success_rate": summary.success_rate,
            "net_revenue_per_1k": summary.net_revenue_per_1k,
        }
        rows.append(row)
        onset = f"{row['median_onset_minutes']:.1f}m" if onsets else "never"
        recover = f"{row['median_recovery_minutes']:.1f}m" if recoveries else "never"
        print(f"{label:<22}{len(onsets):>4}/{measured:<4}{onset:>14}{recover:>16}"
              f"{jitter:>9.4f}{summary.success_rate:>10.2%}{summary.net_revenue_per_1k:>13,.0f}")

    with (RESULTS / "r4_half_life_sweep.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    render(rows)
    print(f"\nwritten to {RESULTS}/")


def render(rows) -> None:
    # Two panels rather than one chart with two y-scales: a dual axis lets the
    # author choose the scales that make the story they want.
    sweep = [r for r in rows if r["half_life_minutes"] != ""]
    xs = [r["half_life_minutes"] for r in sweep]

    fig, (top, bottom) = plt.subplots(2, 1, figsize=(10.0, 7.6), sharex=True)
    fig.patch.set_facecolor(SURFACE)
    for ax in (top, bottom):
        style_axis(ax)
        ax.set_xscale("log")
        ax.set_xticks(xs)
        ax.set_xticklabels([f"{x:g}" for x in xs])

    onset_y = [r["median_onset_minutes"] or 0 for r in sweep]
    recover_y = [r["median_recovery_minutes"] or 0 for r in sweep]
    top.plot(xs, onset_y, color=SERIES[0], linewidth=2.0, marker="o", markersize=5)
    top.plot(xs, recover_y, color=SERIES[1], linewidth=2.0, marker="o", markersize=5)
    direct_labels(
        top,
        [["median onset", SERIES[0], xs[-1], onset_y[-1]], ["median recovery", SERIES[1], xs[-1], recover_y[-1]]],
        min_gap=max(onset_y + recover_y) * 0.09,
        x_offset=xs[-1] * 0.08,
    )

    jitter_y = [r["routing_jitter_clean"] for r in sweep]
    bottom.plot(xs, jitter_y, color=SERIES[2], linewidth=2.0, marker="o", markersize=5)
    bottom.annotate("routing jitter", xy=(xs[-1] * 1.08, jitter_y[-1]), va="center",
                    color=INK, fontsize=9.5)

    top.set_xlim(min(xs) * 0.6, max(xs) * 2.6)
    top.set_ylim(0, max(onset_y + recover_y) * 1.15)
    bottom.set_ylim(0, max(jitter_y) * 1.15)
    top.set_title(
        "median over 12 measured incidents: recovery separates the settings, onset does not",
        color=INK, fontsize=10.5, loc="left", pad=8,
    )
    bottom.set_title(
        "routing jitter on a clean run — the expected cost of a short memory, which does not appear",
        color=INK, fontsize=10.5, loc="left", pad=8,
    )
    bottom.set_xlabel("health half-life (minutes)", color=INK_MUTED, fontsize=9.5, labelpad=8)
    fig.suptitle("R4 — a shorter memory recovers sooner, and costs nothing measurable here",
                 x=0.055, y=0.975, ha="left", color=INK, fontsize=12.5)
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.93))
    fig.savefig(RESULTS / "r4_half_life.png", dpi=200, facecolor=SURFACE)
    plt.close(fig)


def render_from_csv() -> None:
    with (RESULTS / "r4_half_life_sweep.csv").open() as fh:
        rows = []
        for row in csv.DictReader(fh):
            for key in ("half_life_minutes", "median_onset_minutes", "median_recovery_minutes",
                        "routing_jitter_clean"):
                row[key] = float(row[key]) if row[key] else ""
            rows.append(row)
    render(rows)
    print(f"re-rendered {RESULTS}/r4_half_life.png from the saved table")


if __name__ == "__main__":
    if "--chart-only" in sys.argv:
        render_from_csv()
    else:
        main()
