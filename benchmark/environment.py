"""Environment disclosure (task 5.4; STANDARDS.md CP-005: hardware, OS,
Python build, timer resolution and library versions must be published
alongside any measured number). Stdlib introspection only (`platform`,
`sys`, `os`, `time.get_clock_info`); library versions come from
`importlib.metadata` when installed, else reported as "not installed"
rather than guessed.
"""

from __future__ import annotations

import importlib.metadata
import os
import platform
import sys
import time


def _library_version(distribution_name: str) -> str:
    try:
        return importlib.metadata.version(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def disclose() -> dict:
    """One dict with every field CP-005/the design's section 10 asks a
    benchmark run to disclose. Every field is read live from the running
    interpreter/OS, never hardcoded."""
    clock_info = time.get_clock_info("perf_counter")
    gil_enabled = getattr(sys, "_is_gil_enabled", None)
    return {
        "cpu_model": platform.processor() or platform.uname().processor or "unknown",
        "cpu_count": os.cpu_count(),
        "os": f"{platform.system()} {platform.release()}",
        "python_version": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "gil_status": (
            "enabled" if gil_enabled is None or gil_enabled() else "disabled"
        ),
        "perf_counter_resolution_s": clock_info.resolution,
        "cpu_affinity": (
            sorted(os.sched_getaffinity(0))
            if hasattr(os, "sched_getaffinity")
            else "unknown"
        ),
        "semlog_version": _library_version("semlog"),
        "loguru_version": _library_version("loguru"),
        "structlog_version": _library_version("structlog"),
    }
