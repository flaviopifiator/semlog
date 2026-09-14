"""Report formatting (task 5.4/5.5): turns a measured `timing.summarize`
result into the exact Markdown row shape BENCHMARKS.md's benchmark tables
use. Pure formatting, no timing/measurement logic of its own -- this
module never runs a scenario or reads a clock.
"""

from __future__ import annotations


def format_ns(value_ns: float) -> str:
    """Render a nanosecond figure in microseconds (a single JSON-logging
    call costs low single-digit-to-double-digit microseconds, so µs is
    the natural unit for this table; this scale is not a design choice
    about the library, only about legible reporting)."""
    return f"{value_ns / 1_000:.2f} µs"


def format_throughput_row(scenario, subject, summary, memory_bytes=None):
    """One Markdown table row: scenario, subject, median, spread
    (p90-p10), and memory per record when measured (`n/a` otherwise,
    since not every scenario measures memory)."""
    spread_ns = summary["p90_ns"] - summary["p10_ns"]
    memory_cell = f"{memory_bytes:.0f} B" if memory_bytes is not None else "n/a"
    return (
        f"| {scenario} | {subject} | {format_ns(summary['median_ns'])} "
        f"| {format_ns(spread_ns)} | {memory_cell} |"
    )


def format_stage_rows(breakdown):
    """One Markdown row per cumulative stage, in order: stage, cumulative
    median, spread (p90-p10), and the median cost that stage adds on top of
    the previous one (the first stage adds its whole median). A multi-run
    aggregate shows its min-max range instead of the spread."""
    rows = []
    previous_ns = 0
    for stage, summary in breakdown.items():
        median_ns = summary["median_ns"]
        rows.append(
            f"| {stage} | {format_ns(median_ns)} | {_variation_cell(summary)} "
            f"| {format_ns(median_ns - previous_ns)} |"
        )
        previous_ns = median_ns
    return rows


def _variation_cell(summary):
    """A multi-run aggregate's min-max range, else one run's p90-p10 spread."""
    if "min_ns" in summary:
        return format_range_cell(summary)
    return format_ns(summary["p90_ns"] - summary["p10_ns"])


def format_range_cell(aggregated):
    """`min-max µs` across runs, from a `run_benchmark.aggregate_runs` entry."""
    return f"{aggregated['min_ns'] / 1_000:.2f}-{aggregated['max_ns'] / 1_000:.2f} µs"


def format_scaling_rows(scaling, subjects, with_range=False):
    """One Markdown row per attribute count: the count, then each subject's
    median in `subjects` order, followed by its min-max range across runs
    when `with_range` is set (a `run_benchmark.aggregate_runs` result)."""
    rows = []
    for count, row in scaling.items():
        cells = []
        for subject in subjects:
            summary = row[subject]
            cell = format_ns(summary["median_ns"])
            if with_range:
                low, high = summary["min_ns"] / 1_000, summary["max_ns"] / 1_000
                cell = f"{cell} ({low:.2f}-{high:.2f})"
            cells.append(cell)
        rows.append(f"| {count} | " + " | ".join(cells) + " |")
    return rows


def format_scaling_growth_row(scaling, subjects):
    """One Markdown row: how many times each subject's median grows from the
    smallest to the largest attribute count (the first and last entries of
    `scaling`). A ratio of two medians from the same benchmark invocation,
    so it describes the shape of the curve rather than one machine's
    absolute speed."""
    counts = list(scaling)
    first, last = scaling[counts[0]], scaling[counts[-1]]
    cells = [
        f"×{last[subject]['median_ns'] / first[subject]['median_ns']:.2f}"
        for subject in subjects
    ]
    return f"| {counts[0]} → {counts[-1]} | " + " | ".join(cells) + " |"


def format_range_row(scenario, subject, aggregated, memory_bytes=None):
    """One Markdown row for a multi-run aggregate: scenario, subject, median
    of the per-run medians, min-max range across runs, and memory per record
    when measured (`n/a` otherwise)."""
    memory_cell = f"{memory_bytes:.0f} B" if memory_bytes is not None else "n/a"
    return (
        f"| {scenario} | {subject} | {format_ns(aggregated['median_ns'])} "
        f"| {format_range_cell(aggregated)} | {memory_cell} |"
    )
