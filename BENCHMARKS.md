# semlog benchmarks

This document holds the methodology of semlog's benchmark harness and how to run it. Benchmark results are not published yet: they are being validated, and neither this document nor either README publishes a measured figure until the maintainer validates them (STANDARDS.md CP-005).

The harness lives in [`benchmark/`](benchmark/), outside the published package: it does not count toward the package source budget (STANDARDS.md CP-017) and is not shipped in the wheel. It uses only standard-library measurement tools (`time.perf_counter_ns`, `tracemalloc`, `statistics`, and `cProfile` for a diagnostic mode only), with no third-party benchmark package such as `pyperf` or `pytest-benchmark` (CP-012). It compares semlog with the standard library's `logging` plus a hand-written JSON formatter, with `loguru`, and with `structlog`.

## Methodology

### Subjects

- **semlog**: the real pipeline (formatter, queue handler, bounded queue and writer thread), built from the same production classes `configure()` wires (`benchmark/subjects.py`), pointed at the benchmark's sink instead of `stdout`.
- **stdlib baseline**: `logging` with a hand-written JSON formatter on a `logging.StreamHandler`.
- **loguru**: synchronous writes (`enqueue=False`, its default).
- **structlog**: its default `PrintLoggerFactory` in the default-configuration scenario, and on top of `logging` in every other scenario.

Every subject writes to a file opened on `os.devnull`, so timing differences reflect the subject, not the sink.

### Scenarios

1. **Field scaling** (`benchmark/field_scaling.py`). The same-fields comparison below, repeated with 0, 25, 50, 75 and 100 call-site attributes. At every count, all four subjects emit the same envelope with the same attribute keys and values. The attributes mapping is built once per count, outside the timed loop, and each subject receives it through its own call-site API: `extra=` for semlog and the stdlib baseline, keyword arguments for structlog, `bind()` for loguru. Values cycle through `str`, `int`, `float` and `bool`; no key matches semlog's redaction list, and the largest count stays below semlog's default attribute limit of 128, so no subject drops or rewrites a value. The counts are evenly spaced, so a chart that places its points at equal intervals keeps the real shape of the curve.
2. **Default configuration** (`benchmark/subjects.py`): every subject receives the same call (an event name and three attributes: `user_id`, `request_id`, `duration_ms`) and writes one JSON line in its library's natural shape. structlog uses `PrintLoggerFactory`, its default path, which writes directly without going through `logging`.
3. **Same fields** (`benchmark/like_for_like.py`): all four subjects emit the same 17 fields of semlog's envelope, in the same order: `timestamp`, `severity_text`, `severity_number`, `event_name`, `body`, `otel.scope.name`, the three call attributes and the 8 identity fields, which every subject treats as fixed configuration data. structlog runs on top of `logging` with the integration its own documentation recommends for rendering inside `logging`: `structlog.stdlib.LoggerFactory`, `structlog.stdlib.BoundLogger` and `ProcessorFormatter.wrap_for_formatter`, plus a `structlog.stdlib.ProcessorFormatter` on a `logging.StreamHandler`. loguru uses its documented custom-serialization recipe (a patcher that stores the serialized record in `extra`). The stdlib baseline uses a hand-written `logging` formatter with the same fields.
4. **semlog stage breakdown** (`benchmark/stages.py`): the same three-attribute call measured in three cumulative stages: (1) `logging` dispatch and `LogRecord` creation, ending in a handler that does nothing; (2) the same plus semlog's real `Formatter.format`, discarding the line; (3) the full pipeline (queue handler, bounded queue and writer thread). The difference between two consecutive stages is the cost the later stage adds. Stage 1 also includes the cost of the measurement loop and of building the call's `extra`, which every subject pays.
5. **Memory per record**, for the default-configuration subjects: the net growth of traced memory (`tracemalloc.get_traced_memory()`, read before and after the calls), not the total volume allocated. At this scale most allocations are temporary and already freed before the second reading, so values close to zero are that normal behavior, not a measurement error.
6. **Concurrent contention** (CP-007): 8 threads making 100 calls each against one semlog pipeline whose writer is wired to a deliberately slow sink (2 ms per write), connected only to the writer thread, never to the call site.

Timed scenarios make 5000 measured calls after 200 warm-up calls, per subject and, for field scaling, per attribute count. The memory scenario makes 3000 calls after 200 warm-up calls. Scenarios not implemented (calls with an active exception; 1, 4 and 16 threads; `asyncio` tasks; blocking and dropping behavior of all four subjects under a slow sink) are not measured, and no result is ever reported for them: only what the harness measures, never an extrapolation.

### Fairness, verified before timing

- **Default configuration**: the harness compares the normalized dynamic content of every subject's record, field by field.
- **Same fields and field scaling**: the harness requires the same field names in the same order, and the same values in every field except `timestamp`, which must be a well-formed UTC timestamp. Field scaling repeats this check at every attribute count.

The harness runs each check before timing its scenario and reports every discrepancy it finds.

### Differences that are not equalized

- **Transport.** semlog's writer is asynchronous (a queue plus a writer thread, by design: LP-002, LP-009), so its measured cost is rendering and enqueueing, never the write itself; its writer thread still shares the GIL with the timed call. `logging.StreamHandler` (also used by structlog on top of `logging`), loguru with `enqueue=False` and structlog's `PrintLogger` write synchronously inside the call, which is each library's default. `logging.StreamHandler` also flushes after every record; loguru and structlog's `PrintLogger` use Python's normal buffered text I/O.
- **Record content in the default configuration.** semlog renders its full OpenTelemetry-style envelope (severity, identity, `telemetry.sdk.*`) on every record: that is its real contract (LRC-001 to LRC-013), not a benchmark setting. The same-fields and field-scaling scenarios remove this content difference, not the transport difference.
- **Per-attribute work.** Before JSON encoding, every attribute goes through semlog's namespace flattening, its always-on redaction check (LP-005) and its attribute-limit check (LRC-010). semlog does all three in one pass over the call-site attributes, with the flattened name and the redaction check of each key kept in a bounded memo; only a value that is not a plain JSON scalar (a container, or a type such as `datetime` that has to be converted) goes through the recursive redaction walk and the conversion. The other subjects in these scenarios do none of that work, and semlog does not switch it off for the benchmark.
- **`logging` dispatch.** Default structlog (`PrintLoggerFactory`) skips `logging.Logger` dispatch and `LogRecord` creation entirely. semlog is built on `logging` by design, so every `logging` call in the process, including those from third-party libraries, goes through the same pipeline, and it pays that cost on every call. The same-fields and field-scaling scenarios put structlog on top of `logging` as well.

None of these differences is forced to be equal: doing so would measure a configuration that no real user of those libraries runs.

### Repetitions and CPU pinning

`--runs N` repeats everything in N fresh processes. The harness then reports, for every figure, the median of the per-run medians and the range as the minimum and maximum across those runs; the median is the reference figure, because a single process can show an isolated outlier. `--cpus` pins the run (`os.sched_setaffinity`, inherited by every fresh process) to fixed CPUs, preferably distinct physical cores of the same type, so the measurement never moves between core types; the effective affinity is part of the environment disclosure.

### Environment disclosure

Every run of the harness reports the environment it ran in, read live from the running machine by `benchmark/environment.py`: CPU model and count, CPU affinity, operating system, interpreter version and implementation, GIL status, `time.perf_counter` resolution, and the installed `semlog`, `loguru` and `structlog` versions. Any result, once validated and published, is accompanied by that disclosure and by the date of the run. It is the actual result of that specific run, not a performance guarantee: it can change on another machine, with another Python version or under another operating-system load.

### Profile mode

`--profile N` prints the top N `cProfile` entries for semlog's `Formatter.format`. It is a diagnostic aid only: `cProfile` adds its own per-call overhead, so no reported figure comes from that mode.

### How CP-007 is proven

CP-007 (no collapse under contention) is not proven by an isolated throughput figure but by a deterministic, load-tolerant assertion in `tests/test_benchmark_contention.py`: 800 concurrent calls against a writer that sleeps 2 ms per write must finish in less than a tenth of the time they would take if the call site waited for that write (800 × 2 ms = 1.6 s). A second assertion in the same file compares 8 threads with 1 thread for the same total number of calls, in the same run: a lock held during I/O would make 8 threads take many times longer than 1, and they must stay within a bounded factor (3x). The memory bound of the internal queue under sustained load (CP-006) is proven separately, and deterministically, in `tests/test_transport.py::QueueHandlerOverflowTests`.

## Results

Results are not published yet. They are being validated, and they will be published in this document only after the maintainer validates them.

## Running the harness

Run from the repository root, with `loguru` and `structlog` installed in a throwaway virtual environment outside the repository, never in the repository's environment or globally (the published wheel declares zero runtime dependencies either way, CP-003):

```bash
uv venv /tmp/semlog-bench
uv pip install --python /tmp/semlog-bench/bin/python loguru structlog
/tmp/semlog-bench/bin/python -m benchmark.run_benchmark --cpus 0,2 --runs 5
```

Without `loguru` or `structlog`, those subjects are skipped automatically in every scenario and the rest of the harness still runs. `--cpus` requires Linux, `--json` prints machine-readable output, and `--profile N` runs the diagnostic profile mode.
