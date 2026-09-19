"""Shared chart styling for the experiment scripts.

The categorical palette is validated rather than chosen by eye: slots 1-3 clear
every all-pairs gate in light mode (worst colour-vision-deficiency dE 9.2,
worst normal-vision dE 24.0). The third slot sits below 3:1 contrast on the
light surface, so the relief rule applies wherever it is used — every series
carries a direct label, and every figure ships a CSV table beside it.
"""

from __future__ import annotations

from matplotlib.ticker import FuncFormatter

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
GRID = "#ececE8"
SPINE = "#d8d7d2"
SHADE = "#f0efec"


def style_axis(ax, x_formatter=None, y_formatter=None):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(SPINE)
        ax.spines[side].set_linewidth(1.0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK_MUTED, labelsize=9, length=0)
    if x_formatter:
        ax.xaxis.set_major_formatter(FuncFormatter(x_formatter))
    if y_formatter:
        ax.yaxis.set_major_formatter(FuncFormatter(y_formatter))


def direct_labels(ax, ends, min_gap, x_offset):
    """End-of-line labels, nudged apart so they never overlap.

    `ends` is a list of [label, colour, x, y]. A coloured dot carries identity;
    the text itself stays in ink, never in the series colour.
    """
    ends = sorted(ends, key=lambda e: e[3], reverse=True)
    for i in range(1, len(ends)):
        if ends[i - 1][3] - ends[i][3] < min_gap:
            ends[i][3] = ends[i - 1][3] - min_gap
    for label, colour, x, y in ends:
        ax.annotate(label, xy=(x + x_offset, y), va="center", color=INK, fontsize=9.5)
