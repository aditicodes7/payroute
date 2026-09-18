"""R0 acceptance artifact: per-gateway success rate over time, with and without
an injected outage.

Produces, in results/:
  r0_gateway_outage.png / .csv   one gateway's stack degrades
  r0_issuer_outage.png  / .csv   a BANK degrades, and every gateway feels it
                                 differently

and prints the headline numbers to stdout.

Run:  ./.venv/bin/python experiments/r0_simulator_profile.py

Note that both scenarios in a figure are driven by the SAME seed, so they see
the identical transaction stream. Any difference between the panels is the
incident and nothing else — that is what the separate RNG streams buy us.
"""

from __future__ import annotations

import csv
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run without installing

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

from config.default_world import BUSY_HOUR_OFFSET, START, build_default_world
from payroute.sim import GatewayIncident, IssuerIncident, Simulator

RESULTS = Path(__file__).resolve().parents[1] / "results"

# Validated categorical palette, slots 1-3 (blue / orange / aqua). All-pairs
# CVD dE 9.2, normal-vision dE 24.0 on the light surface. Aqua sits below 3:1
# contrast, so the relief rule applies: every line is directly labelled and a
# CSV table view ships beside the chart.
SERIES = {"atlas": "#2a78d6", "beacon": "#eb6834", "cirrus": "#1baf7a"}
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
GRID = "#ececE8"
SPINE = "#d8d7d2"
SHADE = "#f0efec"

PLOT_FROM_HOUR = 8.0
PLOT_TO_HOUR = 20.0


def run_scenario(cfg, seed, incidents, until_hours, bin_minutes, issuer=None):
    """Return {gateway: [(hour, rate, n), ...]} over binned wall-clock time."""
    sim = Simulator(cfg, seed=seed)
    for incident in incidents:
        sim.inject(incident)

    horizon = START + timedelta(hours=until_hours)
    names = cfg.gateway_names
    bins: dict[int, dict[str, list[int]]] = {}

    while True:
        txn = sim.next_transaction()
        if txn.ts >= horizon:
            break
        if issuer is not None and txn.issuer != issuer:
            continue
        key = int((txn.ts - START).total_seconds() // (bin_minutes * 60))
        slot = bins.setdefault(key, {n: [0, 0] for n in names})
        for name in names:
            slot[name][0] += int(sim.resolve(txn, name).success)
            slot[name][1] += 1

    series = {n: [] for n in names}
    for key in sorted(bins):
        hour = key * bin_minutes / 60.0
        for name in names:
            ok, total = bins[key][name]
            if total >= 100:  # below this a bin is noise, not a measurement
                series[name].append((hour, ok / total, total))
    return series


def phase_rates(series, window_start_hour, window_end_hour):
    """Collapse a binned series into before / during / after success rates."""
    out = {}
    for name, points in series.items():
        phases = {"before": [0, 0], "during": [0, 0], "after": [0, 0]}
        for hour, rate, n in points:
            if hour < window_start_hour:
                phase = "before"
            elif hour < window_end_hour:
                phase = "during"
            else:
                phase = "after"
            phases[phase][0] += rate * n
            phases[phase][1] += n
        out[name] = {p: (s / n if n else float("nan")) for p, (s, n) in phases.items()}
    return out


def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(SPINE)
        ax.spines[side].set_linewidth(1.0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK_MUTED, labelsize=9, length=0)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{int(v) % 24:02d}:00"))
    ax.set_xlim(PLOT_FROM_HOUR, PLOT_TO_HOUR + 1.4)  # headroom for direct labels
    ax.set_ylim(0.25, 1.0)


def _draw(ax, series, title, window=None):
    _style(ax)
    if window is not None:
        ax.axvspan(window[0], window[1], color=SHADE, zorder=0)
        ax.annotate(
            "injected outage",
            xy=((window[0] + window[1]) / 2, 0.29),
            ha="center",
            color=INK_MUTED,
            fontsize=8.5,
        )

    ends = []
    for name, colour in SERIES.items():
        points = [(h, r) for h, r, _ in series[name] if PLOT_FROM_HOUR <= h <= PLOT_TO_HOUR]
        xs = [h for h, _ in points]
        ys = [r for _, r in points]
        ax.plot(xs, ys, color=colour, linewidth=2.0, solid_capstyle="round", label=name)
        ax.plot([xs[-1]], [ys[-1]], marker="o", markersize=5, color=colour, zorder=3)
        ends.append([name, colour, xs[-1], ys[-1]])

    # Direct labels, nudged apart so they never overlap: the three gateways sit
    # within a couple of points of each other at the right edge, which is
    # exactly where naive end-of-line labels collide. A colored dot carries
    # identity; the text itself stays in ink.
    ends.sort(key=lambda e: e[3], reverse=True)
    min_gap = 0.038
    for i in range(1, len(ends)):
        if ends[i - 1][3] - ends[i][3] < min_gap:
            ends[i][3] = ends[i - 1][3] - min_gap
    for name, colour, x, y in ends:
        ax.annotate(name, xy=(x + 0.22, y), va="center", color=INK, fontsize=9.5)

    ax.set_title(title, color=INK, fontsize=11.5, loc="left", pad=10)


def figure(series_clean, series_outage, window, titles, subtitle, path, note=None):
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(10.5, 7.4), sharex=True)
    fig.patch.set_facecolor(SURFACE)

    _draw(top, series_clean, titles[0])
    _draw(bottom, series_outage, titles[1], window=window)
    if note is not None:
        bottom.annotate(
            note,
            xy=(window[1] + 0.25, 0.44),
            ha="left",
            va="center",
            color=INK_MUTED,
            fontsize=9,
        )

    bottom.set_xlabel("time of day (UTC)", color=INK_MUTED, fontsize=9.5, labelpad=8)
    top.set_ylabel("success rate", color=INK_MUTED, fontsize=9.5)
    bottom.set_ylabel("success rate", color=INK_MUTED, fontsize=9.5)

    handles, labels = top.get_legend_handles_labels()
    legend = fig.legend(
        handles,
        labels,
        loc="upper left",
        bbox_to_anchor=(0.055, 0.945),
        frameon=False,
        ncol=3,
        fontsize=9.5,
        handlelength=1.6,
        columnspacing=1.4,
    )
    for text in legend.get_texts():
        text.set_color(INK)

    fig.suptitle(subtitle, x=0.062, y=0.985, ha="left", color=INK, fontsize=13.0)
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.90))
    fig.savefig(path, dpi=200, facecolor=SURFACE)
    plt.close(fig)


def write_csv(path, series_clean, series_outage):
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["scenario", "gateway", "hour_utc", "success_rate", "transactions"])
        for scenario, series in (("clean", series_clean), ("outage", series_outage)):
            for name, points in series.items():
                for hour, rate, n in points:
                    writer.writerow([scenario, name, f"{hour:.2f}", f"{rate:.4f}", n])


def report(label, clean, outage, window):
    print(f"\n{label}")
    print(f"{'gateway':<10}{'scenario':<10}{'before':>9}{'during':>9}{'after':>9}{'drop':>9}")
    print("-" * 56)
    for name in SERIES:
        for scenario, series in (("clean", clean), ("outage", outage)):
            p = phase_rates(series, *window)[name]
            drop = p["before"] - p["during"]
            print(
                f"{name:<10}{scenario:<10}{p['before']:>8.1%}{p['during']:>9.1%}"
                f"{p['after']:>9.1%}{drop:>+9.1%}"
            )


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    cfg = build_default_world()

    window_start = BUSY_HOUR_OFFSET.total_seconds() / 3600.0
    seed = 20260918

    # --- gateway-level incident -------------------------------------------
    gw_window = (window_start, window_start + 40 / 60)
    gw_incident = GatewayIncident(
        gateway="atlas",
        start=START + BUSY_HOUR_OFFSET,
        duration=timedelta(minutes=40),
        peak_severity=0.65,
    )
    clean = run_scenario(cfg, seed, [], until_hours=21, bin_minutes=10)
    outage = run_scenario(cfg, seed, [gw_incident], until_hours=21, bin_minutes=10)

    figure(
        clean,
        outage,
        gw_window,
        ("no incident", "atlas degraded for 40 minutes"),
        "R0 — gateway incident: one gateway's stack degrades",
        RESULTS / "r0_gateway_outage.png",
    )
    write_csv(RESULTS / "r0_gateway_outage.csv", clean, outage)
    report("GATEWAY INCIDENT (atlas, 40 min, peak severity 0.65) — all traffic", clean, outage, gw_window)

    # --- issuer-level incident --------------------------------------------
    is_window = (window_start, window_start + 45 / 60)
    is_incident = IssuerIncident(
        issuer="hdfc",
        start=START + BUSY_HOUR_OFFSET,
        duration=timedelta(minutes=45),
        peak_severity=0.60,
    )
    clean_h = run_scenario(cfg, seed, [], until_hours=21, bin_minutes=20, issuer="hdfc")
    outage_h = run_scenario(cfg, seed, [is_incident], until_hours=21, bin_minutes=20, issuer="hdfc")

    figure(
        clean_h,
        outage_h,
        is_window,
        ("no incident", "hdfc degraded for 45 minutes"),
        "R0 — issuer incident: a bank degrades, each gateway feels it differently",
        RESULTS / "r0_issuer_outage.png",
        note="beacon is the WORST gateway for hdfc before the outage\nand the BEST one during it — the correct route changes",
    )
    write_csv(RESULTS / "r0_issuer_outage.csv", clean_h, outage_h)
    report("ISSUER INCIDENT (hdfc, 45 min, peak severity 0.60) — hdfc traffic only", clean_h, outage_h, is_window)

    sensitivity = {g.name: g.issuer_sensitivity["hdfc"] for g in cfg.gateways}
    print("\nconfigured hdfc sensitivity:", ", ".join(f"{k} {v}" for k, v in sensitivity.items()))
    print(f"\nwritten to {RESULTS}/")


if __name__ == "__main__":
    main()
