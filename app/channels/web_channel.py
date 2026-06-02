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
    Button, Div, Head, Html, Link, Meta, Script, Span,
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
                        # Input area
                        Div(
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
        mq.register(self, self.send_message)

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
                    content = self._extract_content(raw)
                    if not content:
                        continue

                    if content.startswith("/"):
                        cmd = content[1:].split(maxsplit=1)
                        if not cmd:
                            continue
                        name = cmd[0].lower()
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

                    await self.mq.incoming.put(
                        IncomingMessage(
                            content=content,
                            channel=ChannelType.WEB,
                            metadata={
                                "websocket_id": client_id,
                                "is_command": content.startswith("/"),
                            },
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
