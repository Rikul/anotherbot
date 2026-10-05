"""Password login for the web channel.

``WebAuthMiddleware`` is a pure ASGI middleware (Starlette's
``BaseHTTPMiddleware`` doesn't see WebSocket scopes) that guards every HTTP
request *and* the ``/ws`` handshake:

* ``GET/POST /login`` and ``GET /logout`` are served by the middleware itself.
* A successful login sets a signed, ``HttpOnly``, ``SameSite=Lax`` session
  cookie. The signing key is derived from the password, so changing
  ``WEB_PASSWORD`` logs everyone out.
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
import html
import ipaddress
import logging
from urllib.parse import parse_qs, urlsplit

from itsdangerous import BadSignature, TimestampSigner
from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

log = logging.getLogger(__name__)

COOKIE_NAME = "ab_session"
SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days
_SESSION_VALUE = "web"
_FAILED_LOGIN_DELAY = 1.0  # seconds; slows down password guessing
_MAX_LOGIN_BODY = 4096


def is_loopback_host(host: str) -> bool:
    """True if binding to ``host`` only accepts connections from this machine."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _login_page(error: str = "") -> HTMLResponse:
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    body = f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>anotherbot — log in</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: system-ui, sans-serif; margin: 0;
         display: grid; place-items: center; min-height: 100vh; }}
  form {{ display: flex; flex-direction: column; gap: 12px; width: min(320px, 90vw); }}
  input, button {{ font: inherit; padding: 10px 12px;
                   border-radius: 8px; border: 1px solid #8884; }}
  button {{ cursor: pointer; background: #6366f1; color: #fff; border: none; }}
  .err {{ color: #dc2626; margin: 0; }}
</style></head>
<body><form method="post" action="/login">
  <h1>anotherbot</h1>
  {err}
  <input type="password" name="password" placeholder="Password" autocomplete="current-password" autofocus required>
  <button type="submit">Log in</button>
</form></body></html>"""
    status = 401 if error else 200
    return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


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

        if path == "/login":
            response = await self._login(scope, receive, method, headers)
        elif path == "/logout":
            response = RedirectResponse("/login", status_code=303)
            response.delete_cookie(COOKIE_NAME, path="/")
        elif self._is_authenticated(headers):
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
        if method in ("GET", "HEAD"):
            if self._is_authenticated(headers):
                return RedirectResponse("/", status_code=303)
            return _login_page()
        if method != "POST":
            return Response(status_code=405, headers={"Allow": "GET, POST"})

        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if len(body) > _MAX_LOGIN_BODY:
                return Response("Request too large", status_code=413)
            if not message.get("more_body"):
                break
        password = parse_qs(body.decode("utf-8", "replace")).get("password", [""])[0]

        if not self._check_password(password):
            client = (scope.get("client") or ("?",))[0]
            log.warning("Failed web login from %s", client)
            await asyncio.sleep(_FAILED_LOGIN_DELAY)
            return _login_page("Wrong password.")

        response = RedirectResponse("/", status_code=303)
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
