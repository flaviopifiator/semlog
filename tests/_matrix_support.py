"""Shared helper for compatibility-matrix tests (CP-002): drives a full
ASGI application -- FastAPI/Starlette, or Django's own `ASGIHandler` --
through a hand-written `scope`/`receive`/`send` caller (CP-011), extended
so `receive()` delivers the request body once and then hangs instead of
immediately reporting a disconnect.

Real ASGI handlers race a "listen for disconnect" task against request
processing (Django's `ASGIHandler.handle` does this explicitly, via
`asyncio.wait(..., return_when=FIRST_COMPLETED)`). The plain
`tests/_asgi_support.py` caller reports an immediate disconnect once its
queued messages run out, which wins that race and cancels the response
before it is sent -- harmless for the library's own middleware tests
(which never exercise that race), but wrong for a real framework's
handler. Not itself a `test*.py` file, so the traceability checker skips
it.
"""

from __future__ import annotations

import asyncio


async def call_asgi_app(app, scope, body=b""):
    """Drive `app(scope, receive, send)` for one full request/response
    cycle: `receive()` yields exactly one `http.request` message carrying
    `body`, then blocks forever -- never reporting a disconnect -- so a
    handler's own disconnect-listening task never races the response."""
    sent = []
    delivered = False
    never = asyncio.Event()

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await never.wait()
        return {"type": "http.disconnect"}  # pragma: no cover -- never reached

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent
