"""Transport, boundary, and response-header enforcement before route work."""

from __future__ import annotations

import ipaddress
import threading
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from arrive90_service.contracts import ServiceConfig
from arrive90_service.observability import AuditSink, access_event

Clock = Callable[[], datetime]


def _headers(scope: Scope) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", ())
    }


async def _reject(
    send: Send, status: int, *, security_headers: tuple[tuple[bytes, bytes], ...]
) -> None:
    body = b'{"detail":"request rejected"}'
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": (
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
                *security_headers,
            ),
        }
    )
    await send({"type": "http.response.body", "body": body})


class BoundaryMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        config: ServiceConfig,
        clock: Clock,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._app = app
        self._config = config
        self._clock = clock
        self._audit_sink = audit_sink
        self._clock_lock = threading.Lock()
        self._last_cutoff: datetime | None = None

    @property
    def security_headers(self) -> tuple[tuple[bytes, bytes], ...]:
        return (
            (
                b"content-security-policy",
                b"default-src 'self'; script-src 'self'; style-src 'self'; "
                b"img-src 'self' data:; connect-src 'self'; worker-src 'self'; "
                b"frame-ancestors 'none'; base-uri 'none'; object-src 'none'",
            ),
            (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
            (b"referrer-policy", b"no-referrer"),
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (
                b"permissions-policy",
                b"camera=(), microphone=(), geolocation=(), payment=(), usb=()",
            ),
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = _headers(scope)
        host = headers.get("host", "")
        peer = (scope.get("client") or ("", 0))[0]
        forwarded = any(name.startswith("x-forwarded-") for name in headers)
        if host not in self._config.allowed_hosts:
            await _reject(send, 400, security_headers=self.security_headers)
            return
        if forwarded and peer not in self._config.trusted_proxy_addresses:
            await _reject(send, 400, security_headers=self.security_headers)
            return
        scheme = scope.get("scheme", "http")
        if peer in self._config.trusted_proxy_addresses:
            scheme = headers.get("x-forwarded-proto", scheme)
            forwarded_for = headers.get("x-forwarded-for")
            if forwarded_for is not None:
                try:
                    if "," in forwarded_for:
                        raise ValueError
                    scope.setdefault("state", {})["client_identity"] = str(
                        ipaddress.ip_address(forwarded_for)
                    )
                except ValueError:
                    await _reject(send, 400, security_headers=self.security_headers)
                    return
        if not self._config.loopback_only and scheme != "https":
            await _reject(send, 400, security_headers=self.security_headers)
            return
        if scope.get("method") == "POST" and headers.get("origin") not in (
            self._config.allowed_origins
        ):
            await _reject(send, 403, security_headers=self.security_headers)
            return
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self._config.maximum_body_bytes:
                    await _reject(send, 413, security_headers=self.security_headers)
                    return
            except ValueError:
                await _reject(send, 400, security_headers=self.security_headers)
                return
        body_messages: list[Message] = []
        body_bytes = 0
        while True:
            message = await receive()
            body_messages.append(message)
            body_bytes += len(message.get("body", b""))
            if body_bytes > self._config.maximum_body_bytes:
                await _reject(send, 413, security_headers=self.security_headers)
                return
            if not message.get("more_body", False):
                break
        cutoff = self._clock()
        if cutoff.tzinfo is None or cutoff.utcoffset() != UTC.utcoffset(cutoff):
            await _reject(send, 503, security_headers=self.security_headers)
            return
        with self._clock_lock:
            if (
                self._last_cutoff is not None
                and (cutoff - self._last_cutoff).total_seconds()
                < -self._config.maximum_clock_regression_seconds
            ):
                await _reject(send, 503, security_headers=self.security_headers)
                return
            self._last_cutoff = max(cutoff, self._last_cutoff or cutoff)
        scope.setdefault("state", {})["initial_query_cutoff_utc"] = cutoff
        message_index = 0

        async def replay_receive() -> Message:
            nonlocal message_index
            if message_index < len(body_messages):
                message = body_messages[message_index]
                message_index += 1
                return message
            return await receive()

        async def secured_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", ()))
                names = {name.lower() for name, _value in response_headers}
                for name, value in self.security_headers:
                    if name not in names:
                        response_headers.append((name, value))
                origin = headers.get("origin")
                if origin in self._config.allowed_origins:
                    response_headers.append((b"access-control-allow-origin", origin.encode()))
                    response_headers.append((b"vary", b"Origin"))
                message["headers"] = response_headers
                if self._audit_sink is not None:
                    # The allow-listed sink cannot affect rider-facing availability.
                    with suppress(Exception):
                        self._audit_sink(
                            access_event(
                                method=str(scope.get("method", "OTHER")),
                                path=str(scope.get("path", "")),
                                status_code=int(message["status"]),
                            )
                        )
            await send(message)

        await self._app(scope, replay_receive, secured_send)


def utc_clock() -> datetime:
    return datetime.now(UTC)
