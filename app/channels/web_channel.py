"""FastHTML web channel — serves a chat UI and a JSON WebSocket endpoint.

The channel exposes two endpoints on the same uvicorn server:

    GET /          — FastHTML chat UI (HTML page, served to browsers)
    WS  /ws        — WebSocket endpoint (JSON framing)

WebSocket message framing::

    {"type": "message", "content": "..."}

Plain text is also accepted as a convenience.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

import uvicorn
from fasthtml.common import (
    A,
    Button,
    Div,
    Head,
    Html,
    Input,
    Label,
    Link,
    Meta,
    NotStr,
    Script,
    Span,
    Textarea,
    Title,
    Body,
    H1,
    P,
    fast_app,
)
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect
from fasthtml.svg import Svg, Path as SvgPath, Polyline, Rect

from .. import config
from ..core import runtime
from ..infra.conversations import ConversationStore
from .channel import Channel, ChannelType
from .message import IncomingMessage, OutgoingMessage
from .message_queue import MessageQueue
from .web_auth import WebAuthMiddleware, is_loopback_host


log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# FastHTML page builder                                                        #
# --------------------------------------------------------------------------- #

# Bump when web_channel.css / web_channel.js change, so browsers (Edge caches
# static assets aggressively) fetch the new copy instead of a stale one.
_ASSET_VERSION = "10"

# Paperclip icon for the attach button (inline so it inherits theme colors).
_PAPERCLIP_SVG = (
    '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 '
    '5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>'
)


def _build_page(auth_enabled: bool = False) -> Html:

    def MarkdownIcon(size=16):
        return Svg(
            Rect(x="3", y="5", width="18", height="14", rx="2"),
            SvgPath(d="M7 15V9l3 3 3-3v6"),
            SvgPath(d="M17 9v4m0 0 2-2m-2 2-2-2"),
            width=size, height=size, viewBox="0 0 24 24",
            fill="none", stroke="currentColor", stroke_width="2",
            stroke_linecap="round", stroke_linejoin="round",
        )

    def RawIcon(size=16):
        return Svg(
            Polyline(points="8 7 3 12 8 17"),
            Polyline(points="16 7 21 12 16 17"),
            width=size, height=size, viewBox="0 0 24 24",
            fill="none", stroke="currentColor", stroke_width="2",
            stroke_linecap="round", stroke_linejoin="round",
        )

    return Html(
        Head(
            Meta(charset="utf-8"),
            Meta(
                name="viewport",
                content="width=device-width, initial-scale=1, viewport-fit=cover, "
                "interactive-widget=resizes-content",
            ),
            Title("anotherbot"),
            Link(rel="preconnect", href="https://fonts.googleapis.com"),
            Link(
                rel="stylesheet",
                href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap",
            ),
            Link(rel="stylesheet", href=f"/static/web_channel.css?v={_ASSET_VERSION}"),
        ),
        Body(
            Div(
                # ---- Header (full width) ----
                Div(
                    Div(
                        Button("☰", id="sidebar-toggle", title="Toggle sidebar"),
                        H1("anotherbot"),
                        id="header-left",
                    ),
                    Div(
                        Div(
                            Span(id="status-dot"),
                            Span("Connecting…", id="status-text"),
                            id="status",
                        ),
                        Div(
                            Button(MarkdownIcon(), type="button", data_mode="md",
                                title="Markdown", aria_label="Markdown",
                                aria_pressed="true"),
                            Button(RawIcon(), type="button", data_mode="raw",
                                title="Raw", aria_label="Raw",
                                aria_pressed="false"),
                            id="render-toggle",
                            role="group",
                            title="How to show bot replies",
                        ),
                        Button("☾", id="theme-btn", title="Toggle light/dark"),
                        *(
                            [
                                A(
                                    "⍈",
                                    href="/logout",
                                    id="logout-link",
                                    title="Log out",
                                    aria_label="Log out",
                                )
                            ]
                            if auth_enabled
                            else []
                        ),
                        id="header-right",
                    ),
                    id="header",
                ),
                # ---- Body row: sidebar + chat ----
                Div(
                    # Sidebar
                    Div(
                        Div(
                            Span("Conversations"),
                            Button("+ New", id="new-conv-btn"),
                            id="sidebar-header",
                        ),
                        Div(id="conv-list"),
                        id="sidebar",
                    ),
                    # Chat panel
                    Div(
                        # Scroll container (outer) + messages (inner flex, bottom-anchored)
                        Div(
                            Div(
                                Div(
                                    Div("✦", cls="icon"),
                                    P(
                                        "Ask me anything, or try /help for available commands."
                                    ),
                                    id="empty",
                                ),
                                id="messages",
                            ),
                            id="messages-wrap",
                        ),
                        # Thinking dots — always just above the input box
                        Div(
                            Div(
                                Div(Span(), Span(), Span(), cls="dots"),
                                cls="thinking-bubble",
                            ),
                            id="thinking",
                        ),
                        # Selected-attachment preview chips (populated by JS)
                        Div(id="attachments"),
                        # Input area
                        Div(
                            # Hidden native file picker. A <label for> (not a JS
                            # click) opens it — native label activation works
                            # consistently across browsers (Edge included),
                            # whereas calling input.click() on a display:none
                            # input is unreliable in Edge. Hide it with an inline
                            # visually-hidden style (not display:none, and not an
                            # external CSS class): display:none suppresses the
                            # `change` event in Edge, and an inline style can't be
                            # defeated by a stale cached stylesheet.
                            Input(
                                type="file",
                                id="file-input",
                                multiple=True,
                                cls="visually-hidden",
                                style=(
                                    "position:absolute;width:1px;height:1px;"
                                    "padding:0;margin:-1px;overflow:hidden;"
                                    "clip:rect(0,0,0,0);white-space:nowrap;border:0"
                                ),
                            ),
                            Label(
                                NotStr(_PAPERCLIP_SVG),
                                id="attach-btn",
                                title="Attach files",
                                **{"for": "file-input"},
                            ),
                            Textarea(
                                id="msg-input",
                                aria_label="Message",
                                placeholder=(
                                    "Message anotherbot…  "
                                    "(Enter to send, Shift+Enter for newline)"
                                ),
                                autocomplete="off",
                                rows="1",
                            ),
                            Button("Send", id="send-btn", disabled=True),
                            id="input-area",
                        ),
                        id="main",
                    ),
                    id="body-row",
                ),
                id="app",
            ),
            # Vendored (no CDN) so the UI works offline: markdown parser + HTML sanitizer.
            Script(src="/static/vendor/marked.min.js?v=15.0.12"),
            Script(src="/static/vendor/purify.min.js?v=3.2.6"),
            Script(src=f"/static/web_channel.js?v={_ASSET_VERSION}"),
        ),
        lang="en",
    )


# --------------------------------------------------------------------------- #
# Channel class                                                                #
# --------------------------------------------------------------------------- #


class WebChannel(Channel):
    """FastHTML web channel.

    Serves the chat UI at ``GET /`` and accepts WebSocket connections at
    ``WS /ws``.  Multiple concurrent clients are supported — each gets a
    unique UUID stored in ``_connections``.
    """

    def __init__(
        self,
        mq: MessageQueue,
        host: str = "127.0.0.1",
        port: int = 8765,
        password: str | None = None,
    ) -> None:
        self.mq = mq
        self.host = host
        self.port = port
        self.password = password or None
        self.stopped: bool = False
        self._connections: dict[str, WebSocket] = {}
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._conn_lock = asyncio.Lock()
        self._upload_dir = config.PROJECT_HOME / "uploads"
        # Built in start(): the FastHTML app, and what uvicorn serves (the app,
        # wrapped in WebAuthMiddleware when a password is set).
        self._fasthtml_app = None
        self._asgi_app = None
        mq.register(self, self.send_message)

    # Cap on a single multipart upload request (combined across files). The
    # agent enforces its own per-message limit when building the LLM payload.
    _MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

    # -- Channel ABC --------------------------------------------------------

    @property
    def has_stopped(self) -> bool:
        return self.stopped

    def clear_stopped(self) -> None:
        self.stopped = False

    @property
    def channel_type(self) -> ChannelType:
        return ChannelType.WEB

    @property
    def default_metadata(self) -> dict:
        return {}

    async def error_handler(self, update: object, context: object) -> None:
        log.error("WebChannel error: %s", context)

    async def process_message(self, message: object) -> None:
        pass  # handled inline in the WebSocket endpoint

    # -- Lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Build the FastHTML app, its routes, and the WebSocket endpoint.

        Raises:
            RuntimeError: if the channel would listen on a non-loopback host
                without a password.
        """
        log.info("Building web channel on %s:%s", self.host, self.port)

        if not self.password and not is_loopback_host(self.host):
            raise RuntimeError(
                f"Web channel would listen on {self.host} without a password. "
                "Set WEB_PASSWORD, or bind to 127.0.0.1 (WEBSOCKET_HOST)."
            )
        if not self.password:
            log.warning(
                "Web channel has no password (WEB_PASSWORD unset); localhost access only"
            )

        self._fasthtml_app, rt = fast_app(hdrs=())
        self._asgi_app = (
            WebAuthMiddleware(self._fasthtml_app, self.password)
            if self.password
            else self._fasthtml_app
        )

        rt("/")(self._index)
        rt("/api/conversations")(self._conversations_api)
        rt("/api/messages")(self._messages_api)
        rt("/api/status")(self._status_api)
        rt("/api/upload", methods=["POST"])(self._upload_api)

        routes = self._fasthtml_app.router.routes
        # Low-level Starlette WebSocket route (multi-client management)
        routes.insert(0, WebSocketRoute("/ws", self._ws_endpoint))
        # Static assets (CSS, JS)
        static_dir = Path(__file__).parent / "static"
        routes.insert(
            1, Mount("/static", StaticFiles(directory=str(static_dir)), name="static")
        )

    # -- HTTP routes ----------------------------------------------------------

    def _index(self):
        """Serve the chat UI page."""
        return _build_page(auth_enabled=bool(self.password))

    def _conversations_api(self):
        """List conversations from all channels plus the web channel's active one."""
        ch = ChannelType.WEB.value
        convs = ConversationStore().list()
        active_id = runtime.get(f"conversation_id:{ch}")
        return JSONResponse({"conversations": convs, "active_id": active_id})

    def _messages_api(self, req: Request):
        """Return the messages of the conversation given by ``?conv_id=``."""
        try:
            conv_id = int(req.query_params.get("conv_id", 0))
        except (ValueError, TypeError):
            return Response(status_code=400)
        if not conv_id:
            return Response(status_code=400)
        store = ConversationStore()
        if not store.get(conv_id):
            return Response(status_code=404)
        return JSONResponse({"messages": store.load_messages(conv_id)})

    def _status_api(self):
        """Return the model currently in use."""
        model = runtime.get("model", config.get("model", "AI"))
        return JSONResponse({"model": model})

    async def _upload_api(self, req: Request):
        """Accept one or more multipart files and store them on disk.

        Returns the server-side basenames the browser then references in
        its WebSocket ``message`` frame via the ``files`` field.  Files are
        written under ``$ANOTHERBOT_HOME/uploads`` with a UUID prefix so
        concurrent clients never collide.
        """
        form = await req.form()
        uploads = [f for f in form.getlist("files") if getattr(f, "filename", None)]
        if not uploads:
            return Response("No files provided", status_code=400)

        self._upload_dir.mkdir(parents=True, exist_ok=True)
        try:
            saved = await self._save_uploads(uploads)
        finally:
            for uf in uploads:
                close = getattr(uf, "close", None)
                if close:
                    await close()

        if saved is None:
            limit_mb = max(1, self._MAX_UPLOAD_BYTES // (1024 * 1024))
            return JSONResponse(
                {"error": f"Upload exceeds {limit_mb} MB limit"}, status_code=413
            )
        return JSONResponse({"files": saved})

    async def _save_uploads(self, uploads: list) -> list[dict] | None:
        """Write uploaded files to the upload dir.

        Returns ``[{"path": stored_name, "name": original_name}, ...]``, or
        ``None`` (after deleting anything already written) if the combined
        size exceeds ``_MAX_UPLOAD_BYTES``.
        """
        saved: list[dict] = []
        total = 0
        for uf in uploads:
            name = Path(uf.filename).name
            stored = f"{uuid.uuid4().hex}_{name}"
            out_path = self._upload_dir / stored
            too_large = False
            with out_path.open("wb") as out:
                while chunk := await uf.read(64 * 1024):
                    total += len(chunk)
                    if total > self._MAX_UPLOAD_BYTES:
                        too_large = True
                        break
                    out.write(chunk)
            if too_large:
                out_path.unlink(missing_ok=True)
                for s in saved:
                    (self._upload_dir / s["path"]).unlink(missing_ok=True)
                return None
            saved.append({"path": stored, "name": name})
        return saved

    # -- WebSocket --------------------------------------------------------------

    async def _ws_endpoint(self, ws: WebSocket) -> None:
        """Serve one browser connection: forward its messages to the agent."""
        await ws.accept()

        client_id = str(uuid.uuid4())
        log.info("WebSocket client connected: %s", client_id)

        async with self._conn_lock:
            self._connections[client_id] = ws

        try:
            while True:
                raw = await ws.receive_text()
                if len(raw) > 65_536:
                    await ws.close(code=1009, reason="Message too large")
                    return
                await self._handle_ws_text(client_id, raw)
        except WebSocketDisconnect:
            log.info("WebSocket client disconnected: %s", client_id)
        except Exception:  # pylint: disable=broad-exception-caught  # one bad client must not kill the server
            log.exception("WebSocket error for client %s", client_id)
        finally:
            async with self._conn_lock:
                self._connections.pop(client_id, None)
                self._send_locks.pop(client_id, None)

    async def _handle_ws_text(self, client_id: str, raw: str) -> None:
        """Parse one WebSocket frame and enqueue it for the agent.

        ``/whoami`` is answered inline because it needs the per-connection
        client ID. All other commands (/help, /status, /stop, /new, /load, …)
        are forwarded to BackgroundAgent's CommandRegistry for consistency with
        the Telegram and Discord channels.
        """
        content, files = self._extract_message(raw)
        if not content and not files:
            return

        is_command = bool(content and content.startswith("/"))
        if is_command:
            cmd = content[1:].split(maxsplit=1)
            if cmd and cmd[0].lower() == "whoami":
                await self._safe_send_json(
                    client_id,
                    {"type": "system", "content": f"Connection ID: {client_id}"},
                )
                return

        metadata = {"websocket_id": client_id, "is_command": is_command}
        if files:
            metadata["files"] = files
        await self.mq.incoming.put(
            IncomingMessage(
                content=content or "", channel=ChannelType.WEB, metadata=metadata
            )
        )

    async def run_polling(self) -> None:
        """Start uvicorn and serve until cancelled."""
        server_config = uvicorn.Config(
            app=self._asgi_app,
            host=self.host,
            port=self.port,
            log_level="info",
        )
        server = uvicorn.Server(server_config)
        log.info("Web UI at http://%s:%s/", self.host, self.port)
        log.info("WebSocket at ws://%s:%s/ws", self.host, self.port)
        await server.serve()

    # -- Message delivery ---------------------------------------------------

    async def send_message(self, message: OutgoingMessage) -> None:
        client_id = message.metadata.get("websocket_id")
        is_command = message.metadata.get("is_command", False)
        msg_type = "system" if is_command else "message"
        payload = {"type": msg_type, "content": message.content}
        if client_id:
            await self._safe_send_json(client_id, payload)
        else:
            # No specific client (e.g. scheduled task delivery) — broadcast to all connected clients
            async with self._conn_lock:
                targets = list(self._connections.keys())
            for cid in targets:
                await self._safe_send_json(cid, payload)

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _extract_content(raw: str) -> str | None:
        raw = raw.strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw
        if isinstance(data, dict) and data.get("type") == "message":
            return str(data.get("content") or "").strip() or None
        return raw

    def _extract_message(self, raw: str) -> tuple[str | None, list[str]]:
        """Parse a raw WebSocket frame into (text, attachment_paths).

        ``files`` is a list of basenames the browser received from
        ``/api/upload``; each is resolved against the upload dir and dropped
        if it escapes that dir or no longer exists.
        """
        stripped = raw.strip()
        if not stripped:
            return None, []

        try:
            data = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            return stripped, []

        if isinstance(data, dict) and data.get("type") == "message":
            content = str(data.get("content") or "").strip() or None
            files = self._resolve_upload_paths(data.get("files"))
            return content, files

        return stripped, []

    def _resolve_upload_paths(self, names: object) -> list[str]:
        """Map client-supplied upload basenames to validated absolute paths.

        Only the basename is honoured (joined to the upload dir), so a client
        can never reference files outside ``self._upload_dir``.
        """
        if not isinstance(names, list):
            return []
        upload_dir = self._upload_dir.resolve()
        resolved: list[str] = []
        for name in names:
            if not isinstance(name, str) or not name:
                continue
            candidate = (upload_dir / Path(name).name).resolve()
            if candidate.parent == upload_dir and candidate.is_file():
                resolved.append(str(candidate))
        return resolved

    async def _safe_send_json(self, client_id: str, payload: dict) -> None:
        async with self._conn_lock:
            ws = self._connections.get(client_id)
            if ws is None:
                return
            lock = self._send_locks.setdefault(client_id, asyncio.Lock())
        async with lock:
            try:
                await ws.send_json(payload)
            except Exception:  # pylint: disable=broad-exception-caught  # drop the broken connection
                log.exception("Failed to send to WebSocket client %s", client_id)
                async with self._conn_lock:
                    self._connections.pop(client_id, None)
                    self._send_locks.pop(client_id, None)
