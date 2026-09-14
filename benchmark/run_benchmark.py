"""Benchmark runner (task 5.4): orchestrates the fairness checks and every
scenario across every subject available in the running interpreter, then
prints the environment disclosure and Markdown results tables.

Two comparison views are always published side by side:
- the default-configuration scenario (`subjects.py`): each library with its
  own natural output and defaults;
- the like-for-like scenario (`like_for_like.py`): every subject emits
  semlog's exact 17-field envelope, and structlog runs on top of stdlib
  logging.
Plus the field-scaling scenario (`field_scaling.py`: the like-for-like
subjects at 3 to 100 call-site attributes), semlog's per-call stage
breakdown (`stages.py`) and the CP-007 contention scenario.

Usable two ways:
- Directly with the repository's own interpreter (no install needed;
  mirrors `tests/__init__.py`'s own sys.path trick), for the semlog +
  stdlib-baseline comparison and the CP-007 contention proof.
- With `loguru`/`structlog` additionally installed (a throwaway virtual
  environment, never the repository's own environment -- CP-008/CP-013),
  for the full four-subject comparison.

Run with: ``python -m benchmark.run_benchmark`` (one run), ``--runs 5``
(five fresh processes, reporting the median and min-max range across
them), ``--json`` (machine-readable output), ``--cpus 0,2`` (pin the run to
fixed CPUs, disclosed as ``cpu_affinity``), or ``--profile N`` (the top N
`cProfile` entries for semlog's formatter; diagnostic only).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from . import (
    environment,
    fairness,
    field_scaling,
    like_for_like,
    profiling,
    report,
    scenarios,
    sinks,
    stages,
    subjects,
    timing,
)

_THIRD_PARTY_SUBJECTS = frozenset({"loguru", "structlog"})
_TIMED_VIEWS = ("steady_state", "like_for_like", "stages")
_FAIRNESS_KEYS = (
    "fairness_problems",
    "like_for_like_fairness_problems",
    "field_scaling_fairness_problems",
)


def available_subjects():
    """Return `{name: factory}` from `subjects.SUBJECT_FACTORIES`, skipping
    a third-party comparison subject whose package is not importable in
    this interpreter (semlog and the stdlib baseline are always
    available: neither has an external dependency)."""
    result = {}
    for name, factory in subjects.SUBJECT_FACTORIES.items():
        if name in _THIRD_PARTY_SUBJECTS and importlib.util.find_spec(name) is None:
            continue
        result[name] = factory
    return result


def run_fairness_check(subject_factories, check=fairness.check_fairness):
    import io

    call = {"user_id": "u-fair", "request_id": "r-fair", "duration_ms": 4.0}
    raw = {}
    for name, factory in subject_factories.items():
        buf = io.BytesIO()
        emit, shutdown = factory(buf)
        try:
            emit(**call)
        finally:
            shutdown()
        line = buf.getvalue().decode("utf-8").splitlines()[0]
        raw[name] = json.loads(line)
    return check(raw)


def run_steady_state(subject_factories, n=5000, warmup=200):
    results = {}
    for name, factory in subject_factories.items():
        with sinks.devnull_sink() as sink:
            emit, shutdown = factory(sink)
            try:
                samples = scenarios.steady_state_throughput(emit, n=n, warmup=warmup)
            finally:
                shutdown()
        results[name] = timing.summarize(samples)
    return results


def run_memory(subject_factories, n=3000, warmup=200):
    results = {}
    for name, factory in subject_factories.items():
        results[name] = scenarios.memory_per_record(
            factory, sinks.devnull_sink, n=n, warmup=warmup
        )
    return results


def run_contention():
    return scenarios.contention_scenario(
        threads=8, calls_per_thread=100, slow_write_delay_s=0.002
    )


def collect():
    """Run every check and scenario once, in this process."""
    available = available_subjects()
    like = like_for_like.available_factories()
    builders = like_for_like.available_builders()
    return {
        "environment": environment.disclose(),
        "fairness_problems": run_fairness_check(available),
        "steady_state": run_steady_state(available),
        "memory": run_memory(available),
        "like_for_like_fairness_problems": run_fairness_check(
            like, check=like_for_like.check_fairness
        ),
        "like_for_like": run_steady_state(like),
        "field_scaling_fairness_problems": field_scaling.check_fairness(builders),
        "field_scaling": field_scaling.run_field_scaling(builders),
        "stages": stages.stage_breakdown(),
        "contention": run_contention(),
    }


def _range(values):
    return {
        "median_ns": statistics.median(values),
        "min_ns": min(values),
        "max_ns": max(values),
    }


def aggregate_runs(runs):
    """Fold several `collect()` results (each from its own fresh process)
    into the median of per-run medians plus the min-max range across runs,
    per view and subject/stage (and per attribute count for the field-scaling
    scenario); memory and contention report the median."""
    result = {"runs": len(runs)}
    for view in _TIMED_VIEWS:
        result[view] = {
            name: _range([run[view][name]["median_ns"] for run in runs])
            for name in runs[0][view]
        }
    result["field_scaling"] = {
        count: {
            name: _range(
                [run["field_scaling"][count][name]["median_ns"] for run in runs]
            )
            for name in row
        }
        for count, row in runs[0]["field_scaling"].items()
    }
    result["memory"] = {
        name: statistics.median(run["memory"][name] for run in runs)
        for name in runs[0]["memory"]
    }
    result["contention_ns"] = statistics.median(
        run["contention"]["concurrent_ns"] for run in runs
    )
    return result


def _positive_int(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {text}")
    return value


def _cpu_set(text):
    try:
        cpus = {int(part) for part in text.split(",")}
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected CPU numbers like 0,2, got {text}"
        ) from None
    return cpus


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="python -m benchmark.run_benchmark")
    parser.add_argument(
        "--profile",
        type=_positive_int,
        metavar="N",
        help="print the top N cProfile entries for semlog's formatter and exit",
    )
    parser.add_argument(
        "--runs",
        type=_positive_int,
        default=1,
        metavar="N",
        help="repeat everything in N fresh processes; report median and range",
    )
    parser.add_argument(
        "--json", action="store_true", help="print JSON instead of Markdown"
    )
    parser.add_argument(
        "--cpus",
        type=_cpu_set,
        metavar="0,2",
        help="pin this process (and every fresh run it spawns) to these CPUs",
    )
    return parser.parse_args(argv)


def _run_fresh_process():
    completed = subprocess.run(
        [sys.executable, "-m", "benchmark.run_benchmark", "--json"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _print_problems(title, problems):
    print(f"\n# {title}")
    for problem in problems:
        print(f"- DISCREPANCY: {problem}")
    if not problems:
        print("- No discrepancies across compared subjects.")


def _print_single_run(results):
    _print_problems(
        "Fairness check (default configuration)", results["fairness_problems"]
    )
    print("\n# Steady-state throughput, default configuration (1 thread, devnull)")
    print("| Escenario | Sujeto | Mediana | Dispersión (p90-p10) | Memoria/registro |")
    print("|---|---|---|---|---|")
    for name, summary in results["steady_state"].items():
        print(
            report.format_throughput_row(
                "default", name, summary, results["memory"][name]
            )
        )
    _print_problems(
        "Fairness check (like-for-like)", results["like_for_like_fairness_problems"]
    )
    print("\n# Steady-state throughput, like-for-like field set (1 thread, devnull)")
    print("| Escenario | Sujeto | Mediana | Dispersión (p90-p10) | Memoria/registro |")
    print("|---|---|---|---|---|")
    for name, summary in results["like_for_like"].items():
        print(report.format_throughput_row("like-for-like", name, summary))
    _print_problems(
        "Fairness check (field scaling)", results["field_scaling_fairness_problems"]
    )
    _print_scaling(results["field_scaling"], "median, 1 run", with_range=False)
    print("\n# Concurrent contention (CP-007)")
    for key, value in results["contention"].items():
        print(f"- {key}: {value}")


def _print_aggregate(aggregated):
    for view, label in (
        ("steady_state", "default"),
        ("like_for_like", "like-for-like"),
    ):
        print(f"\n# Steady-state throughput, {label} ({aggregated['runs']} runs)")
        print(
            "| Escenario | Sujeto | Mediana | Rango (min-max) | Memoria neta/registro |"
        )
        print("|---|---|---|---|---|")
        for name, summary in aggregated[view].items():
            memory = aggregated["memory"].get(name) if view == "steady_state" else None
            print(report.format_range_row(label, name, summary, memory))
    _print_scaling(
        aggregated["field_scaling"],
        f"median (min-max) across {aggregated['runs']} runs",
        with_range=True,
    )
    print(f"\n# Concurrent contention (CP-007), median of {aggregated['runs']} runs")
    print(f"- concurrent_ns: {aggregated['contention_ns']}")


def _print_scaling(scaling, title, *, with_range):
    subjects = list(next(iter(scaling.values())))
    print(f"\n# Field scaling, µs per record ({title})")
    print("| Attributes | " + " | ".join(subjects) + " |")
    print("|---" * (len(subjects) + 1) + "|")
    for row in report.format_scaling_rows(scaling, subjects, with_range=with_range):
        print(row)
    print(report.format_scaling_growth_row(scaling, subjects))


def _print_stages(breakdown, title):
    print(f"\n# Stage breakdown, semlog per call ({title})")
    print("| Etapa | Mediana acumulada | Variación | Costo que agrega la etapa |")
    print("|---|---|---|---|")
    for row in report.format_stage_rows(breakdown):
        print(row)


def main(argv=None):
    args = parse_args(argv)
    if args.cpus is not None:
        os.sched_setaffinity(0, args.cpus)  # Linux; inherited by fresh runs
    if args.profile is not None:
        print(profiling.profile_formatter(top=args.profile))
        return None
    if args.runs == 1:
        results = collect()
        if args.json:
            print(json.dumps(results))
            return results
    else:
        runs = [_run_fresh_process() for _ in range(args.runs)]
        results = aggregate_runs(runs)
        results["environment"] = runs[0]["environment"]
        for key in _FAIRNESS_KEYS:
            results[key] = sorted({p for run in runs for p in run[key]})
        if args.json:
            print(json.dumps(results))
            return results

    print("# Environment disclosure")
    for key, value in results["environment"].items():
        print(f"- {key}: {value}")
    if args.runs == 1:
        _print_single_run(results)
        _print_stages(results["stages"], "p90-p10 spread, 1 run")
    else:
        _print_problems(
            "Fairness check (default configuration)", results["fairness_problems"]
        )
        _print_problems(
            "Fairness check (like-for-like)", results["like_for_like_fairness_problems"]
        )
        _print_problems(
            "Fairness check (field scaling)", results["field_scaling_fairness_problems"]
        )
        _print_aggregate(results)
        _print_stages(results["stages"], f"min-max range, {args.runs} runs")
    return results


if __name__ == "__main__":
    main()
