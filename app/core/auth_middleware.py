from __future__ import annotations

import hashlib
import logging
import re

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from .audit import record_admin_audit
from .auth_service import AuthService
from .rate_limit import auth_rate_limiter

logger = logging.getLogger(__name__)


def _under(prefix: str, path: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


class AuthMiddleware:
    """Authenticate admin APIs and dynamic APIs without trusting Host/url parsing."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        auth_type = self.requires_auth(path)
        scope.setdefault("state", {})
        scope["state"].update(owner_hash="public", is_admin_auth=False)
        if auth_type is None:
            await self.app(scope, receive, send)
            return

        token = self.extract_token(Headers(scope=scope))
        remote = self.remote_address(scope)
        retry_after = auth_rate_limiter.retry_after(auth_type, remote)
        if retry_after:
            await self.rate_limited_response(retry_after)(scope, receive, send)
            return
        access_key = None
        if auth_type == "admin":
            valid = AuthService.validate_admin_token(token)
        else:
            if not AuthService.has_active_access_key():
                await JSONResponse(
                    {
                        "success": False,
                        "message": "Nenhuma Access Key ativa configurada",
                        "data": [],
                        "errors": [{"detail": "ACCESS_KEY_NOT_CONFIGURED"}],
                        "meta": {},
                    },
                    status_code=503,
                )(scope, receive, send)
                return
            valid, access_key = AuthService.validate_access_token(token)

        if not valid:
            retry_after = auth_rate_limiter.register_failure(auth_type, remote)
            self.audit_failure(
                auth_type,
                remote,
                str(scope["state"].get("request_id") or "") or None,
            )
            if retry_after:
                await self.rate_limited_response(retry_after)(scope, receive, send)
                return
            await JSONResponse(
                {
                    "success": False,
                    "message": "API Key invalida ou nao autorizada",
                    "data": [],
                    "errors": [{"detail": "INVALID_API_KEY"}],
                    "meta": {},
                },
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return

        auth_rate_limiter.register_success(auth_type, remote)
        scope["state"]["owner_hash"] = self.owner_hash(token or "")
        scope["state"]["is_admin_auth"] = auth_type == "admin"
        if access_key:
            scope["state"]["access_key_id"] = int(access_key["id"])
            AuthService.register_access_usage(int(access_key["id"]), path)
        await self.app(scope, receive, send)

    @staticmethod
    def requires_auth(path: str) -> str | None:
        if _under("/api/base/admin", path):
            return "admin"
        if path.startswith("/api/") and not _under("/api/base", path):
            return "api"
        return None

    @staticmethod
    def extract_token(headers: Headers) -> str | None:
        value = headers.get("authorization", "")
        token = value[7:] if value.lower().startswith("bearer ") else value
        token = token.strip()
        if not token or len(token) > 512 or re.search(r"[\r\n\t]", token):
            return None
        return token

    @staticmethod
    def owner_hash(token: str) -> str:
        return "sha256:" + hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def remote_address(scope) -> str:
        client = scope.get("client")
        return str(client[0]) if client else "unknown"

    @staticmethod
    def rate_limited_response(retry_after: int) -> JSONResponse:
        return JSONResponse(
            {
                "success": False,
                "message": "Muitas tentativas de autenticacao; aguarde antes de tentar novamente",
                "data": [],
                "errors": [{"detail": "AUTH_RATE_LIMITED"}],
                "meta": {"retry_after": retry_after},
            },
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )

    @staticmethod
    def audit_failure(auth_type: str, remote: str, request_id: str | None) -> None:
        try:
            remote_hash = "sha256:" + hashlib.sha256(remote.encode()).hexdigest()
            record_admin_audit(
                "auth.failure",
                f"{auth_type}_auth",
                request_id=request_id,
                remote_addr_hash=remote_hash,
                outcome="failure",
            )
        except Exception as exc:
            logger.error("Falha ao auditar autenticacao error_type=%s", type(exc).__name__)
