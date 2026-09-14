"""Shared ASGI test helper: a hand-written `scope`/`receive`/`send` caller
(CP-011: no third-party ASGI test client for the library's own unit tests);
not itself a `test*.py` file, so the traceability checker skips it.
"""

from __future__ import annotations


def http_scope(headers=None, path="/", method="GET"):
    """A minimal ASGI `http` scope with W3C-header-shaped raw headers."""
    raw = [
        (key.encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": raw,
        "query_string": b"",
    }


async def call_asgi(app, scope, messages_in=()):
    """Drive `app(scope, receive, send)`, feeding queued `messages_in` in
    order and collecting every message the app sends."""
    inbox = list(messages_in)
    sent = []

    async def receive():
        return inbox.pop(0) if inbox else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent
