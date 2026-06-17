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
import uvicorn
from pathlib import Path
from fasthtml.common import (
    Button, Div, Head, Html, Input, Label, Link, Meta, NotStr, Script, Span,
    Textarea, Title, Body, H1, P, fast_app,
)
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .channel import Channel, ChannelType
from .message import IncomingMessage, OutgoingMessage
from .message_queue import MessageQueue

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# FastHTML page builder                                                        #
# --------------------------------------------------------------------------- #

# Paperclip icon for the attach button (inline so it inherits theme colors).
_PAPERCLIP_SVG = (
    '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 '
    '5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>'
)


def _build_page() -> Html:
    return Html(
        Head(
            Meta(charset="utf-8"),
            Meta(name="viewport", content="width=device-width, initial-scale=1"),
            Title("anotherbot"),
            Link(rel="preconnect", href="https://fonts.googleapis.com"),
            Link(rel="stylesheet",
                 href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap"),
            Link(rel="stylesheet", href="/static/web_channel.css"),
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
                        Button("☾", id="theme-btn", title="Toggle light/dark"),
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
                                    P("Ask me anything, or try /help for available commands."),
                                    id="empty",
                                ),
                                id="messages",
                            ),
                            id="messages-wrap",
                        ),
                        # Thinking dots — always just above the input box
                        Div(
                            Div(Div(Span(), Span(), Span(), cls="dots"), cls="thinking-bubble"),
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
                            # input is unreliable in Edge. The input is visually
                            # hidden (not display:none) so its `change` event
                            # still fires reliably when opened via the label.
                            Input(
                                type="file",
                                id="file-input",
                                multiple=True,
                                cls="visually-hidden",
                            ),
                            Label(
                                NotStr(_PAPERCLIP_SVG),
                                id="attach-btn",
                                title="Attach files",
                                **{"for": "file-input"},
                            ),
                            Textarea(
                                id="msg-input",
                                placeholder="Message anotherbot…  (Enter to send, Shift+Enter for newline)",
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
            Script(src="/static/web_channel.js"),
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
    ) -> None:
        self.mq = mq
        self.host = host
        self.port = port
        self.stopped: bool = False
        self._connections: dict[str, WebSocket] = {}
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._conn_lock = asyncio.Lock()
        from .. import config as _cfg
        self._upload_dir = _cfg.PROJECT_HOME / "uploads"
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
        log.error(f"WebChannel error: {context}")

    async def process_message(self, message: object) -> None:
        pass  # handled inline in the WebSocket endpoint

    # -- Lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Build the FastHTML app + WebSocket route."""
        log.info(f"Building web channel on {self.host}:{self.port}")

        self._fasthtml_app, rt = fast_app(hdrs=())

        @rt("/")
        def index():
            return _build_page()

        @rt("/api/conversations")
        def conversations_api(req):
            from starlette.responses import JSONResponse
            from ..infra.conversations import ConversationStore
            from ..core import runtime as _rt
            store = ConversationStore()
            ch = ChannelType.WEB.value
            convs = store.list(ch)
            active_id = _rt.get(f"conversation_id:{ch}")
            return JSONResponse({"conversations": convs, "active_id": active_id})

        @rt("/api/messages")
        def messages_api(req):
            from starlette.responses import JSONResponse, Response
            from ..infra.conversations import ConversationStore
            try:
                conv_id = int(req.query_params.get("conv_id", 0))
            except (ValueError, TypeError):
                return Response(status_code=400)
            if not conv_id:
                return Response(status_code=400)
            store = ConversationStore()
            conv = store.get(conv_id)
            if conv is None or conv.get("channel") != ChannelType.WEB.value:
                return Response(status_code=404)
            msgs = store.load_messages(conv_id)
            return JSONResponse({"messages": msgs})

        @rt("/api/status")
        def status_api(req):
            from starlette.responses import JSONResponse
            from .. import config as _cfg
            from ..core import runtime as _rt
            model = _rt.get("model", _cfg.get("model", "AI"))
            return JSONResponse({"model": model})

        @rt("/api/upload", methods=["POST"])
        async def upload_api(req):
            """Accept one or more multipart files and store them on disk.

            Returns the server-side basenames the browser then references in
            its WebSocket ``message`` frame via the ``files`` field.  Files are
            written under ``$ANOTHERBOT_HOME/uploads`` with a UUID prefix so
            concurrent clients never collide.
            """
            from starlette.responses import JSONResponse, Response
            form = await req.form()
            uploads = [f for f in form.getlist("files") if getattr(f, "filename", None)]
            if not uploads:
                return Response("No files provided", status_code=400)

            self._upload_dir.mkdir(parents=True, exist_ok=True)
            saved: list[dict] = []
            total = 0
            try:
                for uf in uploads:
                    data = await uf.read()
                    total += len(data)
                    if total > self._MAX_UPLOAD_BYTES:
                        for s in saved:
                            (self._upload_dir / s["path"]).unlink(missing_ok=True)
                        limit_mb = self._MAX_UPLOAD_BYTES // (1024 * 1024)
                        return JSONResponse(
                            {"error": f"Upload exceeds {limit_mb} MB limit"},
                            status_code=413,
                        )
                    name = Path(uf.filename).name
                    stored = f"{uuid.uuid4().hex}_{name}"
                    (self._upload_dir / stored).write_bytes(data)
                    saved.append({"path": stored, "name": name})
            finally:
                for uf in uploads:
                    close = getattr(uf, "close", None)
                    if close:
                        await close()

            return JSONResponse({"files": saved})

        # Starlette WebSocket route (low-level, for multi-client management)
        async def _ws_endpoint(ws: WebSocket) -> None:
            await ws.accept()

            client_id = str(uuid.uuid4())
            log.info(f"WebSocket client connected: {client_id}")

            async with self._conn_lock:
                self._connections[client_id] = ws

            try:
                while True:
                    raw = await ws.receive_text()
                    if len(raw) > 65_536:
                        await ws.close(code=1009, reason="Message too large")
                        return
                    content, files = self._extract_message(raw)
                    if not content and not files:
                        continue

                    is_command = bool(content and content.startswith("/"))
                    if is_command:
                        cmd = content[1:].split(maxsplit=1)
                        name = cmd[0].lower() if cmd else ""
                        # /whoami is handled inline — it needs the per-connection client_id.
                        # All other commands (/help, /status, /stop, /new, /load, …) are
                        # forwarded to BackgroundAgent's CommandRegistry for consistency
                        # with the Telegram and Discord channels.
                        if name == "whoami":
                            await self._safe_send_json(
                                client_id,
                                {"type": "system", "content": f"Connection ID: {client_id}"},
                            )
                            continue

                    metadata = {
                        "websocket_id": client_id,
                        "is_command": is_command,
                    }
                    if files:
                        metadata["files"] = files

                    await self.mq.incoming.put(
                        IncomingMessage(
                            content=content or "",
                            channel=ChannelType.WEB,
                            metadata=metadata,
                        )
                    )
            except WebSocketDisconnect:
                log.info(f"WebSocket client disconnected: {client_id}")
            except Exception:
                log.exception(f"WebSocket error for client {client_id}")
            finally:
                async with self._conn_lock:
                    self._connections.pop(client_id, None)
                    self._send_locks.pop(client_id, None)

        # Mount the WebSocket route on the FastHTML (Starlette) app
        self._fasthtml_app.router.routes.insert(
            0, WebSocketRoute("/ws", _ws_endpoint)
        )

        # Serve static assets (CSS, JS)
        from starlette.routing import Mount
        from starlette.staticfiles import StaticFiles
        static_dir = Path(__file__).parent / "static"
        self._fasthtml_app.router.routes.insert(
            1, Mount("/static", StaticFiles(directory=str(static_dir)), name="static")
        )

    async def run_polling(self) -> None:
        """Start uvicorn and serve until cancelled."""
        config = uvicorn.Config(
            app=self._fasthtml_app,
            host=self.host,
            port=self.port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        log.info(f"Web UI at http://{self.host}:{self.port}/")
        log.info(f"WebSocket at ws://{self.host}:{self.port}/ws")
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
        content = self._extract_content(raw)
        files: list[str] = []
        stripped = raw.strip()
        if stripped:
            try:
                data = json.loads(stripped)
            except (json.JSONDecodeError, TypeError):
                data = None
            if isinstance(data, dict) and data.get("type") == "message":
                files = self._resolve_upload_paths(data.get("files"))
        return content, files

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
            except Exception:
                log.exception(f"Failed to send to WebSocket client {client_id}")
                async with self._conn_lock:
                    self._connections.pop(client_id, None)
                    self._send_locks.pop(client_id, None)
