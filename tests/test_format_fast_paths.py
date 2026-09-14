"""Formatter fast-path tests: every hot-path shortcut in `_format.py` must
render byte-for-byte what the plain path renders, including at the edges
where a shortcut could silently diverge (second boundaries and rounding for
the cached timestamp prefix, concurrent formatting, and bounded caches
under a flood of unique inputs).
"""

from __future__ import annotations

import datetime
import decimal
import enum
import gc
import json
import logging
import math
import random
import sys
import threading
import tracemalloc
import unittest
from unittest import mock

from semlog import _format
from semlog._context import Snapshot, pop, push
from semlog._format import Formatter


def _identity():
    return {
        "service.name": "svc",
        "service.namespace": None,
        "service.version": None,
        "service.instance.id": "11111111-1111-1111-1111-111111111111",
        "deployment.environment.name": None,
        "telemetry.sdk.name": "semlog",
        "telemetry.sdk.version": "0.0.0",
        "telemetry.sdk.language": "python",
    }


def _record(msg="app.custom.event", extra=None, created=None):
    logger = logging.getLogger("semlog.tests.fast_paths")
    record = logger.makeRecord(
        logger.name, logging.INFO, __file__, 1, msg, (), None, extra=extra
    )
    if created is not None:
        record.created = created
    return record


def _reference_timestamp(created):
    """The plain, uncached rendering, kept here as the oracle."""
    dt = datetime.datetime.fromtimestamp(created, tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}Z"


class CachedTimestampTests(unittest.TestCase):
    """Proves: LRC-002

    The per-second date/time prefix may be cached, but every rendered
    timestamp must equal `datetime.fromtimestamp`'s own rendering, whatever
    order the records arrive in."""

    _SECOND = 1_700_000_000  # 2023-11-14T22:13:20Z

    def _timestamp(self, formatter, created):
        return json.loads(formatter.format(_record(created=created)))["timestamp"]

    def test_second_boundary_in_both_directions(self):
        formatter = Formatter(identity=_identity())
        x = self._SECOND
        self.assertEqual(
            "2023-11-14T22:13:20.999999Z", self._timestamp(formatter, x + 0.999999)
        )
        self.assertEqual(
            "2023-11-14T22:13:21.000000Z", self._timestamp(formatter, x + 1.0)
        )
        self.assertEqual(
            "2023-11-14T22:13:20.999999Z", self._timestamp(formatter, x + 0.999999)
        )
        self.assertEqual(
            "2023-11-14T22:13:21.000001Z", self._timestamp(formatter, x + 1.000001)
        )

    def test_microsecond_rounding_carries_into_the_next_second(self):
        formatter = Formatter(identity=_identity())
        created = self._SECOND + 0.9999996
        self.assertEqual("2023-11-14T22:13:21.000000Z", _reference_timestamp(created))
        self.assertEqual(
            "2023-11-14T22:13:20.999999Z",
            self._timestamp(formatter, self._SECOND + 0.999999),
        )
        self.assertEqual(
            "2023-11-14T22:13:21.000000Z", self._timestamp(formatter, created)
        )

    def test_matches_the_reference_rendering_for_many_values(self):
        rng = random.Random(20260914)
        edges = (0.0, 5e-7, 1.5e-6, 2.5e-6, 0.4999995, 0.9999994, 0.9999995, 0.9999996)
        values = [self._SECOND + edge for edge in edges]
        values += [rng.uniform(0, 4_102_444_800) for _ in range(2000)]
        values += [rng.randrange(0, 4_102_444_800) for _ in range(50)]  # int seconds
        values += [0, 0.5, -0.5, -1.0000004, -86_400.25]
        formatter = Formatter(identity=_identity())
        for created in values:
            with self.subTest(created=created):
                self.assertEqual(
                    _reference_timestamp(created), self._timestamp(formatter, created)
                )

    def test_concurrent_formatting_never_mixes_a_stale_second(self):
        formatter = Formatter(identity=_identity())
        values = [
            self._SECOND + (i % 5) + ((i * 7919) % 1_000_000) / 1_000_000
            for i in range(4000)
        ]
        threads = 4
        barrier = threading.Barrier(threads)
        mismatches = []

        def worker(offset):
            barrier.wait()
            for created in values[offset::threads]:
                rendered = self._timestamp(formatter, created)
                if rendered != _reference_timestamp(created):
                    mismatches.append((created, rendered))

        previous_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)  # force frequent thread switches
        try:
            workers = [
                threading.Thread(target=worker, args=(i,)) for i in range(threads)
            ]
            for thread in workers:
                thread.start()
            for thread in workers:
                thread.join()
        finally:
            sys.setswitchinterval(previous_interval)
        self.assertEqual([], mismatches)


_HEAD = (
    '{"timestamp":"2023-11-14T22:13:20.000000Z","severity_text":"INFO",'
    '"severity_number":9,"event_name":"app.custom.event","body":null,'
)
_SCOPE = '"otel.scope.name":"semlog.tests.fast_paths",'
_IDENTITY_TAIL = (
    '"service.name":"svc","service.namespace":null,"service.version":null,'
    '"service.instance.id":"11111111-1111-1111-1111-111111111111",'
    '"deployment.environment.name":null,"telemetry.sdk.name":"semlog",'
    '"telemetry.sdk.version":"0.0.0","telemetry.sdk.language":"python"}'
)


def _render(formatter, extra):
    return formatter.format(_record(extra=extra, created=1_700_000_000.0))


class IdentitySpliceTests(unittest.TestCase):
    """Proves: LRC-001

    The identity fields may be pre-rendered once and spliced into each
    line, but the bytes must stay exactly what `envelope.update(identity)`
    followed by one encode produces: identity values win over a colliding
    call-site or context key (which keeps that key's earlier position),
    identity keys otherwise come last in identity order, and the sanitizing
    fallback renders the whole record as before."""

    def test_identity_comes_last_after_attributes_and_limit_counters(self):
        formatter = Formatter(
            identity=_identity(), max_attributes=2, max_attribute_length=3
        )
        line = _render(formatter, {"user_id": 42, "note": "abcdef", "extra": "z"})
        self.assertEqual(
            _HEAD
            + _SCOPE
            + '"app.user_id":42,"app.note":"abc","app.dropped_attributes_count":1,'
            + '"app.truncated_attributes_count":1,'
            + _IDENTITY_TAIL,
            line,
        )

    def test_identity_wins_a_call_site_collision_at_the_colliding_position(self):
        formatter = Formatter(identity=_identity())
        extra = {"service.name": "evil", "zzz": 1, "telemetry.sdk.language": "cobol"}
        self.assertEqual(
            _HEAD
            + _SCOPE
            + '"service.name":"svc","app.zzz":1,"telemetry.sdk.language":"python",'
            + '"service.namespace":null,"service.version":null,'
            + '"service.instance.id":"11111111-1111-1111-1111-111111111111",'
            + '"deployment.environment.name":null,"telemetry.sdk.name":"semlog",'
            + '"telemetry.sdk.version":"0.0.0"}',
            _render(formatter, extra),
        )

    def test_identity_wins_a_bound_context_attribute_collision(self):
        token = push(
            Snapshot(
                trace_id="a" * 32,
                span_id="b" * 16,
                trace_flags="01",
                request_id="req-1",
                attributes={"service.version": "ctx", "app.tenant": "t1"},
                baggage={"k": "v"},
            )
        )
        try:
            line = _render(Formatter(identity=_identity()), {"user_id": 1})
        finally:
            pop(token)
        self.assertEqual(
            _HEAD
            + '"trace_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","span_id":"bbbbbbbbbbbbbbbb",'
            + '"trace_flags":"01","http.request.id":"req-1",'
            + _SCOPE
            + '"app.user_id":1,"service.version":null,"app.tenant":"t1",'
            + '"baggage.k":"v","service.name":"svc","service.namespace":null,'
            + '"service.instance.id":"11111111-1111-1111-1111-111111111111",'
            + '"deployment.environment.name":null,"telemetry.sdk.name":"semlog",'
            + '"telemetry.sdk.version":"0.0.0","telemetry.sdk.language":"python"}',
            line,
        )

    def test_sanitizing_fallback_still_renders_identity_last(self):
        formatter = Formatter(identity=_identity())
        line = _render(formatter, {"score": math.nan, "nested": {True: 1, None: 2}})
        self.assertEqual(
            _HEAD
            + _SCOPE
            + '"app.score":"NaN","app.nested":{"True":1,"None":2},'
            + _IDENTITY_TAIL,
            line,
        )

    def test_identity_only_the_fallback_can_encode_sanitizes_the_whole_record(self):
        formatter = Formatter(identity={"service.name": "svc", "weird": math.inf})
        self.assertEqual(
            _HEAD
            + _SCOPE
            + '"app.nested":{"True":1},"service.name":"svc","weird":"Infinity"}',
            _render(formatter, {"nested": {True: 1}}),
        )

    def test_non_string_identity_key_renders_as_before(self):
        formatter = Formatter(identity={"service.name": "svc", 7: "x"})
        self.assertEqual(
            _HEAD + _SCOPE + '"app.nested":{"null":1},"service.name":"svc","7":"x"}',
            _render(formatter, {"nested": {None: 1}}),
        )

    def test_empty_identity_renders_a_well_formed_record(self):
        formatter = Formatter(identity={})
        self.assertEqual(
            _HEAD + _SCOPE + '"app.user_id":1}', _render(formatter, {"user_id": 1})
        )


def _bounded_caches():
    """Every memoized helper in the formatter module (anything exposing the
    `functools.lru_cache` introspection API)."""
    return [
        obj
        for obj in vars(_format).values()
        if callable(obj) and hasattr(obj, "cache_info")
    ]


class BoundedMemoizationTests(unittest.TestCase):
    """Proves: LP-005

    Attribute keys and event names can be high-cardinality or
    attacker-influenced: any memoization of per-key or per-message work must
    stay bounded in entries AND must not pin arbitrarily large strings."""

    def test_formatter_memoizes_through_size_capped_caches_only(self):
        caches = _bounded_caches()
        self.assertTrue(caches, "expected the hot path to use a bounded memo")
        for cache in caches:
            with self.subTest(cache=cache.__name__):
                self.assertIsNotNone(cache.cache_parameters()["maxsize"])

    def test_a_flood_of_unique_keys_and_event_names_never_exceeds_the_cap(self):
        formatter = Formatter(identity=_identity(), redact_keys=("card_number",))
        caps = {
            cache: cache.cache_parameters()["maxsize"] for cache in _bounded_caches()
        }
        flood = max(caps.values(), default=0) * 3 + 100
        payload = {f"field_{i}": i for i in range(flood)}
        formatter.format(_record(extra={"payload": payload}))
        for i in range(flood):
            formatter.format(_record(msg=f"flood.event_{i}.happened"))
        for cache, cap in caps.items():
            with self.subTest(cache=cache.__name__):
                self.assertLessEqual(cache.cache_info().currsize, cap)

    def test_huge_unique_keys_and_messages_are_not_retained(self):
        formatter = Formatter(identity=_identity())
        gc.collect()
        tracemalloc.start()
        try:
            before = tracemalloc.get_traced_memory()[0]
            for i in range(1500):
                big = f"k{i:06d}" + "x" * 20_000
                formatter.format(_record(msg=f"a.{big}", extra={"payload": {big: i}}))
            gc.collect()
            retained = tracemalloc.get_traced_memory()[0] - before
        finally:
            tracemalloc.stop()
        # 1500 distinct 20 kB strings: caching them would retain megabytes.
        self.assertLess(retained, 2_000_000)


class RedactionFastPathTests(unittest.TestCase):
    """Proves: LP-005

    Memoizing the sensitive-key check and skipping the recursive walk for
    plain scalars must keep redaction's exact semantics."""

    def test_the_memo_never_leaks_one_formatters_redact_keys_into_another(self):
        custom = Formatter(identity=_identity(), redact_keys=("card_number",))
        plain = Formatter(identity=_identity())
        extra = {"card_number": "4111", "nested": {"Card_Number": "4111"}}
        for _ in range(3):  # alternate, so a warm memo is exercised both ways
            redacted = json.loads(custom.format(_record(extra=extra)))
            kept = json.loads(plain.format(_record(extra=extra)))
            self.assertEqual("REDACTED", redacted["app.card_number"])
            self.assertEqual({"Card_Number": "REDACTED"}, redacted["app.nested"])
            self.assertEqual("4111", kept["app.card_number"])
            self.assertEqual({"Card_Number": "4111"}, kept["app.nested"])

    def test_case_insensitive_full_key_or_last_dotted_segment(self):
        formatter = Formatter(identity=_identity(), redact_keys=("Card_Number",))
        extra = {
            "Payment.CARD_NUMBER": "4111",
            "card_number.hint": "last4",
            "user.Password": "p",
            "PASSWORD": "p",
            "passwordless": "no",
        }
        parsed = json.loads(formatter.format(_record(extra=extra)))
        self.assertEqual("REDACTED", parsed["Payment.CARD_NUMBER"])
        self.assertEqual("last4", parsed["card_number.hint"])
        self.assertEqual("REDACTED", parsed["user.Password"])
        self.assertEqual("REDACTED", parsed["app.PASSWORD"])
        self.assertEqual("no", parsed["app.passwordless"])

    def test_keys_longer_than_any_memo_cap_are_still_redacted(self):
        formatter = Formatter(identity=_identity())
        long_key = "segment." * 500 + "Token"
        parsed = json.loads(
            formatter.format(_record(extra={"payload": {long_key: "t", "x" * 4000: 1}}))
        )
        self.assertEqual({long_key: "REDACTED", "x" * 4000: 1}, parsed["app.payload"])

    def test_lists_tuples_and_non_string_keys_are_still_walked(self):
        formatter = Formatter(identity=_identity())
        extra = {
            "items": ({"token": "a", 7: "seven"}, [{"secret": "b"}, "plain", 1.5]),
            "flags": [True, None, {"api_key": ["k"]}],
        }
        parsed = json.loads(formatter.format(_record(extra=extra)))
        self.assertEqual(
            [
                {"token": "REDACTED", "7": "seven"},
                [{"secret": "REDACTED"}, "plain", 1.5],
            ],
            parsed["app.items"],
        )
        self.assertEqual([True, None, {"api_key": "REDACTED"}], parsed["app.flags"])

    def test_recursion_cap_is_unchanged(self):
        def nested(levels):
            value = {"password": "p", "depth": levels}
            for _ in range(levels):
                value = {"child": value}
            return value

        formatter = Formatter(identity=_identity())
        # The attributes map is depth 0, so a map reached through 15 "child"
        # links from `app.payload` sits at depth 16: still walked.
        walked = json.loads(formatter.format(_record(extra={"payload": nested(15)})))
        beyond = json.loads(formatter.format(_record(extra={"payload": nested(16)})))

        def innermost(value):
            while "child" in value:
                value = value["child"]
            return value

        self.assertEqual("REDACTED", innermost(walked["app.payload"])["password"])
        self.assertEqual("p", innermost(beyond["app.payload"])["password"])


class _Mode(enum.Enum):
    UNKNOWN = math.nan


class _Level(enum.IntEnum):
    HIGH = 3


class AttributeLimitsFastPathTests(unittest.TestCase):
    """Proves: LRC-009, LRC-010

    A shortcut may skip the limits walk only when nothing can be dropped,
    truncated or coerced; every other record keeps the full path, so these
    records must render exactly as before."""

    _SHORT_IDENTITY = '"service.name":"svc"}'

    def _render(self, extra, **limits):
        formatter = Formatter(identity={"service.name": "svc"}, **limits)
        return _render(formatter, extra)

    def test_count_exactly_at_the_limit_keeps_everything_without_a_counter(self):
        self.assertEqual(
            _HEAD + _SCOPE + '"app.a":1,"app.b":2,"app.c":3,' + self._SHORT_IDENTITY,
            self._render({"a": 1, "b": 2, "c": 3}, max_attributes=3),
        )

    def test_one_over_the_limit_drops_the_last_and_counts_it(self):
        self.assertEqual(
            _HEAD
            + _SCOPE
            + '"app.a":1,"app.b":2,"app.c":3,"app.dropped_attributes_count":1,'
            + self._SHORT_IDENTITY,
            self._render({"a": 1, "b": 2, "c": 3, "d": 4}, max_attributes=3),
        )

    def test_a_zero_limit_drops_everything(self):
        self.assertEqual(
            _HEAD + _SCOPE + '"app.dropped_attributes_count":1,' + self._SHORT_IDENTITY,
            self._render({"a": 1}, max_attributes=0),
        )

    def test_non_native_values_are_still_coerced_before_encoding(self):
        extra = {
            "when": datetime.datetime(
                2024, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc
            ),
            "amount": decimal.Decimal("1.50"),
            "tags": {"a"},
            "level": _Level.HIGH,
            "raw": b"hi",
            "nested": [datetime.date(2024, 1, 2)],
        }
        self.assertEqual(
            _HEAD
            + _SCOPE
            + '"app.when":"2024-01-02T03:04:05+00:00","app.amount":"1.50","app.tags":["a"],'
            + '"app.level":3,"app.raw":"aGk=","app.nested":["2024-01-02"],'
            + self._SHORT_IDENTITY,
            self._render(extra),
        )

    def test_a_value_coerced_to_nan_still_reaches_the_sanitizing_fallback(self):
        self.assertEqual(
            _HEAD + _SCOPE + '"app.mode":"NaN",' + self._SHORT_IDENTITY,
            self._render({"mode": _Mode.UNKNOWN}),
        )

    def test_any_length_limit_keeps_the_truncation_walk(self):
        self.assertEqual(
            _HEAD + _SCOPE + '"app.a":"x","app.b":["eQ=="],' + self._SHORT_IDENTITY,
            self._render({"a": "x", "b": [b"y"]}, max_attribute_length=10_000),
        )
        self.assertEqual(
            _HEAD
            + _SCOPE
            + '"app.a":"xyz","app.b":["eXl5"],"app.truncated_attributes_count":2,'
            + self._SHORT_IDENTITY,
            self._render({"a": "xyzw", "b": [b"yyyy"]}, max_attribute_length=3),
        )


def _spy(name):
    """Record every call to the `_format` helper `name` while it still runs."""
    return mock.patch.object(_format, name, wraps=getattr(_format, name))


class SinglePassBoundaryTests(unittest.TestCase):
    """Proves: LP-005, LRC-007, LRC-009, LRC-010

    Call-site attributes are flattened, redacted by key and checked for
    JSON scalars in one pass. Only a value that is not a plain JSON scalar,
    or a configured limit, takes the slow path (the recursive redaction walk,
    coercion and truncation). These tests pin the exact point where each
    path starts, observed through the slow-path helpers, and the rendered
    bytes on both sides of that point."""

    _TAIL = '"service.name":"svc"}'

    def _formatter(self, **options):
        return Formatter(identity={"service.name": "svc"}, **options)

    def test_a_scalar_only_record_never_enters_the_slow_path(self):
        extra = {"user_id": 42, "app.note": "n", "ratio": 1.5, "ok": True}
        extra.update({"gone": None, "password": "p", "a.b.Token": "t"})
        with (
            _spy("_redact_value") as walk,
            _spy("_coerce_scalar") as coerce,
            _spy("_truncate") as truncate,
        ):
            line = _render(self._formatter(), extra)
        self.assertEqual(
            _HEAD
            + _SCOPE
            + '"app.user_id":42,"app.note":"n","app.ratio":1.5,"app.ok":true,'
            + '"app.gone":null,"app.password":"REDACTED","a.b.Token":"REDACTED",'
            + self._TAIL,
            line,
        )
        self.assertEqual(
            (0, 0, 0), (walk.call_count, coerce.call_count, truncate.call_count)
        )

    def test_the_slow_path_starts_exactly_at_a_value_that_is_not_a_plain_scalar(self):
        cases = [
            ("a string", '"a string"', False, 0),
            (7, "7", False, 0),
            ([1], "[1]", True, 0),
            ((1,), "[1]", True, 0),
            ({"k": 1}, '{"k":1}', True, 0),
            (b"hi", '"aGk="', False, 0),
            (_Level.HIGH, "3", False, 0),
            (decimal.Decimal("1.50"), '"1.50"', False, 1),
        ]
        formatter = self._formatter()
        for value, rendered, walked, coerced in cases:
            with (
                self.subTest(value=value),
                _spy("_redact_value") as walk,
                _spy("_coerce_scalar") as coerce,
            ):
                line = _render(formatter, {"a": "x", "b": value, "c": 2})
                self.assertEqual(
                    _HEAD
                    + _SCOPE
                    + f'"app.a":"x","app.b":{rendered},"app.c":2,'
                    + self._TAIL,
                    line,
                )
                expected_walk = [mock.call(value, formatter._redact_keys, 1)]
                self.assertEqual(
                    expected_walk if walked else [], walk.call_args_list[:1]
                )
                self.assertEqual(coerced, coerce.call_count)

    def test_a_sensitive_key_nested_below_scalar_siblings_is_walked_and_redacted(self):
        payload = {"id": 1, "password": "p", "items": [{"Token": "t"}, "plain"]}
        formatter = self._formatter()
        with _spy("_redact_value") as walk:
            line = _render(formatter, {"user": "u", "payload": payload, "token": "t"})
        self.assertEqual(
            _HEAD
            + _SCOPE
            + '"app.user":"u","app.payload":{"id":1,"password":"REDACTED",'
            + '"items":[{"Token":"REDACTED"},"plain"]},"app.token":"REDACTED",'
            + self._TAIL,
            line,
        )
        self.assertEqual(
            mock.call(payload, formatter._redact_keys, 1), walk.call_args_list[0]
        )

    def test_a_colliding_key_keeps_its_first_position_and_its_last_value(self):
        with _spy("_redact_value") as walk:
            line = _render(
                self._formatter(), {"x": [{"token": "t"}], "n": 1, "app.x": 2}
            )
        self.assertEqual(_HEAD + _SCOPE + '"app.x":2,"app.n":1,' + self._TAIL, line)
        self.assertEqual(0, walk.call_count)  # the overwritten container is not walked
        line = _render(self._formatter(), {"x": 2, "n": 1, "app.x": [{"token": "t"}]})
        self.assertEqual(
            _HEAD + _SCOPE + '"app.x":[{"token":"REDACTED"}],"app.n":1,' + self._TAIL,
            line,
        )

    def test_truncation_runs_only_once_a_length_limit_is_configured(self):
        extra = {"a": "xyzw", "b": 1}
        for limit, rendered, counter in (
            (None, '"app.a":"xyzw","app.b":1,', ""),
            (4, '"app.a":"xyzw","app.b":1,', ""),
            (3, '"app.a":"xyz","app.b":1,', '"app.truncated_attributes_count":1,'),
        ):
            with self.subTest(limit=limit), _spy("_truncate") as truncate:
                line = _render(self._formatter(max_attribute_length=limit), extra)
                self.assertEqual(_HEAD + _SCOPE + rendered + counter + self._TAIL, line)
                self.assertEqual(0 if limit is None else 2, truncate.call_count)

    def test_the_count_limit_cuts_first_and_never_coerces_a_dropped_value(self):
        extra = {"a": 1, "b": decimal.Decimal(2), "c": decimal.Decimal(3)}
        for limit, rendered, counter, coerced in (
            (3, '"app.a":1,"app.b":"2","app.c":"3",', "", 2),
            (2, '"app.a":1,"app.b":"2",', '"app.dropped_attributes_count":1,', 1),
            (1, '"app.a":1,', '"app.dropped_attributes_count":2,', 0),
        ):
            with self.subTest(limit=limit), _spy("_coerce_scalar") as coerce:
                line = _render(self._formatter(max_attributes=limit), extra)
                self.assertEqual(_HEAD + _SCOPE + rendered + counter + self._TAIL, line)
                self.assertEqual(coerced, coerce.call_count)

    def test_bound_context_and_baggage_get_the_same_redaction_and_coercion(self):
        token = push(
            Snapshot(
                attributes={
                    "app.user": "context never overrides",
                    "tenant": {"Token": "t"},
                    "password": "p",
                    "when": datetime.date(2024, 1, 2),
                },
                baggage={"password": "b", "k": "v", "user": "baggage overrides"},
            )
        )
        try:
            line = _render(self._formatter(baggage_prefix="app."), {"user": 1, "n": 2})
        finally:
            pop(token)
        self.assertEqual(
            _HEAD
            + _SCOPE
            + '"app.user":"baggage overrides","app.n":2,"tenant":{"Token":"REDACTED"},'
            + '"password":"REDACTED","when":"2024-01-02","app.password":"REDACTED",'
            + '"app.k":"v",'
            + self._TAIL,
            line,
        )

    def test_the_key_memo_stays_bounded_and_an_evicted_key_renders_the_same(self):
        memo = _format._key_info
        cap = memo.cache_parameters()["maxsize"]
        formatter = self._formatter(max_attributes=cap * 2)
        probe = {"user": 1, "password": "p", "a.b.Token": "t"}
        before = _render(formatter, probe)
        flood = _render(formatter, {f"flood_{i}": i for i in range(cap + 10)})
        self.assertEqual(cap, memo.cache_info().currsize)
        self.assertIn(f'"app.flood_{cap + 9}":{cap + 9},', flood)
        misses = memo.cache_info().misses
        self.assertEqual(before, _render(formatter, probe))
        self.assertEqual(misses + len(probe), memo.cache_info().misses)

    def test_top_level_keys_longer_than_the_memo_cap_are_never_cached(self):
        limit = _format._MEMO_MAX_LENGTH
        at_cap = "k" * (limit - len(".Token")) + ".Token"
        over_cap = "k" + at_cap
        undotted = "n" * (limit + 1)
        _format._key_info.cache_clear()
        line = _render(self._formatter(), {at_cap: "a", over_cap: "b", undotted: 3})
        self.assertEqual(1, _format._key_info.cache_info().currsize)
        self.assertEqual(
            _HEAD
            + _SCOPE
            + f'"{at_cap}":"REDACTED","{over_cap}":"REDACTED","app.{undotted}":3,'
            + self._TAIL,
            line,
        )


class EventNameClassificationFastPathTests(unittest.TestCase):
    """Proves: LRC-004

    Memoizing the event-name pattern check must keep classification exact:
    only an argument-free string message matching the pattern becomes
    `event_name`, whatever was classified before."""

    def _classified(self, formatter, msg, args=()):
        logger = logging.getLogger("semlog.tests.fast_paths")
        record = logger.makeRecord(
            logger.name, logging.INFO, __file__, 1, msg, args, None
        )
        parsed = json.loads(formatter.format(record))
        return parsed["event_name"], parsed["body"]

    def test_conformant_and_non_conformant_messages_repeatedly(self):
        formatter = Formatter(identity=_identity())
        long_name = "app." + ".".join(["segment"] * 60)
        expectations = [
            (("payment.completed",), ("payment.completed", None)),
            (("Payment.completed",), (None, "Payment.completed")),
            (("single",), (None, "single")),
            (("a.b.",), (None, "a.b.")),
            (("1a.b",), (None, "1a.b")),
            (("a.b\n",), ("a.b\n", None)),  # `$` also matches before a final newline
            ((long_name,), (long_name, None)),
            ((long_name + "X",), (None, long_name + "X")),
            (("app.%s.done", ("x",)), (None, "app.x.done")),
            (("payment.completed", ({"k": 1},)), (None, "payment.completed")),
            ((42,), (None, "42")),
        ]
        for _ in range(2):  # the second pass runs against a warm memo
            for call, expected in expectations:
                with self.subTest(call=call):
                    self.assertEqual(expected, self._classified(formatter, *call))


if __name__ == "__main__":
    unittest.main()
