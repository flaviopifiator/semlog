"""Field-scaling scenario: per-record cost as call-site attributes grow.

The like-for-like view (`like_for_like.py`) fixes the call at three
attributes. This scenario repeats the same comparison at several attribute
counts (`FIELD_COUNTS`), so the published result shows how each subject's
per-record cost grows with the number of fields, not only its cost at one
point. The counts are evenly spaced, from a call with no attributes to one
with 100, so a chart that places its points at equal intervals keeps the
real shape of the curve.

- Every subject is a like-for-like builder, unchanged: semlog's real
  pipeline, the hand-written stdlib JSON baseline, loguru's
  custom-serialization recipe, and structlog on top of stdlib logging.
- At every count, every subject emits the same envelope with the same
  attribute keys and values, verified field by field (`check_fairness`)
  before any timing.
- The attributes mapping is built once per count, outside the timed loop,
  so a sample measures the logging call, not building the caller's dict.
  Each subject receives it through its own call-site API: `extra=` for
  semlog and the stdlib baseline, keyword arguments for structlog, `bind()`
  for loguru.
- Values cycle through `str`, `int`, `float` and `bool`, the common shapes of
  real call-site attributes; key names never match semlog's redaction list,
  and the largest count stays below semlog's default attribute limit (128),
  so no subject drops or rewrites a value.

Stdlib-only timing (`time.perf_counter_ns` through `timing`, CP-012).
"""

from __future__ import annotations

import io
import json

from . import like_for_like, sinks, timing

FIELD_COUNTS = (0, 25, 50, 75, 100)


def attribute_keys(count):
    """`count` distinct dotted attribute keys, in a stable order."""
    if count < 0:
        raise ValueError(f"count must not be negative, got {count}")
    return tuple(f"app.field_{index:03d}" for index in range(count))


def attributes(count):
    """The attributes every subject logs at `count`: deterministic values
    cycling through `str`, `int`, `float` and `bool`."""
    values = {}
    for index, key in enumerate(attribute_keys(count)):
        kind = index % 4
        if kind == 0:
            values[key] = f"value-{index}"
        elif kind == 1:
            values[key] = index
        elif kind == 2:
            values[key] = index + 0.5
        else:
            values[key] = index % 8 == 3
    return values


def _emit_record(build, keys, values):
    buffer = io.BytesIO()
    emit, shutdown = build(buffer, keys)
    try:
        emit(values)
    finally:
        shutdown()
    return json.loads(buffer.getvalue().decode("utf-8").splitlines()[0])


def check_fairness(builders, counts=FIELD_COUNTS):
    """Discrepancies across `builders` at every count (empty when fair), each
    prefixed with the attribute count it was found at."""
    problems = []
    for count in counts:
        keys = attribute_keys(count)
        values = attributes(count)
        raw = {
            name: _emit_record(build, keys, values) for name, build in builders.items()
        }
        fields = like_for_like.envelope_fields(keys)
        problems.extend(
            f"{count} attributes: {problem}"
            for problem in like_for_like.check_fairness(raw, fields)
        )
    return problems


def measure(emit, values, n=5000, warmup=200):
    """`n` nanosecond samples of `emit(values)`, one per call, after `warmup`
    untimed calls."""
    for _ in range(warmup):
        emit(values)
    samples = []
    for _ in range(n):
        start = timing.now_ns()
        emit(values)
        samples.append(timing.elapsed_ns(start))
    return samples


def run_field_scaling(builders, counts=FIELD_COUNTS, n=5000, warmup=200):
    """`{str(count): {subject: timing.summarize(samples)}}`, in `counts`
    order, each subject built fresh on a devnull sink for every count."""
    results = {}
    for count in counts:
        keys = attribute_keys(count)
        values = attributes(count)
        row = {}
        for name, build in builders.items():
            with sinks.devnull_sink() as sink:
                emit, shutdown = build(sink, keys)
                try:
                    samples = measure(emit, values, n=n, warmup=warmup)
                finally:
                    shutdown()
            row[name] = timing.summarize(samples)
        results[str(count)] = row
    return results
