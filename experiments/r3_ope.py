"""R3: how wrong is each estimator, and when should you stop believing it?

For each logging policy (LinUCB at a range of exploration rates) we estimate the
value of a fixed target policy five ways, and compare each estimate against the
simulator's exact answer. A real deployment cannot do this — there you can only
compare one estimate against another. Here there is an answer key.

Run:  ./.venv/bin/python experiments/r3_ope.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from _style import INK, INK_MUTED, SERIES, SURFACE, direct_labels, style_axis
from config.default_world import build_default_world
from payroute.features import Featuriser
from payroute.ope import (
    CrossFittedRewardModel,
    NoOverlapError,
    clipped_ips,
    direct_method,
    doubly_robust,
    ips,
    snips,
)
from payroute.policies import BestPerSegment, LinUCB, UniformRandom
from payroute.policies.runner import run_policy
from payroute.sim import Simulator

RESULTS = Path(__file__).resolve().parents[1] / "results"

N = 60_000
EVAL_SEED = 5
TRAIN_SEED = 101
EPSILONS = [0.30, 0.20, 0.10, 0.05, 0.02, 0.01, 0.00]
ESTIMATORS = ["direct_method", "dm_misspecified", "ips", "snips", "clipped_ips", "doubly_robust"]
PALETTE = ["#2a78d6", "#eda100", "#eb6834", "#1baf7a", "#e87ba4", "#4a3aa7"]


class IssuerBlindFeaturiser:
    """Deliberately misspecified: it cannot see the issuer.

    Our simulator's success probability is base[method] + adjust[issuer], which
    is exactly linear in the normal feature set — so the Direct Method's reward
    model is perfectly specified and DM has no bias to exhibit. That flatters it
    in a way no real deployment would enjoy, and it is the first thing a
    sceptical reader should attack. Blinding the model to the issuer removes a
    term the truth genuinely depends on, which is what misspecification actually
    looks like.
    """

    def __init__(self, cfg):
        base = Featuriser.from_config(cfg)
        self._inner = Featuriser(
            base.methods, ["__any__"], base.categories, base.amount_log_mean, base.amount_log_std
        )
        self.dim = self._inner.dim
        self.names = self._inner.names

    def transform(self, context):
        return self._inner.transform({**dict(context), "issuer": "__any__"})


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    cfg = build_default_world()
    featuriser = Featuriser.from_config(cfg)

    # The target policy: fitted once, on its own exploration log, and frozen.
    training = run_policy(
        Simulator(cfg, seed=TRAIN_SEED), UniformRandom(cfg.gateway_names, seed=TRAIN_SEED), 80_000
    )
    target = BestPerSegment.fit(training)

    rows = []
    for epsilon in EPSILONS:
        sim = Simulator(cfg, seed=EVAL_SEED)
        logger = LinUCB(cfg.gateway_names, featuriser, alpha=1.0, epsilon=epsilon, seed=11)
        log = run_policy(sim, logger, N)

        # The answer key: exact, because it integrates over the target's action
        # distribution rather than sampling it.
        replay = sim.fresh()
        txns = list(replay.transactions(len(log)))
        truth = replay.oracle.true_success_value(
            lambda t: target.action_probs({"method": t.method, "issuer": t.issuer}), txns
        )

        model = CrossFittedRewardModel.fit(log, featuriser, cfg.gateway_names, folds=2)
        blind = CrossFittedRewardModel.fit(
            log, IssuerBlindFeaturiser(cfg), cfg.gateway_names, folds=2
        )
        calls = {
            "direct_method": lambda: direct_method(log, target, reward_model=model.predict),
            "dm_misspecified": lambda: direct_method(log, target, reward_model=blind.predict),
            "ips": lambda: ips(log, target),
            "snips": lambda: snips(log, target),
            "clipped_ips": lambda: clipped_ips(log, target, clip=20.0),
            "doubly_robust": lambda: doubly_robust(log, target, reward_model=model.predict),
        }

        print(f"\nlogging policy: linucb(eps={epsilon:.2f})   truth = {truth:.4f}")
        print(f"  {'estimator':<16}{'estimate':>10}{'error':>10}{'ci width':>11}{'ESS':>12}  trust")
        print("  " + "-" * 68)

        for name, call in calls.items():
            try:
                result = call()
            except NoOverlapError as exc:
                print(f"  {name:<16}{'REFUSED':>10}   {str(exc).split('.')[0][:44]}")
                rows.append(
                    {"epsilon": epsilon, "estimator": name, "truth": truth, "estimate": "",
                     "error": "", "ci_width": "", "ess": "", "max_weight": "", "trust": "refused"}
                )
                continue

            error = result.estimate - truth
            print(
                f"  {name:<16}{result.estimate:>10.4f}{error:>+10.4f}"
                f"{result.ci[1] - result.ci[0]:>11.4f}{result.ess:>12,.0f}  {result.trust}"
            )
            rows.append(
                {
                    "epsilon": epsilon,
                    "estimator": name,
                    "truth": truth,
                    "estimate": result.estimate,
                    "error": error,
                    "ci_width": result.ci[1] - result.ci[0],
                    "ess": result.ess,
                    "max_weight": result.max_weight,
                    "trust": result.trust,
                }
            )

    with (RESULTS / "r3_ope.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    # --- the chart -------------------------------------------------------
    # x is the log-level effective sample size, which is a property of the
    # (log, target) pair and therefore the same for every estimator. Plotting
    # each estimator against its OWN ESS, as an earlier version did, made
    # clipped_ips zigzag and put direct_method at a single point.
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(10.5, 8.4), sharex=True)
    fig.patch.set_facecolor(SURFACE)

    for ax, formatter in ((top, lambda v, _: f"{v:.2f}"), (bottom, lambda v, _: f"{v:.2f}")):
        style_axis(ax, y_formatter=formatter)
        ax.set_xscale("log")
        ax.invert_xaxis()

    # epsilon = 0 is excluded from the chart, not hidden: with no overlap the
    # effective sample size is not defined, and direct_method's 43,644 there is
    # an artefact of counting 0/1 weights rather than a measure of evidence.
    # That row is reported in the table and in the README instead.
    usable = [r for r in rows if r["trust"] != "refused" and float(r["epsilon"]) > 0]
    error_ends, width_ends = [], []
    for name, colour in zip(ESTIMATORS, PALETTE):
        points = sorted(
            (float(r["ess"]), abs(float(r["error"])), float(r["ci_width"]))
            for r in usable
            if r["estimator"] == name
        )
        xs = [p[0] for p in points]
        top.plot(xs, [p[1] for p in points], color=colour, linewidth=2.0, marker="o",
                 markersize=4.5, solid_capstyle="round", label=name)
        bottom.plot(xs, [p[2] for p in points], color=colour, linewidth=2.0, marker="o",
                    markersize=4.5, solid_capstyle="round")
        error_ends.append([name, colour, xs[0], points[0][1]])
        width_ends.append([name, colour, xs[0], points[0][2]])

    max_error = max(abs(float(r["error"])) for r in usable)
    max_width = max(float(r["ci_width"]) for r in usable)
    top.set_ylim(-max_error * 0.04, max_error * 1.12)
    bottom.set_ylim(-max_width * 0.04, max_width * 1.12)
    direct_labels(top, error_ends, min_gap=max_error * 0.075, x_offset=0)
    direct_labels(bottom, width_ends, min_gap=max_width * 0.075, x_offset=0)

    lo = min(float(r["ess"]) for r in usable)
    hi = max(float(r["ess"]) for r in usable)
    top.set_xlim(hi * 1.6, lo * 0.22)

    top.set_title("absolute error against the answer key", color=INK, fontsize=10.5, loc="left", pad=8)
    bottom.set_title("width of the reported 95% interval", color=INK, fontsize=10.5, loc="left", pad=8)
    bottom.set_xlabel(
        "effective sample size of the log  —  less exploration  →",
        color=INK_MUTED, fontsize=9.5, labelpad=8,
    )
    fig.suptitle(
        "R3 — what each estimator does as exploration disappears",
        x=0.055, y=0.975, ha="left", color=INK, fontsize=12.5,
    )
    legend = fig.legend(loc="upper left", bbox_to_anchor=(0.052, 0.935), frameon=False,
                        ncol=6, fontsize=8.5, handlelength=1.4, columnspacing=1.0)
    for text in legend.get_texts():
        text.set_color(INK)
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.90))
    fig.savefig(RESULTS / "r3_estimator_error.png", dpi=200, facecolor=SURFACE)
    plt.close(fig)

    print(f"\nwritten to {RESULTS}/")


if __name__ == "__main__":
    main()
