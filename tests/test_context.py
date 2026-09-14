"""Context snapshot primitive tests (design #162 §1.1 "Context scope").

Proves TCP-005: one immutable snapshot bound through a single
``contextvars.ContextVar``, reset to the prior value on exit, isolated
across concurrent async tasks and thread-pool-offloaded synchronous work,
with no leftover context on a worker thread that is reused without its own
context copy.
"""

from __future__ import annotations

import asyncio
import contextvars
import unittest
from concurrent.futures import ThreadPoolExecutor

from semlog._context import Snapshot, current, pop, push


class ContextSnapshotTests(unittest.TestCase):
    """Proves: TCP-005"""

    def test_no_snapshot_bound_by_default(self):
        self.assertIsNone(current())

    def test_push_binds_and_pop_resets_to_prior_value(self):
        outer = Snapshot(trace_id="a" * 32)
        outer_token = push(outer)
        try:
            inner = Snapshot(trace_id="b" * 32)
            inner_token = push(inner)
            self.assertEqual(inner, current())
            pop(inner_token)
            self.assertEqual(outer, current())
        finally:
            pop(outer_token)
        self.assertIsNone(current())

    def test_isolation_across_concurrent_async_tasks(self):
        results = {}

        async def bind_and_read(name, trace_id):
            token = push(Snapshot(trace_id=trace_id))
            try:
                await asyncio.sleep(0)
                results[name] = current().trace_id
            finally:
                pop(token)

        async def run_both():
            await asyncio.gather(
                bind_and_read("first", "1" * 32),
                bind_and_read("second", "2" * 32),
            )

        asyncio.run(run_both())
        self.assertEqual({"first": "1" * 32, "second": "2" * 32}, results)

    def test_context_survives_threadpool_offload(self):
        snapshot = Snapshot(trace_id="c" * 32)
        token = push(snapshot)
        try:
            ctx = contextvars.copy_context()
            with ThreadPoolExecutor(max_workers=1) as pool:
                seen = pool.submit(ctx.run, current).result()
        finally:
            pop(token)
        self.assertEqual(snapshot, seen)

    def test_reused_worker_carries_no_leftover_context(self):
        def handle_request(snapshot):
            token = push(snapshot)
            try:
                return current()
            finally:
                pop(token)

        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(handle_request, Snapshot(trace_id="d" * 32)).result()
            second_seen_before_bind = pool.submit(current).result()

        self.assertEqual("d" * 32, first.trace_id)
        self.assertIsNone(second_seen_before_bind)


if __name__ == "__main__":
    unittest.main()
