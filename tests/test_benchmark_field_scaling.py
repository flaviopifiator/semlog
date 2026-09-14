"""Field-scaling scenario tests: the per-record cost of every like-for-like
subject as the number of call-site attributes grows. Every subject emits
the same envelope for the same attributes at every field count, verified
field by field before any timing. `loguru`/`structlog` variants skip
gracefully when those packages are not installed in the running
interpreter.
"""

from __future__ import annotations

import importlib.util
import io
import json
import unittest

from benchmark import field_scaling, like_for_like, report, run_benchmark

_HAS_LOGURU = importlib.util.find_spec("loguru") is not None
_HAS_STRUCTLOG = importlib.util.find_spec("structlog") is not None


def _emit_record(build, count):
    keys = field_scaling.attribute_keys(count)
    buffer = io.BytesIO()
    emit, shutdown = build(buffer, keys)
    try:
        emit(field_scaling.attributes(count))
    finally:
        shutdown()
    lines = [line for line in buffer.getvalue().decode("utf-8").splitlines() if line]
    if len(lines) != 1:
        raise AssertionError(f"expected one JSON line, got {lines!r}")
    return json.loads(lines[0])


class AttributeGenerationTests(unittest.TestCase):
    def test_field_counts_are_evenly_spaced_from_zero_to_one_hundred(self):
        # Evenly spaced, so a chart that places points at equal intervals
        # (Mermaid's xychart) keeps the curve's real shape.
        self.assertEqual((0, 25, 50, 75, 100), field_scaling.FIELD_COUNTS)

    def test_attribute_keys_are_distinct_dotted_and_ordered(self):
        keys = field_scaling.attribute_keys(25)
        self.assertEqual(25, len(keys))
        self.assertEqual(25, len(set(keys)))
        self.assertEqual("app.field_000", keys[0])
        self.assertEqual("app.field_024", keys[-1])
        self.assertEqual(keys[:10], field_scaling.attribute_keys(10))

    def test_zero_attributes_is_an_empty_call_and_a_negative_count_is_rejected(self):
        self.assertEqual((), field_scaling.attribute_keys(0))
        self.assertEqual({}, field_scaling.attributes(0))
        with self.assertRaises(ValueError):
            field_scaling.attribute_keys(-1)

    def test_attributes_are_deterministic_json_values_of_mixed_types(self):
        values = field_scaling.attributes(10)
        self.assertEqual(field_scaling.attribute_keys(10), tuple(values))
        self.assertEqual(values, field_scaling.attributes(10))
        self.assertEqual(values, json.loads(json.dumps(values)))
        self.assertEqual({str, int, float, bool}, {type(v) for v in values.values()})


class FieldScalingSubjectTests(unittest.TestCase):
    """Proves: CP-005"""

    def _assert_same_envelope(self, build, count=25):
        record = _emit_record(build, count)
        keys = field_scaling.attribute_keys(count)
        self.assertEqual(list(like_for_like.envelope_fields(keys)), list(record))
        for key, value in field_scaling.attributes(count).items():
            self.assertEqual(value, record[key], key)
        self.assertEqual("bench.request.completed", record["event_name"])
        self.assertEqual("bench-svc", record["service.name"])

    def test_envelope_fields_for_the_call_attributes_are_the_like_for_like_set(self):
        self.assertEqual(
            like_for_like.FIELDS,
            like_for_like.envelope_fields(like_for_like.CALL_ATTRIBUTES),
        )

    def test_semlog_builder(self):
        self._assert_same_envelope(like_for_like.build_semlog)

    def test_every_available_builder_emits_the_bare_envelope_with_no_attributes(self):
        for name, build in like_for_like.available_builders().items():
            with self.subTest(subject=name):
                self._assert_same_envelope(build, count=0)

    def test_stdlib_baseline_builder(self):
        self._assert_same_envelope(like_for_like.build_stdlib_baseline)

    @unittest.skipUnless(_HAS_LOGURU, "loguru not installed in this interpreter")
    def test_loguru_builder(self):
        self._assert_same_envelope(like_for_like.build_loguru)

    @unittest.skipUnless(_HAS_STRUCTLOG, "structlog not installed in this interpreter")
    def test_structlog_on_stdlib_builder(self):
        self._assert_same_envelope(like_for_like.build_structlog_stdlib)

    def test_semlog_keeps_every_attribute_at_the_largest_field_count(self):
        count = max(field_scaling.FIELD_COUNTS)
        record = _emit_record(like_for_like.build_semlog, count)
        self.assertNotIn("app.dropped_attributes_count", record)
        self.assertEqual(count, sum(1 for key in record if key.startswith("app.")))

    def test_available_builders_match_the_interpreter(self):
        available = like_for_like.available_builders()
        self.assertIn("semlog", available)
        self.assertIn("stdlib_baseline", available)
        self.assertEqual(_HAS_LOGURU, "loguru" in available)
        self.assertEqual(_HAS_STRUCTLOG, "structlog_stdlib" in available)


class FieldScalingFairnessTests(unittest.TestCase):
    """Proves: CP-005"""

    def test_every_available_builder_is_fair_at_every_field_count(self):
        problems = field_scaling.check_fairness(like_for_like.available_builders())
        self.assertEqual([], problems)

    def test_a_builder_that_alters_an_attribute_is_reported_with_its_count(self):
        def altering_builder(sink, attribute_keys):
            emit, shutdown = like_for_like.build_stdlib_baseline(sink, attribute_keys)

            def emit_altered(attributes):
                emit(dict(attributes, **{attribute_keys[-1]: "changed"}))

            return emit_altered, shutdown

        problems = field_scaling.check_fairness(
            {"semlog": like_for_like.build_semlog, "altering": altering_builder},
            counts=(10,),
        )
        self.assertTrue(problems)
        self.assertTrue(all(p.startswith("10 attributes: ") for p in problems))
        self.assertTrue(any("app.field_009" in p for p in problems), problems)


class FieldScalingMeasurementTests(unittest.TestCase):
    def test_one_summary_per_field_count_and_subject(self):
        builders = {
            name: like_for_like.BUILDERS[name] for name in ("semlog", "stdlib_baseline")
        }
        results = field_scaling.run_field_scaling(
            builders, counts=(3, 10), n=20, warmup=2
        )
        self.assertEqual(["3", "10"], list(results))
        for count, row in results.items():
            self.assertEqual(["semlog", "stdlib_baseline"], list(row), count)
            for name, summary in row.items():
                with self.subTest(count=count, subject=name):
                    self.assertEqual(20, summary["n"])
                    self.assertGreater(summary["median_ns"], 0)

    def test_measure_times_one_call_per_sample_after_warmup(self):
        calls = []
        samples = field_scaling.measure(calls.append, {"app.x": 1}, n=7, warmup=3)
        self.assertEqual(7, len(samples))
        self.assertEqual(10, len(calls))
        self.assertTrue(all(isinstance(s, int) and s >= 0 for s in samples))


class FieldScalingReportTests(unittest.TestCase):
    def test_rows_show_one_median_per_subject_per_field_count(self):
        scaling = {
            "3": {"semlog": {"median_ns": 10_000}, "loguru": {"median_ns": 13_500}},
            "10": {"semlog": {"median_ns": 14_250}, "loguru": {"median_ns": 20_000}},
        }
        self.assertEqual(
            ["| 3 | 10.00 µs | 13.50 µs |", "| 10 | 14.25 µs | 20.00 µs |"],
            report.format_scaling_rows(scaling, ("semlog", "loguru")),
        )

    def test_growth_row_divides_the_largest_count_by_the_smallest(self):
        scaling = {
            "3": {"semlog": {"median_ns": 10_000}, "loguru": {"median_ns": 12_000}},
            "25": {"semlog": {"median_ns": 20_000}, "loguru": {"median_ns": 18_000}},
            "100": {"semlog": {"median_ns": 64_000}, "loguru": {"median_ns": 30_000}},
        }
        self.assertEqual(
            "| 3 → 100 | ×6.40 | ×2.50 |",
            report.format_scaling_growth_row(scaling, ("semlog", "loguru")),
        )

    def test_rows_show_the_range_when_the_summary_is_a_multi_run_aggregate(self):
        scaling = {
            "3": {"semlog": {"median_ns": 10_000, "min_ns": 9_500, "max_ns": 11_000}}
        }
        self.assertEqual(
            ["| 3 | 10.00 µs (9.50-11.00) |"],
            report.format_scaling_rows(scaling, ("semlog",), with_range=True),
        )


class FieldScalingRunnerTests(unittest.TestCase):
    def test_aggregate_runs_folds_field_scaling_per_count_and_subject(self):
        def run(semlog_ns, baseline_ns):
            return {
                "steady_state": {"semlog": {"median_ns": 1}},
                "like_for_like": {"semlog": {"median_ns": 1}},
                "stages": {"format": {"median_ns": 1}},
                "memory": {"semlog": 1.0},
                "contention": {"concurrent_ns": 1},
                "field_scaling": {
                    "3": {
                        "semlog": {"median_ns": semlog_ns},
                        "stdlib_baseline": {"median_ns": baseline_ns},
                    }
                },
            }

        result = run_benchmark.aggregate_runs(
            [run(10_000, 8_000), run(12_000, 9_000), run(11_000, 7_000)]
        )
        self.assertEqual(
            {"median_ns": 11_000, "min_ns": 10_000, "max_ns": 12_000},
            result["field_scaling"]["3"]["semlog"],
        )
        self.assertEqual(
            {"median_ns": 8_000, "min_ns": 7_000, "max_ns": 9_000},
            result["field_scaling"]["3"]["stdlib_baseline"],
        )


if __name__ == "__main__":
    unittest.main()
