"""WebSocket channel for FastAPI-based real-time agent communication.

Connects browser or programmatic WebSocket clients to the background agent.
Each connection gets a UUID, and responses are routed back to the correct
client via metadata.

Authentication: optional ``api_key`` query parameter checked on connect.
Rejects mismatched keys with WebSocket close code 4001.

Message framing is JSON in both directions::

    {"type": "message", "content": "Your prompt or response here"}

Raw text is also accepted on input as a convenience for simple clients
(e.g. ``websocat``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

import uvicorn
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect

from .channel import Channel, ChannelType
from .message import IncomingMessage, OutgoingMessage
from .message_queue import MessageQueue

log = logging.getLogger(__name__)

MAX_WS_MESSAGE_LENGTH = 4096  # max chars per outgoing WebSocket chunk


class WebSocketChannel(Channel):
    """FastAPI + WebSocket channel for the background agent.

    Multiple concurrent clients are supported — each gets a unique UUID
    stored in ``_connections``. Responses are routed to the correct
    client by matching ``metadata["websocket_id"]``.
    """

    def __init__(
        self,
        mq: MessageQueue,
        host: str = "127.0.0.1",
        port: int = 8765,
        api_key: str | None = None,
    ) -> None:
        self.mq = mq
        self.host = host
        self.port = port
        self.api_key = api_key
        self.stopped: bool = False
        self._connections: dict[str, WebSocket] = {}
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
        # No single default — WebSocket clients are ephemeral.
        return {}

    async def error_handler(self, update: object, context: object) -> None:
        log.error(f"WebSocket error: {context}")

    async def process_message(self, message: object) -> None:
        # WebSocket messages are handled inline in the endpoint handler.
        pass

    # -- Public API ---------------------------------------------------------

    def start(self) -> None:
        """Build the FastAPI app but do *not* run it yet.

        ``run_polling()`` starts uvicorn and awaits it until shutdown.
        """
        log.info(f"Building WebSocket channel on {self.host}:{self.port}")

        self._app = FastAPI(title="CraftersCode WebSocket Channel")

        @self._app.websocket("/ws")
        async def websocket_endpoint(
            ws: WebSocket,
            api_key: str = Query(default=""),
        ) -> None:
            # Always accept first so close codes are delivered in-band.
            await ws.accept()

            # Auth: reject if api_key is configured and doesn't match
            if self.api_key and api_key != self.api_key:
                await ws.close(code=4001, reason="Unauthorized — bad api_key")
                log.warning("WebSocket connection rejected: invalid api_key")
                return

            client_id = str(uuid.uuid4())
            log.info(f"WebSocket client connected: {client_id}")

            async with self._conn_lock:
                self._connections[client_id] = ws

            try:
                while True:
                    raw = await ws.receive_text()
                    content = self._extract_content(raw)
                    if not content:
                        continue

                    # Check for local commands
                    if content.startswith("/"):
                        cmd_parts = content[1:].split(maxsplit=1)
                        if not cmd_parts:
                            continue
                        cmd_name = cmd_parts[0].lower()

                        if cmd_name == "whoami":
                            await self._safe_send_json(
                                client_id,
                                {"type": "message", "content": f"Your connection ID is {client_id}."},
                            )
                            continue

                        if cmd_name == "stop":
                            self.stopped = True
                            await self._safe_send_json(
                                client_id,
                                {"type": "message", "content": "Stopped."},
                            )
                            continue

                    # Forward to agent
                    await self.mq.incoming.put(
                        IncomingMessage(
                            content=content,
                            channel=ChannelType.WEB,
                            metadata={"websocket_id": client_id},
                        )
                    )
            except WebSocketDisconnect:
                log.info(f"WebSocket client disconnected: {client_id}")
            except Exception:
                log.exception(f"WebSocket error for client {client_id}")
            finally:
                async with self._conn_lock:
                    self._connections.pop(client_id, None)

    async def run_polling(self) -> None:
        """Start uvicorn and await until shutdown.

        Blocks on ``server.serve()``, which runs until cancelled or the
        server is stopped externally.  This mirrors the Telegram channel's
        ``run_polling()`` pattern where the event loop is kept alive by the
        underlying server's own event loop.
        """
        config = uvicorn.Config(
            app=self._app,
            host=self.host,
            port=self.port,
            log_level="info",
        )
        server = uvicorn.Server(config)

        log.info(f"WebSocket server listening on ws://{self.host}:{self.port}/ws")
        await server.serve()

    # -- Message delivery ---------------------------------------------------

    async def send_message(self, message: OutgoingMessage) -> None:
        """Deliver an outgoing message to the correct WebSocket client.

        Looks up ``websocket_id`` in ``message.metadata`` and sends JSON
        over that connection. Skips silently if the client has disconnected.
        """
        client_id = message.metadata.get("websocket_id")
        if not client_id:
            log.error("Cannot send WebSocket message: no websocket_id in metadata")
            return

        await self._safe_send_json(client_id, {"type": "message", "content": message.content})

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _extract_content(raw: str) -> str | None:
        """Extract message content from a raw WebSocket text frame.

        Tries JSON decoding first (matching the documented protocol).
        Falls back to treating the raw string as plain text for simple
        clients such as ``websocat``.  Returns ``None`` for empty payloads.
        """
        raw = raw.strip()
        if not raw:
            return None

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            # Plain text fallback
            return raw

        # JSON framing: {"type": "message", "content": "..."}
        if isinstance(data, dict) and data.get("type") == "message":
            content = data.get("content", "")
            return content.strip() or None

        # Unknown JSON shape — forward raw as-is
        return raw

    async def _safe_send_json(self, client_id: str, payload: dict) -> None:
        """Send a JSON payload to a WebSocket client, handling disconnects.

        Looks up the client under lock, then sends outside the lock to
        avoid blocking other connection operations. Removes the client
        from ``_connections`` on any send failure.
        """
        async with self._conn_lock:
            ws = self._connections.get(client_id)

        if ws is None:
            return

        try:
            # Split long messages like other channels do
            text = payload.get("content", "")
            if len(text) > MAX_WS_MESSAGE_LENGTH:
                for i in range(0, len(text), MAX_WS_MESSAGE_LENGTH):
                    chunk = text[i : i + MAX_WS_MESSAGE_LENGTH]
                    await ws.send_json({"type": "message", "content": chunk})
            else:
                await ws.send_json(payload)
        except Exception:
            log.exception(f"Failed to send to WebSocket client {client_id}")
            async with self._conn_lock:
                self._connections.pop(client_id, None)
