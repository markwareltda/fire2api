from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.responses import JSONResponse

from .settings import get_settings

logger = logging.getLogger("fire2api.request")
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestSecurityMiddleware:
    def __init__(self, app):
        self.app = app
        self.settings = get_settings()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        incoming = headers.get(b"x-request-id", b"").decode("ascii", "ignore")
        request_id = incoming if SAFE_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                too_large = int(content_length) > self.settings.max_request_body_bytes
            except ValueError:
                too_large = True
            if too_large:
                await JSONResponse(
                    {
                        "success": False,
                        "message": "Body excede o limite permitido",
                        "data": [],
                        "errors": [{"detail": "REQUEST_TOO_LARGE"}],
                        "meta": {"request_id": request_id},
                    },
                    status_code=413,
                )(scope, receive, send)
                return

        consumed = 0

        async def limited_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.settings.max_request_body_bytes:
                    raise RequestBodyTooLarge
            return message

        async def secure_send(message):
            if message["type"] == "http.response.start":
                response_headers = list(message.setdefault("headers", []))
                response_headers.extend(
                    [
                        (b"x-request-id", request_id.encode()),
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                        (b"content-security-policy", b"frame-ancestors 'none'"),
                    ]
                )
            await send(message)

        started = time.monotonic()
        try:
            await self.app(scope, limited_receive, secure_send)
        except RequestBodyTooLarge:
            await JSONResponse(
                {
                    "success": False,
                    "message": "Body excede o limite permitido",
                    "data": [],
                    "errors": [{"detail": "REQUEST_TOO_LARGE"}],
                    "meta": {"request_id": request_id},
                },
                status_code=413,
            )(scope, receive, secure_send)
        finally:
            logger.info(
                "request method=%s path=%s request_id=%s duration_ms=%d",
                scope.get("method"),
                scope.get("path"),
                request_id,
                int((time.monotonic() - started) * 1000),
            )


class RequestBodyTooLarge(Exception):
    pass
