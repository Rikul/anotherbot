"""Password login for the web channel.

``WebAuthMiddleware`` is a pure ASGI middleware (Starlette's
``BaseHTTPMiddleware`` doesn't see WebSocket scopes) that guards every HTTP
request *and* the ``/ws`` handshake:

* ``POST /api/login`` (JSON ``{"password": ...}``) and ``POST /api/logout``
  are served by the middleware itself.
* A successful login sets a signed, ``HttpOnly``, ``SameSite=Lax`` session
  cookie. The signing key is derived from the password, so changing
  ``WEB_PASSWORD`` logs everyone out.
* The login page (``/login``) and its assets are public and passed through to
  the app, which serves them from ``frontend/``; a logged-in ``GET /login``
  redirects to ``/``.
* Unauthenticated requests: ``GET /`` redirects to ``/login``, other HTTP
  requests get 401, and WebSocket handshakes are rejected.
* State-changing requests and WebSocket handshakes must come from the same
  origin (``Origin`` header host == ``Host``), blocking cross-site WebSocket
  hijacking and CSRF from other ports/sites.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
from urllib.parse import urlsplit

from itsdangerous import BadSignature, TimestampSigner
from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse, RedirectResponse, Response

log = logging.getLogger(__name__)

COOKIE_NAME = "ab_session"
SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days
_SESSION_VALUE = "web"
_FAILED_LOGIN_DELAY = 1.0  # seconds; slows down password guessing
_MAX_LOGIN_BODY = 4096
# Served to logged-out browsers: the login page and the files it loads.
_PUBLIC_PATHS = frozenset({"/login", "/login.css", "/login.js"})


def is_loopback_host(host: str) -> bool:
    """True if binding to ``host`` only accepts connections from this machine."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class WebAuthMiddleware:  # pylint: disable=too-few-public-methods  # ASGI app: only __call__
    """ASGI middleware that puts the whole web app behind a password login.

    Args:
        app: the ASGI app to protect.
        password: the ``WEB_PASSWORD``; must not be empty.
    """

    def __init__(self, app, password: str) -> None:
        if not password:
            raise ValueError("WebAuthMiddleware requires a non-empty password")
        self.app = app
        self._password = password.encode()
        key = hashlib.sha256(b"anotherbot-web-session\0" + self._password).digest()
        self._signer = TimestampSigner(key, salt="web-session")

    # -- session helpers -----------------------------------------------------

    def _is_authenticated(self, headers: Headers) -> bool:
        token = cookie_parser(headers.get("cookie", "")).get(COOKIE_NAME)
        if not token:
            return False
        try:
            value = self._signer.unsign(token, max_age=SESSION_MAX_AGE)
        except BadSignature:  # also covers SignatureExpired
            return False
        return value.decode() == _SESSION_VALUE

    def _check_password(self, candidate: str) -> bool:
        return hmac.compare_digest(candidate.encode(), self._password)

    @staticmethod
    def _same_origin(headers: Headers) -> bool:
        origin = headers.get("origin")
        if origin is None:
            return True  # non-browser clients and same-origin GETs may omit it
        return urlsplit(origin).netloc.lower() == headers.get("host", "").lower()

    @staticmethod
    def _is_https(scope, headers: Headers) -> bool:
        proto = headers.get("x-forwarded-proto", scope.get("scheme", "http"))
        return proto.split(",")[0].strip() in ("https", "wss")

    # -- ASGI ----------------------------------------------------------------

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "websocket":
            await self._handle_websocket(scope, receive, send)
        elif scope["type"] == "http":
            await self._handle_http(scope, receive, send)
        else:  # lifespan
            await self.app(scope, receive, send)

    async def _handle_websocket(self, scope, receive, send) -> None:
        headers = Headers(scope=scope)
        if not self._same_origin(headers):
            log.warning(
                "Rejected cross-origin WebSocket from %r", headers.get("origin")
            )
        elif self._is_authenticated(headers):
            await self.app(scope, receive, send)
            return
        # Closing before accept makes the server reject the handshake (HTTP 403).
        await receive()  # websocket.connect
        await send({"type": "websocket.close", "code": 1008})

    async def _handle_http(self, scope, receive, send) -> None:
        headers = Headers(scope=scope)
        path = scope["path"]
        method = scope["method"]

        if method not in ("GET", "HEAD", "OPTIONS") and not self._same_origin(headers):
            await Response("Cross-origin request rejected", status_code=403)(
                scope, receive, send
            )
            return

        if path == "/api/login":
            response = await self._login(scope, receive, method, headers)
        elif path == "/api/logout":
            response = self._logout(method)
        elif path == "/login" and self._is_authenticated(headers):
            response = RedirectResponse("/", status_code=303)
        elif self._is_authenticated(headers) or (
            path in _PUBLIC_PATHS and method in ("GET", "HEAD")
        ):
            await self.app(scope, receive, send)
            return
        elif path == "/" and method in ("GET", "HEAD"):
            response = RedirectResponse("/login", status_code=303)
        else:
            response = JSONResponse(
                {"error": "authentication required"}, status_code=401
            )
        await response(scope, receive, send)

    async def _login(self, scope, receive, method: str, headers: Headers) -> Response:
        if method != "POST":
            return Response(status_code=405, headers={"Allow": "POST"})

        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if len(body) > _MAX_LOGIN_BODY:
                return JSONResponse({"error": "Request too large"}, status_code=413)
            if not message.get("more_body"):
                break
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            data = None
        password = data.get("password") if isinstance(data, dict) else None
        if not isinstance(password, str):
            return JSONResponse(
                {"error": 'Expected JSON {"password": "..."}'}, status_code=400
            )

        if not self._check_password(password):
            client = (scope.get("client") or ("?",))[0]
            log.warning("Failed web login from %s", client)
            await asyncio.sleep(_FAILED_LOGIN_DELAY)
            return JSONResponse({"error": "Wrong password."}, status_code=401)

        response = JSONResponse({"ok": True})
        response.set_cookie(
            COOKIE_NAME,
            self._signer.sign(_SESSION_VALUE).decode(),
            max_age=SESSION_MAX_AGE,
            path="/",
            httponly=True,
            samesite="lax",  # Strict would drop the session when following links from chat apps
            secure=self._is_https(scope, headers),
        )
        return response

    @staticmethod
    def _logout(method: str) -> Response:
        if method != "POST":
            return Response(status_code=405, headers={"Allow": "POST"})
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response
