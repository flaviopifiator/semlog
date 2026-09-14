"""Shared sinks (task 5.1): every subject in a given scenario writes to
the identical kind of sink, so timing differences reflect subject cost,
not sink cost (fairness). Two kinds, both stdlib: a real file open on
`os.devnull` (the cheapest possible sink, isolating pure formatting/
call-site cost), and a real `os.pipe` drained by a background reader
thread (a sink that actually moves bytes, closer to a piped stdout in
production).
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager


@contextmanager
def devnull_sink():
    """A writable binary file on `os.devnull`."""
    with open(os.devnull, "wb") as handle:
        yield handle


@contextmanager
def pipe_sink():
    """A real `os.pipe()`; the write end is yielded as a binary file
    object. A daemon reader thread drains and discards the read end
    continuously, so the writer never blocks on a full kernel pipe
    buffer, no matter how much is written."""
    read_fd, write_fd = os.pipe()
    stop = threading.Event()

    def _drain():
        with os.fdopen(read_fd, "rb") as reader:
            while not stop.is_set():
                chunk = reader.read(65536)
                if not chunk:
                    return

    reader_thread = threading.Thread(target=_drain, daemon=True)
    reader_thread.start()
    writer = os.fdopen(write_fd, "wb")
    try:
        yield writer
    finally:
        writer.close()
        stop.set()
        reader_thread.join(timeout=2)
