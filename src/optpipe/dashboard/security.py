"""Request authority boundary for the local-only, read-only dashboard.

This complements literal loopback binding; it is not user authentication or
authorization for a publicly hosted or multiuser service.
"""
import re

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send


_LOCAL_AUTHORITY = re.compile(rb"(?:127\.0\.0\.1|\[::1\]|localhost)(?::([0-9]{1,5}))?", re.IGNORECASE)


class LocalHostMiddleware:
    """Reject ambiguous/nonlocal HTTP Host headers before any route is called.

    Exact localhost is a local-browser convenience, not a wildcard DNS suffix.
    Forwarding headers are deliberately not authority: this app is not a proxy
    deployment. A future deployment needs its own reviewed access boundary.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            values = [value for name, value in scope.get("headers", []) if name.lower() == b"host"]
            match = _LOCAL_AUTHORITY.fullmatch(values[0]) if len(values) == 1 else None
            if match is None or (match[1] is not None and not 1 <= int(match[1]) <= 65535):
                response = PlainTextResponse("Invalid Host header", status_code=400)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
