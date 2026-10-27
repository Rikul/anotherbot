"""Web channel — a JSON HTTP API and WebSocket endpoint for the browser UI.

The UI itself is plain HTML/CSS/JS in the ``frontend/`` folder next to this
module, served as static files by the same uvicorn server. Everything dynamic
goes through these endpoints:

    GET  /api/conversations  — conversations from all channels + the active ID
    POST /api/conversations/<id>/rename — JSON ``{"name": ...}``
    GET  /api/conversations/<id>/export — the conversation as a JSON download
    GET  /api/messages       — messages of ``?conv_id=`` (with row IDs)
    GET  /api/messages/<id>/trajectory — a turn's trajectory as a JSON download
    GET  /api/status         — model in use and whether login is enabled
    GET  /api/commands       — slash commands, for the input's autocomplete
    POST /api/upload         — multipart attachments, returns stored names
    GET  /api/uploads/<name> — one stored upload (thumbnails, lightbox, downloads)
    WS   /ws                 — chat (JSON framing)

With ``WEB_PASSWORD`` set, ``WebAuthMiddleware`` adds ``POST /api/login`` and
``POST /api/logout`` (see ``web_auth``).

WebSocket message framing::

    → {"type": "message", "content": "...", "files": ["<stored name>", ...]}
    ← {"type": "message", "content": "...", "conversation_id": int?, "final": true?}
    ← {"type": "system", "content": "..."}
    ← {"type": "busy", "busy": bool, "stopped": bool, "conversation_id": int?}
    ← {"type": "trajectory", "message_id": int, "conversation_id": int}

Plain text is also accepted as a convenience. ``busy`` frames go to every tab
when an agent turn starts/ends; ``stopped`` is true when a ``/stop`` cut the
turn short. Everything an agent turn sends (thinking text, tool status lines,
the reply, an error, the ``trajectory`` frame) carries the turn's
``conversation_id`` and also goes to every tab, since they all share one
agent; ``final`` marks the turn's reply. Command replies (``system``) go only
to the tab that sent the command, and messages with no tab (scheduled tasks)
to every tab.

On connect a tab gets the last ``busy`` frame delivered and, if a turn is
running, the turn frames delivered since it started, so a reopened browser
sees the turn's progress so far and its remaining replies.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from .. import config
from ..core import runtime
from ..infra.conversations import ConversationStore
from .channel import Channel, ChannelType
from .commands import CommandRegistry, rename_conversation
from .message import IncomingMessage, OutgoingMessage
from .message_queue import MessageQueue
from .web_auth import WebAuthMiddleware, is_loopback_host


log = logging.getLogger(__name__)

# The browser UI (index.html, login.html, CSS, JS, vendored libs).
FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"

# Browsers (Edge especially) cache static assets aggressively; make them
# revalidate every time so a changed frontend file is picked up on reload.
# StaticFiles sends ETag/Last-Modified, so unchanged files cost only a 304.
_NO_CACHE = {"Cache-Control": "no-cache"}


# Uploads the browser may render inline (thumbnails, lightbox). Anything else,
# SVG and HTML included (they can carry script), is served as a download.
_INLINE_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
# Stored upload names are "<uuid4 hex>_<original name>".
_UPLOAD_PREFIX = re.compile(r"^[0-9a-f]{32}_")


class _FrontendFiles(StaticFiles):
    """``StaticFiles`` whose responses carry ``Cache-Control: no-cache``."""

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers.update(_NO_CACHE)
        return response


# --------------------------------------------------------------------------- #
# Channel class                                                                #
# --------------------------------------------------------------------------- #


class WebChannel(Channel):
    """Web channel.

    Serves the ``frontend/`` UI and its JSON API, and accepts WebSocket
    connections at ``WS /ws``. Multiple concurrent clients are supported —
    each gets a unique UUID stored in ``_connections``.
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
        # Whether an agent turn is running, and whether /stop was sent during it.
        self._turn_running = False
        self._stop_requested = False
        self._turn_conv_id: int | None = None
        # What the browsers have been sent (frames are delivered after the
        # agent queues them): the last busy frame and, while that says busy,
        # the turn's frames since. Replayed to a tab when it connects.
        self._shown_busy: dict = self._busy_frame(False)
        self._turn_frames: list[dict] = []
        self._connections: dict[str, WebSocket] = {}
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._conn_lock = asyncio.Lock()
        self._upload_dir = config.PROJECT_HOME / "uploads"
        # The agent's slash commands, set by bg_server; listed by /api/commands.
        self.commands: CommandRegistry | None = None
        # Built in start(): the Starlette app, and what uvicorn serves (the app,
        # wrapped in WebAuthMiddleware when a password is set).
        self._app: Starlette | None = None
        self._asgi_app = None
        mq.register(self, self.send_message)

    # Cap on a single multipart upload request (combined across files). The
    # agent enforces its own per-message limit when building the LLM payload.
    _MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

    # Turn frames kept for replay on connect (the oldest are dropped first).
    _MAX_TURN_FRAMES = 500

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
        """Build the Starlette app: API routes, WebSocket endpoint and frontend.

        Raises:
            RuntimeError: if the channel would listen on a non-loopback host
                without a password, or ``frontend/`` is missing.
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

        self._app = Starlette(
            routes=[
                Route("/api/conversations", self._conversations_api),
                Route(
                    "/api/conversations/{conv_id:int}/rename",
                    self._rename_api,
                    methods=["POST"],
                ),
                Route("/api/conversations/{conv_id:int}/export", self._export_api),
                Route("/api/messages", self._messages_api),
                Route(
                    "/api/messages/{message_id:int}/trajectory", self._trajectory_api
                ),
                Route("/api/status", self._status_api),
                Route("/api/commands", self._commands_api),
                Route("/api/upload", self._upload_api, methods=["POST"]),
                Route("/api/uploads/{name}", self._upload_file_api),
                WebSocketRoute("/ws", self._ws_endpoint),
                Route("/login", self._login_page),
                # Last: everything else is a frontend file ("/" → index.html)
                Mount("/", _FrontendFiles(directory=FRONTEND_DIR, html=True)),
            ]
        )
        self._asgi_app = (
            WebAuthMiddleware(self._app, self.password) if self.password else self._app
        )

    # -- HTTP routes ----------------------------------------------------------

    def _login_page(self, _req: Request):
        """Serve the login page (only reachable when a password is set)."""
        if not self.password:
            return Response(status_code=404)
        return FileResponse(FRONTEND_DIR / "login.html", headers=_NO_CACHE)

    def _conversations_api(self, _req: Request):
        """List conversations from all channels plus the web channel's active one."""
        ch = ChannelType.WEB.value
        convs = ConversationStore().list()
        active_id = runtime.get(f"conversation_id:{ch}")
        return JSONResponse({"conversations": convs, "active_id": active_id})

    async def _rename_api(self, req: Request):
        """Rename a conversation from any channel: ``{"name": ...}`` → ``{"id", "name"}``.

        Done here rather than via ``/rename`` so it doesn't queue behind a
        running agent turn or post a reply into the chat.
        """
        conv_id = req.path_params["conv_id"]
        try:
            data = await req.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            data = None
        name = data.get("name") if isinstance(data, dict) else None
        if not isinstance(name, str) or not name.strip():
            return JSONResponse(
                {"error": 'Expected JSON {"name": "<non-empty>"}'}, status_code=400
            )
        try:
            persisted = rename_conversation(ConversationStore(), conv_id, name)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        return JSONResponse({"id": conv_id, "name": persisted})

    def _export_api(self, req: Request):
        """Download a conversation and its messages as a JSON file.

        Unlike ``/export``, nothing is written on the server: the browser may
        be on another machine (e.g. the app runs in Docker).
        """
        conv_id = req.path_params["conv_id"]
        try:
            filename, payload = ConversationStore().export_payload(conv_id)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        # filename is "<id>-<[a-z0-9-] slug>.json", safe to put in a header as-is
        return JSONResponse(
            payload,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

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
        return JSONResponse({"messages": store.load_messages(conv_id, with_meta=True)})

    def _trajectory_api(self, req: Request):
        """Download one turn's trajectory (keyed by its final reply's message ID)."""
        message_id = req.path_params["message_id"]
        data = ConversationStore().get_trajectory(message_id)
        if data is None:
            return JSONResponse({"error": "No trajectory for this message"}, 404)
        conv_id = data.get("conversation_id")
        filename = (
            f"trajectory-{int(conv_id)}-{message_id}.json"
            if isinstance(conv_id, int)
            else f"trajectory-{message_id}.json"
        )
        return JSONResponse(
            data,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def _status_api(self, _req: Request):
        """Return the model currently in use and whether login is enabled."""
        model = runtime.get("model", config.get("model", "AI"))
        return JSONResponse({"model": model, "auth": bool(self.password)})

    def _commands_api(self, _req: Request):
        """Slash commands for autocomplete: the agent's, plus ``/whoami``.

        ``/whoami`` is answered by this channel, so it isn't in the registry.
        """
        cmds = [
            {"name": c.name, "description": c.description}
            for c in (self.commands.list() if self.commands else [])
        ]
        cmds.append({"name": "whoami", "description": "Show this tab's connection ID."})
        return JSONResponse({"commands": sorted(cmds, key=lambda c: c["name"])})

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

    def _upload_file_api(self, req: Request):
        """Serve one stored upload, so the UI can show attachments in history.

        Only files directly inside the upload dir are served (basename-only,
        like ``_resolve_upload_paths``). Uploads share the UI's origin and
        session, so ``nosniff`` and a sandbox CSP keep the browser from ever
        running one as a page; non-image files are forced downloads.
        """
        resolved = self._resolve_upload_paths([req.path_params["name"]])
        if not resolved:
            return Response(status_code=404)
        path = Path(resolved[0])
        headers = {
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            # stored names are unique, so the content never changes
            "Cache-Control": "private, max-age=31536000, immutable",
        }
        media_type = _INLINE_IMAGE_TYPES.get(path.suffix.lower())
        if media_type:
            return FileResponse(path, media_type=media_type, headers=headers)
        return FileResponse(
            path,
            media_type="application/octet-stream",
            filename=_UPLOAD_PREFIX.sub("", path.name),
            headers=headers,
        )

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

        # Hold this tab's send lock while catching it up, so frames delivered
        # meanwhile queue behind the replay. Registering the connection and
        # copying the replay under _conn_lock (send_message records frames
        # under it too) means each frame is either replayed or sent live,
        # never both or neither.
        lock = asyncio.Lock()
        async with lock:
            async with self._conn_lock:
                self._connections[client_id] = ws
                self._send_locks[client_id] = lock
                # "stopped" was news for the tabs open when the turn ended
                replay = [{**self._shown_busy, "stopped": False}, *self._turn_frames]
            try:
                for frame in replay:
                    await ws.send_json(frame)
            except Exception:  # pylint: disable=broad-exception-caught  # the receive loop below sees the disconnect
                log.info("WebSocket client %s left during catch-up", client_id)

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
        client ID, and ``/stop`` because the agent's queue is busy with the very
        turn it should stop (Telegram and Discord do the same). All other
        commands (/help, /status, /new, /load, …) are forwarded to
        BackgroundAgent's CommandRegistry.
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
            if cmd and cmd[0].lower() == "stop":
                await self._safe_send_json(
                    client_id, {"type": "system", "content": self._request_stop()}
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
        serve = asyncio.ensure_future(server.serve())
        try:
            await asyncio.shield(serve)
        except asyncio.CancelledError:
            # Shut uvicorn down cleanly (closes WebSockets, runs lifespan
            # shutdown) instead of cancelling it mid-request, e.g. on /restart.
            server.should_exit = True
            await serve
            raise

    # -- Message delivery ---------------------------------------------------

    def _request_stop(self) -> str:
        """Ask the running turn to stop; returns the reply for the requester.

        The agent loop checks the flag between steps, so the turn ends after
        the current model call / tool batch. When idle the flag is left alone.
        """
        if not self._turn_running:
            return "Nothing to stop."
        self.stopped = True
        self._stop_requested = True
        return "Stopping after the current step…"

    @staticmethod
    def _busy_frame(
        busy: bool, stopped: bool = False, conversation_id: int | None = None
    ) -> dict:
        frame = {"type": "busy", "busy": busy, "stopped": stopped}
        if conversation_id is not None:
            frame["conversation_id"] = conversation_id
        return frame

    async def on_agent_turn(self, running: bool) -> None:
        """Track the running turn and tell every tab (they share one agent).

        The frame goes through the outgoing queue, so it reaches the browser
        after the turn's replies rather than overtaking them. It names the
        turn's conversation: the agent's active one, which can't change
        until the turn ends (commands queue behind it).
        """
        stopped = not running and self._stop_requested
        if running:
            self._turn_conv_id = runtime.get(f"conversation_id:{ChannelType.WEB.value}")
        self._turn_running = running
        self._stop_requested = False
        await self.mq.outgoing_msg(
            OutgoingMessage(
                content="",
                channel=self,  # the queue routes by the registered channel object
                metadata={
                    "busy_frame": self._busy_frame(running, stopped, self._turn_conv_id)
                },
            )
        )

    async def on_trajectory_saved(
        self, message_id: int, conversation_id: int, metadata: dict
    ) -> None:
        """Tell the tabs the turn's reply can now be downloaded.

        Sent through the outgoing queue so it arrives after the reply itself.
        """
        await self.mq.outgoing_msg(
            OutgoingMessage(
                content="",
                channel=self,
                metadata={
                    **(metadata or {}),
                    "trajectory_frame": {
                        "type": "trajectory",
                        "message_id": message_id,
                        "conversation_id": conversation_id,
                    },
                },
            )
        )

    async def send_message(self, message: OutgoingMessage) -> None:
        meta = message.metadata or {}
        client_id = meta.get("websocket_id")
        is_command = meta.get("is_command", False)
        # Part of an agent turn: BackgroundAgent tags those with the conversation.
        in_turn = meta.get("conversation_id") is not None and not is_command
        if "busy_frame" in meta:
            payload = meta["busy_frame"]
        elif "trajectory_frame" in meta:
            payload = meta["trajectory_frame"]
        else:
            payload = {
                "type": "system" if is_command else "message",
                "content": message.content,
            }
            if in_turn:
                payload["conversation_id"] = meta["conversation_id"]
            if meta.get("final") and not is_command:
                payload["final"] = True
            if meta.get("not_started"):
                payload["not_started"] = True

        if client_id and not in_turn and "busy_frame" not in meta:
            await self._safe_send_json(client_id, payload)
            return
        # Busy and turn frames go to every tab (they share one agent), and so
        # does anything without a tab (e.g. a scheduled task's output).
        async with self._conn_lock:
            self._record(payload, in_turn)
            targets = list(self._connections.keys())
        for cid in targets:
            await self._safe_send_json(cid, payload)

    def _record(self, payload: dict, in_turn: bool) -> None:
        """Remember what a tab connecting now needs to catch up on."""
        if payload.get("type") == "busy":
            self._shown_busy = payload
            self._turn_frames = []
        elif in_turn and self._shown_busy.get("busy"):
            self._turn_frames.append(payload)
            del self._turn_frames[: -self._MAX_TURN_FRAMES]

    # -- Helpers ------------------------------------------------------------

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
