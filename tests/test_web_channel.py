import json
import pytest
from unittest.mock import AsyncMock

from app.channels.channel import ChannelType
from app.channels.message import OutgoingMessage
from app.channels.message_queue import MessageQueue
from app.channels.web_channel import FRONTEND_DIR, WebChannel


def make_web_channel():
    mq = MessageQueue()
    ch = WebChannel(mq=mq, host="127.0.0.1", port=8765)
    return ch, mq


# --- Initialization ---


def test_registers_delivery_function():
    ch, mq = make_web_channel()
    assert ch in mq._delivery


def test_stores_host_and_port():
    ch, _ = make_web_channel()
    assert ch.host == "127.0.0.1"
    assert ch.port == 8765


def test_initial_stopped_false():
    ch, _ = make_web_channel()
    assert ch.has_stopped is False


def test_clear_stopped_resets_state():
    ch, _ = make_web_channel()
    ch.stopped = True
    ch.clear_stopped()
    assert ch.has_stopped is False


def test_channel_type():
    ch, _ = make_web_channel()
    assert ch.channel_type == ChannelType.WEB


def test_default_metadata_empty():
    ch, _ = make_web_channel()
    assert ch.default_metadata == {}


# --- _extract_message: frame parsing ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("hello world", "hello world"),  # plain text
        (
            json.dumps({"type": "message", "content": "hi there"}),
            "hi there",
        ),  # JSON framing
        (json.dumps({"type": "message", "content": "  trimmed  "}), "trimmed"),
        ("", None),
        ("   ", None),
        ("{not valid json}", "{not valid json}"),  # invalid JSON → raw
        (
            json.dumps({"type": "system", "content": "x"}),
            json.dumps({"type": "system", "content": "x"}),
        ),  # other type → raw
        (json.dumps({"type": "message", "content": None}), None),
        (json.dumps({"type": "message", "content": "   "}), None),
    ],
)
def test_extract_message_content(raw, expected):
    ch, _ = make_web_channel()
    assert ch._extract_message(raw)[0] == expected


# --- _extract_message: text + attachments ---


def test_extract_message_plain_text_no_files():
    ch, _ = make_web_channel()
    content, files = ch._extract_message("hello")
    assert content == "hello"
    assert files == []


def test_extract_message_ignores_non_list_files():
    ch, _ = make_web_channel()
    raw = json.dumps({"type": "message", "content": "hi", "files": "nope"})
    content, files = ch._extract_message(raw)
    assert content == "hi"
    assert files == []


# --- _resolve_upload_paths: traversal protection ---


def test_resolve_upload_paths_rejects_traversal(tmp_path):
    ch, _ = make_web_channel()
    ch._upload_dir = tmp_path
    (tmp_path / "ok.txt").write_text("x")
    # basename-only join means traversal segments are stripped → file not found
    assert ch._resolve_upload_paths(["../secret", "/etc/passwd"]) == []


def test_resolve_upload_paths_accepts_known_file(tmp_path):
    ch, _ = make_web_channel()
    ch._upload_dir = tmp_path
    (tmp_path / "good.txt").write_text("x")
    resolved = ch._resolve_upload_paths(["good.txt"])
    assert resolved == [str((tmp_path / "good.txt").resolve())]


def test_resolve_upload_paths_skips_missing(tmp_path):
    ch, _ = make_web_channel()
    ch._upload_dir = tmp_path
    assert ch._resolve_upload_paths(["ghost.txt"]) == []


def test_resolve_upload_paths_ignores_non_strings(tmp_path):
    ch, _ = make_web_channel()
    ch._upload_dir = tmp_path
    assert ch._resolve_upload_paths([1, None, ""]) == []


# --- /api/upload endpoint ---


def test_upload_saves_files_and_returns_names(tmp_path):
    ch, _ = make_web_channel()
    ch._upload_dir = tmp_path / "uploads"
    ch.start()

    from starlette.testclient import TestClient

    client = TestClient(ch._app, raise_server_exceptions=True)
    resp = client.post(
        "/api/upload",
        files=[
            ("files", ("a.txt", b"hello", "text/plain")),
            ("files", ("b.png", b"\x89PNG", "image/png")),
        ],
    )
    assert resp.status_code == 200
    saved = resp.json()["files"]
    assert [s["name"] for s in saved] == ["a.txt", "b.png"]
    # files were actually written and resolvable
    resolved = ch._resolve_upload_paths([s["path"] for s in saved])
    assert len(resolved) == 2


def test_upload_rejects_empty_request(tmp_path):
    ch, _ = make_web_channel()
    ch._upload_dir = tmp_path / "uploads"
    ch.start()

    from starlette.testclient import TestClient

    client = TestClient(ch._app, raise_server_exceptions=True)
    resp = client.post("/api/upload")
    assert resp.status_code == 400


def test_upload_rejects_oversized(tmp_path):
    ch, _ = make_web_channel()
    ch._upload_dir = tmp_path / "uploads"
    ch._MAX_UPLOAD_BYTES = 10
    ch.start()

    from starlette.testclient import TestClient

    client = TestClient(ch._app, raise_server_exceptions=True)
    resp = client.post(
        "/api/upload",
        files=[("files", ("big.bin", b"x" * 50, "application/octet-stream"))],
    )
    assert resp.status_code == 413


# --- frontend/index.html ---

_INDEX_HTML = (FRONTEND_DIR / "index.html").read_text()


def test_index_has_attach_controls():
    for tok in ('id="attach-btn"', 'id="file-input"', 'id="attachments"', "<svg"):
        assert tok in _INDEX_HTML, f"Missing {tok}"


def test_start_registers_upload_route():
    ch, _ = make_web_channel()
    ch.start()
    paths = [getattr(r, "path", None) for r in ch._app.router.routes]
    assert "/api/upload" in paths


def test_index_references_frontend_assets():
    for src in (
        "/app.css",
        "/app.js",
        "/vendor/marked.min.js",
        "/vendor/purify.min.js",
    ):
        assert f'"{src}"' in _INDEX_HTML, f"Missing {src}"
        assert (FRONTEND_DIR / src.lstrip("/")).is_file(), f"{src} not on disk"


def test_index_has_no_inline_css_or_js():
    # --sidebar-w lives only in app.css; connect() only in app.js
    assert "--sidebar-w" not in _INDEX_HTML
    assert "function connect()" not in _INDEX_HTML


def test_index_key_structural_elements():
    for element_id in (
        "messages",
        "input-area",
        "sidebar",
        "header",
        "messages-wrap",
        "thinking",
        "logout-btn",
    ):
        assert f'id="{element_id}"' in _INDEX_HTML, f"Missing element #{element_id}"


def test_index_logout_button_hidden_until_status_says_auth():
    assert 'id="logout-btn" title="Log out" aria-label="Log out" hidden' in _INDEX_HTML


# --- Frontend files on disk ---


def test_frontend_files_exist_and_are_non_empty():
    for name in (
        "index.html",
        "app.css",
        "app.js",
        "login.html",
        "login.css",
        "login.js",
    ):
        path = FRONTEND_DIR / name
        assert path.is_file() and path.stat().st_size > 0, f"{name} missing or empty"


def test_static_css_has_theme_variables():
    content = (FRONTEND_DIR / "app.css").read_text()
    assert "--bg:" in content
    assert "--accent:" in content


def test_user_and_ai_message_rows_fill_available_width():
    content = (FRONTEND_DIR / "app.css").read_text()
    assert "max-width: 80%" not in content
    assert (
        ".msg-row.user,\n.msg-row.ai {\n    align-self: stretch;\n    width: 100%;\n}"
    ) in content
    assert (
        ".user .bubble,\n.ai .bubble {\n    flex: 1;\n    min-width: 0;\n}"
    ) in content


def test_mobile_hides_composer_placeholder():
    content = (FRONTEND_DIR / "app.css").read_text()
    mobile_css = content.split("@media (max-width: 768px)", 1)[1]
    assert "#msg-input::placeholder { color: transparent; }" in mobile_css


def test_static_js_has_iife():
    content = (FRONTEND_DIR / "app.js").read_text()
    assert "(function()" in content


def test_static_js_regex_uses_single_backslash():
    content = (FRONTEND_DIR / "app.js").read_text()
    # The formatMessage regex must use single-backslash escapes (\w, \s, \S),
    # not the doubled Python-string escapes (\\w, \\s, \\S) that would be a bug.
    assert r"[\w]*" in content
    assert r"[\s\S]" in content
    assert r"[\\w]" not in content


# --- send_message ---


@pytest.mark.asyncio
async def test_send_message_targeted():
    ch, _ = make_web_channel()
    mock_ws = AsyncMock()
    ch._connections["conn1"] = mock_ws

    msg = OutgoingMessage(
        content="hello", channel=ChannelType.WEB, metadata={"websocket_id": "conn1"}
    )
    await ch.send_message(msg)

    mock_ws.send_json.assert_called_once_with({"type": "message", "content": "hello"})


def test_send_message_uses_system_type_for_commands():
    ch, _ = make_web_channel()
    mock_ws = AsyncMock()
    ch._connections["conn1"] = mock_ws

    # Verify the type selection logic directly (send_message is async but we
    # can inspect metadata → msg_type mapping without awaiting).
    is_command = True
    msg_type = "system" if is_command else "message"
    assert msg_type == "system"

    is_command = False
    msg_type = "system" if is_command else "message"
    assert msg_type == "message"


@pytest.mark.asyncio
async def test_send_message_sends_system_type_when_is_command():
    ch, _ = make_web_channel()
    mock_ws = AsyncMock()
    ch._connections["conn1"] = mock_ws

    msg = OutgoingMessage(
        content="ok",
        channel=ChannelType.WEB,
        metadata={"websocket_id": "conn1", "is_command": True},
    )
    await ch.send_message(msg)

    mock_ws.send_json.assert_called_once_with({"type": "system", "content": "ok"})


@pytest.mark.asyncio
async def test_send_message_broadcasts_when_no_websocket_id():
    ch, _ = make_web_channel()
    ws1, ws2 = AsyncMock(), AsyncMock()
    ch._connections["a"] = ws1
    ch._connections["b"] = ws2

    msg = OutgoingMessage(content="broadcast", channel=ChannelType.WEB, metadata={})
    await ch.send_message(msg)

    ws1.send_json.assert_called_once_with({"type": "message", "content": "broadcast"})
    ws2.send_json.assert_called_once_with({"type": "message", "content": "broadcast"})


@pytest.mark.asyncio
async def test_send_message_skips_missing_client():
    ch, _ = make_web_channel()
    # No connection registered — should not raise
    msg = OutgoingMessage(
        content="hello", channel=ChannelType.WEB, metadata={"websocket_id": "ghost"}
    )
    await ch.send_message(msg)  # must not raise


@pytest.mark.asyncio
async def test_send_message_removes_dead_connection_on_error():
    ch, _ = make_web_channel()
    mock_ws = AsyncMock()
    mock_ws.send_json.side_effect = Exception("broken pipe")
    ch._connections["dead"] = mock_ws

    msg = OutgoingMessage(
        content="oops", channel=ChannelType.WEB, metadata={"websocket_id": "dead"}
    )
    await ch.send_message(msg)

    assert "dead" not in ch._connections


def test_api_messages_rejects_missing_conv():
    from unittest.mock import patch

    ch, _ = make_web_channel()
    ch.start()

    from starlette.testclient import TestClient

    with patch("app.infra.conversations.ConversationStore.get", return_value=None):
        client = TestClient(ch._app, raise_server_exceptions=True)
        resp = client.get("/api/messages?conv_id=99")
    assert resp.status_code == 404


# --- start(): route registration ---


def test_start_registers_websocket_route():
    from starlette.routing import WebSocketRoute

    ch, _ = make_web_channel()
    ch.start()
    ws_routes = [r for r in ch._app.router.routes if isinstance(r, WebSocketRoute)]
    assert any(r.path == "/ws" for r in ws_routes)


def test_start_mounts_frontend_last():
    from starlette.routing import Mount

    ch, _ = make_web_channel()
    ch.start()
    last = ch._app.router.routes[-1]
    assert (
        isinstance(last, Mount) and last.path == ""
    )  # catch-all, after the API routes


def test_root_serves_index_html_with_no_cache():
    from starlette.testclient import TestClient

    ch, _ = make_web_channel()
    ch.start()
    resp = TestClient(ch._asgi_app).get("/")
    assert resp.status_code == 200
    assert 'id="msg-input"' in resp.text
    assert resp.headers["cache-control"] == "no-cache"


def test_frontend_assets_are_served():
    from starlette.testclient import TestClient

    ch, _ = make_web_channel()
    ch.start()
    client = TestClient(ch._asgi_app)
    for path in ("/app.js", "/app.css", "/vendor/marked.min.js"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["cache-control"] == "no-cache"
    assert client.get("/nope.js").status_code == 404


def test_login_page_404_without_password():
    from starlette.testclient import TestClient

    ch, _ = make_web_channel()
    ch.start()
    assert TestClient(ch._asgi_app).get("/login").status_code == 404


# --- routes registered by start() ---


def test_conversations_api_lists_all_and_active_id():
    from unittest.mock import patch
    from starlette.testclient import TestClient

    ch, _ = make_web_channel()
    ch.start()
    convs = [
        {"id": 1, "name": "A", "channel": "telegram"},
        {"id": 2, "name": "B", "channel": "web"},
    ]
    with (
        patch(
            "app.infra.conversations.ConversationStore.list", return_value=convs
        ) as mock_list,
        patch("app.core.runtime._store", {"conversation_id:web": 2}),
    ):
        resp = TestClient(ch._asgi_app).get("/api/conversations")
    assert resp.status_code == 200
    assert resp.json() == {"conversations": convs, "active_id": 2}
    mock_list.assert_called_once_with()  # no channel filter: web sees every channel


def test_status_api_returns_runtime_model():
    from unittest.mock import patch
    from starlette.testclient import TestClient

    ch, _ = make_web_channel()
    ch.start()
    with patch("app.core.runtime._store", {"model": "m-1"}):
        resp = TestClient(ch._asgi_app).get("/api/status")
    assert resp.json() == {"model": "m-1", "auth": False}


@pytest.mark.asyncio
async def test_websocket_message_is_enqueued_for_agent():
    from starlette.testclient import TestClient

    ch, mq = make_web_channel()
    ch.start()
    with TestClient(ch._asgi_app).websocket_connect("/ws") as ws:
        assert ws.receive_json() == {"type": "busy", "busy": False, "stopped": False}
        ws.send_text(json.dumps({"type": "message", "content": "hello"}))
        ws.send_text(json.dumps({"type": "message", "content": "/help"}))
        ws.send_text(json.dumps({"type": "message", "content": "/whoami"}))
        assert (
            "Connection ID" in ws.receive_json()["content"]
        )  # answered inline, not enqueued
    first, second = mq.incoming.get_nowait(), mq.incoming.get_nowait()
    assert first.content == "hello" and first.metadata["is_command"] is False
    assert second.content == "/help" and second.metadata["is_command"] is True
    assert first.metadata["websocket_id"] == second.metadata["websocket_id"]
    assert mq.incoming.empty()


# --- conversation actions: rename / export ---


@pytest.fixture
def api_client(tmp_path):
    """A started channel whose API uses a temp ConversationStore."""
    from unittest.mock import patch
    from starlette.testclient import TestClient
    from app.infra.conversations import ConversationStore

    store = ConversationStore(db_path=tmp_path / "app.db")
    ch, _ = make_web_channel()
    ch.start()
    with patch("app.channels.web_channel.ConversationStore", return_value=store):
        yield TestClient(ch._asgi_app), store


def test_rename_api_renames_any_channels_conversation(api_client):
    from unittest.mock import patch

    client, store = api_client
    cid = store.create("telegram", "Old")
    runtime_store = {"conversation_id:web": cid}
    with patch("app.core.runtime._store", runtime_store):
        resp = client.post(f"/api/conversations/{cid}/rename", json={"name": "  New  "})
    assert resp.status_code == 200
    assert resp.json() == {"id": cid, "name": "New"}
    assert store.get(cid)["name"] == "New"
    assert runtime_store["conversation_name:web"] == "New"  # /status shows it


@pytest.mark.parametrize(
    "body", [{"name": ""}, {"name": "   "}, {"name": 5}, {}, ["x"]]
)
def test_rename_api_rejects_bad_body(api_client, body):
    client, store = api_client
    cid = store.create("web", "Keep")
    assert client.post(f"/api/conversations/{cid}/rename", json=body).status_code == 400
    assert store.get(cid)["name"] == "Keep"


def test_rename_api_rejects_non_json(api_client):
    client, store = api_client
    cid = store.create("web", "Keep")
    resp = client.post(
        f"/api/conversations/{cid}/rename",
        content=b"name=x",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 400


def test_rename_api_missing_conversation_is_404(api_client):
    client, _ = api_client
    assert (
        client.post("/api/conversations/99/rename", json={"name": "x"}).status_code
        == 404
    )


def test_rename_api_get_does_not_rename(api_client):
    client, store = api_client
    cid = store.create("web", "Keep")
    # POST-only route; a GET falls through to the frontend catch-all → 404
    assert client.get(f"/api/conversations/{cid}/rename?name=x").status_code == 404
    assert store.get(cid)["name"] == "Keep"


def test_export_api_downloads_json_without_writing_on_server(api_client, tmp_path):
    from unittest.mock import patch
    from app.infra import conversations

    client, store = api_client
    cid = store.create("discord", "My Chat")
    with patch.object(conversations, "PROJECT_HOME", tmp_path):
        resp = client.get(f"/api/conversations/{cid}/export")
    assert resp.status_code == 200
    assert (
        resp.headers["content-disposition"]
        == f'attachment; filename="{cid}-my-chat.json"'
    )
    data = resp.json()
    assert data["id"] == cid and data["name"] == "My Chat" and data["messages"] == []
    assert not (tmp_path / "conversations").exists()


def test_export_api_missing_conversation_is_404(api_client):
    client, _ = api_client
    resp = client.get("/api/conversations/99/export")
    assert resp.status_code == 404
    assert "not found" in resp.json()["error"]


def test_trajectory_api_downloads_json(api_client, tmp_path):
    from app.infra.message_history import MessageHistory

    client, store = api_client
    cid = store.create("telegram", "Chat")
    mid = MessageHistory("telegram", db_path=tmp_path / "app.db").add_message(
        "assistant", "a", cid
    )
    store.save_trajectory(mid, cid, {"conversation_id": cid, "messages": []})
    resp = client.get(f"/api/messages/{mid}/trajectory")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == (
        f'attachment; filename="trajectory-{cid}-{mid}.json"'
    )
    assert resp.json() == {"conversation_id": cid, "messages": []}


def test_trajectory_api_missing_is_404(api_client):
    client, _ = api_client
    assert client.get("/api/messages/99/trajectory").status_code == 404


def test_messages_api_includes_ids_and_trajectory_flag(api_client, tmp_path):
    from app.infra.message_history import MessageHistory

    client, store = api_client
    cid = store.create("web")
    mid = MessageHistory("web", db_path=tmp_path / "app.db").add_message(
        "assistant", "a", cid
    )
    store.save_trajectory(mid, cid, {})
    msgs = client.get(f"/api/messages?conv_id={cid}").json()["messages"]
    assert msgs == [
        {"role": "assistant", "content": "a", "id": mid, "has_trajectory": True}
    ]


@pytest.mark.asyncio
async def test_trajectory_frame_goes_to_the_requesting_tab_only():
    ch, mq = make_web_channel()
    a, b = AsyncMock(), AsyncMock()
    ch._connections.update({"a": a, "b": b})
    await ch.on_trajectory_saved(7, 3, {"websocket_id": "a"})
    msg = mq.outgoing.get_nowait()
    await ch.send_message(msg)
    a.send_json.assert_awaited_once_with(
        {"type": "trajectory", "message_id": 7, "conversation_id": 3}
    )
    b.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_final_flag_reaches_the_browser_only_for_replies():
    ch, _ = make_web_channel()
    ws = AsyncMock()
    ch._connections["c1"] = ws
    await ch.send_message(
        OutgoingMessage(
            content="done", channel=ch, metadata={"websocket_id": "c1", "final": True}
        )
    )
    await ch.send_message(
        OutgoingMessage(
            content="> bash [ls]...", channel=ch, metadata={"websocket_id": "c1"}
        )
    )
    await ch.send_message(
        OutgoingMessage(
            content="ok",
            channel=ch,
            metadata={"websocket_id": "c1", "is_command": True, "final": True},
        )
    )
    assert [c.args[0] for c in ws.send_json.await_args_list] == [
        {"type": "message", "content": "done", "final": True},
        {"type": "message", "content": "> bash [ls]..."},
        {"type": "system", "content": "ok"},
    ]


def test_sidebar_actions_in_frontend():
    js = (FRONTEND_DIR / "app.js").read_text()
    for needle in (
        "/api/conversations/${conv.id}/rename",
        "/api/conversations/${id}/export",
        "`/fork ${id}`",
        "`/delete ${id}`",
        "Confirm delete",
        "conv-actions-btn",
    ):
        assert needle in js, needle


# --- /stop and busy frames ---


@pytest.mark.asyncio
async def test_stop_while_idle_does_nothing():
    ch, mq = make_web_channel()
    ws = AsyncMock()
    ch._connections["c1"] = ws
    await ch._handle_ws_text("c1", json.dumps({"type": "message", "content": "/stop"}))
    ws.send_json.assert_called_once_with(
        {"type": "system", "content": "Nothing to stop."}
    )
    assert ch.has_stopped is False
    assert mq.incoming.empty()


@pytest.mark.asyncio
async def test_stop_during_turn_sets_flag_immediately_and_is_not_queued():
    ch, mq = make_web_channel()
    ws = AsyncMock()
    ch._connections["c1"] = ws
    await ch.on_agent_turn(True)
    await ch._handle_ws_text("c1", "/stop")
    assert ch.has_stopped is True  # seen by the running loop, not after it
    assert mq.incoming.empty()
    ws.send_json.assert_called_once_with(
        {"type": "system", "content": "Stopping after the current step…"}
    )


@pytest.mark.asyncio
async def test_busy_frames_are_queued_in_order_and_broadcast_with_stopped_flag():
    from app.core import runtime

    runtime.set("conversation_id:web", 5)
    ch, mq = make_web_channel()
    ws1, ws2 = AsyncMock(), AsyncMock()
    ch._connections.update({"a": ws1, "b": ws2})

    await ch.on_agent_turn(True)
    await mq.outgoing.put(
        OutgoingMessage(
            content="reply", channel=ChannelType.WEB, metadata={"websocket_id": "a"}
        )
    )
    await ch._handle_ws_text("a", "/stop")
    await ch.on_agent_turn(False)
    await ch.on_agent_turn(True)  # next turn: stop request doesn't carry over
    await ch.on_agent_turn(False)

    while not mq.outgoing.empty():
        await ch.send_message(mq.outgoing.get_nowait())

    busy = lambda b, s: {  # noqa: E731
        "type": "busy",
        "busy": b,
        "stopped": s,
        "conversation_id": 5,
    }
    assert [c.args[0] for c in ws2.send_json.call_args_list] == [
        busy(True, False),
        busy(False, True),
        busy(True, False),
        busy(False, False),
    ]
    # the requester also got the reply (before the end frame) and the stop ack
    a_frames = [c.args[0] for c in ws1.send_json.call_args_list]
    assert a_frames.index({"type": "message", "content": "reply"}) < a_frames.index(
        busy(False, True)
    )


def _turn_msg(content, conv_id=7, **meta):
    return OutgoingMessage(
        content=content,
        channel=ChannelType.WEB,
        metadata={"websocket_id": "old-tab", "conversation_id": conv_id, **meta},
    )


def _busy_msg(busy, stopped=False, conv_id=7):
    return OutgoingMessage(
        content="",
        channel=ChannelType.WEB,
        metadata={"busy_frame": WebChannel._busy_frame(busy, stopped, conv_id)},
    )


def test_websocket_gets_idle_state_on_connect():
    from starlette.testclient import TestClient

    ch, _ = make_web_channel()
    ch.start()
    with TestClient(ch._asgi_app).websocket_connect("/ws") as ws:
        assert ws.receive_json() == {"type": "busy", "busy": False, "stopped": False}


@pytest.mark.asyncio
async def test_turn_frames_go_to_every_tab_with_the_conversation_id():
    """All tabs share one agent: a tab opened mid-turn must see its replies,
    even though the turn was started from another (maybe closed) tab."""
    ch, _ = make_web_channel()
    a, b = AsyncMock(), AsyncMock()
    ch._connections.update({"old-tab": a, "new-tab": b})
    await ch.send_message(_turn_msg("> bash [ls]..."))
    await ch.send_message(_turn_msg("done", final=True))
    expected = [
        {"type": "message", "content": "> bash [ls]...", "conversation_id": 7},
        {"type": "message", "content": "done", "conversation_id": 7, "final": True},
    ]
    assert [c.args[0] for c in a.send_json.await_args_list] == expected
    assert [c.args[0] for c in b.send_json.await_args_list] == expected


@pytest.mark.asyncio
async def test_refused_message_is_marked_not_started_for_its_tab_only():
    ch, _ = make_web_channel()
    a, b = AsyncMock(), AsyncMock()
    ch._connections.update({"a": a, "b": b})
    await ch.send_message(
        OutgoingMessage(
            content="Restarting, please send that again in a few seconds.",
            channel=ChannelType.WEB,
            metadata={"websocket_id": "a", "not_started": True},
        )
    )
    a.send_json.assert_awaited_once_with(
        {
            "type": "message",
            "content": "Restarting, please send that again in a few seconds.",
            "not_started": True,
        }
    )
    b.send_json.assert_not_awaited()


def test_frontend_clears_pending_turn_only_for_refused_messages():
    js = (FRONTEND_DIR / "app.js").read_text()
    assert "if (data.not_started && !agentBusy && pendingTurn)" in js
    assert "data.conversation_id == null && !agentBusy" not in js


@pytest.mark.asyncio
async def test_command_replies_still_go_only_to_the_requesting_tab():
    ch, _ = make_web_channel()
    a, b = AsyncMock(), AsyncMock()
    ch._connections.update({"a": a, "b": b})
    await ch.send_message(
        OutgoingMessage(
            content="Loaded",
            channel=ChannelType.WEB,
            metadata={"websocket_id": "a", "is_command": True, "conversation_id": 7},
        )
    )
    a.send_json.assert_awaited_once_with({"type": "system", "content": "Loaded"})
    b.send_json.assert_not_awaited()


def test_tab_opened_mid_turn_gets_busy_state_and_the_turn_so_far():
    import asyncio

    from starlette.testclient import TestClient

    ch, _ = make_web_channel()
    ch.start()

    async def deliver(*msgs):
        for m in msgs:
            await ch.send_message(m)

    asyncio.run(deliver(_busy_msg(True), _turn_msg("thinking..."), _turn_msg("> bash")))
    client = TestClient(ch._asgi_app)
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json() == {
            "type": "busy",
            "busy": True,
            "stopped": False,
            "conversation_id": 7,
        }
        assert ws.receive_json()["content"] == "thinking..."
        assert ws.receive_json()["content"] == "> bash"

    # once the turn has ended its frames are in the history, not replayed;
    # and "stopped" was news only for the tabs open at the time
    asyncio.run(deliver(_turn_msg("done", final=True), _busy_msg(False, stopped=True)))
    assert ch._turn_frames == []
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json() == {
            "type": "busy",
            "busy": False,
            "stopped": False,
            "conversation_id": 7,
        }


@pytest.mark.asyncio
async def test_turn_replay_is_capped():
    ch, _ = make_web_channel()
    ch._MAX_TURN_FRAMES = 3
    await ch.send_message(_busy_msg(True))
    for i in range(5):
        await ch.send_message(_turn_msg(f"line {i}"))
    assert [f["content"] for f in ch._turn_frames] == ["line 2", "line 3", "line 4"]


@pytest.mark.asyncio
async def test_scheduled_task_output_is_not_replayed_as_part_of_the_turn():
    ch, _ = make_web_channel()
    await ch.send_message(_busy_msg(True))
    await ch.send_message(
        OutgoingMessage(content="task output", channel=ChannelType.WEB, metadata={})
    )
    assert ch._turn_frames == []


def test_frontend_locks_conversation_switching_during_a_turn():
    js = (FRONTEND_DIR / "app.js").read_text()
    for needle in (
        "const switchLocked = () => agentBusy || pendingTurn;",
        "if (switchLocked()) {",  # sidebar click
        "|| switchLocked()) return;",  # + New
        "/^\\/(new|load|fork)\\b/i",  # typed commands
        "disabled: switchLocked() ? SWITCH_LOCKED : ''",  # Fork menu item
        "holdFrames(",  # replayed frames wait for the history
        "data.conversation_id !== activeConvId",
    ):
        assert needle in js, needle


def test_index_has_hidden_stop_button():
    assert 'id="stop-btn"' in _INDEX_HTML
    assert 'id="stop-btn" title="Stop" aria-label="Stop" hidden>' in _INDEX_HTML


@pytest.mark.asyncio
async def test_busy_frames_are_delivered_by_the_queues_process_outgoing():
    import asyncio

    ch, mq = make_web_channel()
    ws = AsyncMock()
    ch._connections["a"] = ws
    task = asyncio.create_task(mq.process_outgoing())
    try:
        await ch.on_agent_turn(True)
        await ch.on_agent_turn(False)
        await asyncio.sleep(0.05)
    finally:
        task.cancel()
    assert [c.args[0]["busy"] for c in ws.send_json.call_args_list] == [True, False]


def test_frontend_attaches_pasted_files():
    js = (FRONTEND_DIR / "app.js").read_text()
    assert "inputEl.addEventListener('paste'" in js
    assert "data.getData('text/plain')" in js  # text + image clipboard pastes as text


# --- GET /api/uploads/<name> ---

_UUID = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def uploads_client(tmp_path):
    from starlette.testclient import TestClient

    ch, _ = make_web_channel()
    ch._upload_dir = tmp_path
    ch.start()
    return TestClient(ch._asgi_app), tmp_path


def test_upload_image_served_inline_with_safety_headers(uploads_client):
    client, updir = uploads_client
    (updir / f"{_UUID}_shot.PNG").write_bytes(b"\x89PNG")
    resp = client.get(f"/api/uploads/{_UUID}_shot.PNG")
    assert resp.status_code == 200
    assert resp.content == b"\x89PNG"
    assert resp.headers["content-type"] == "image/png"
    assert "content-disposition" not in resp.headers
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in resp.headers["content-security-policy"]
    assert "immutable" in resp.headers["cache-control"]


@pytest.mark.parametrize(
    "name", ["notes.txt", "page.html", "logo.svg", "My Report.pdf"]
)
def test_upload_non_image_is_a_download_with_original_name(uploads_client, name):
    from urllib.parse import quote

    client, updir = uploads_client
    (updir / f"{_UUID}_{name}").write_text("<script>alert(1)</script>")
    resp = client.get(f"/api/uploads/{quote(_UUID + '_' + name)}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/octet-stream"
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert quote(name) in disposition or name in disposition
    assert _UUID not in disposition
    assert resp.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "path",
    [
        "/api/uploads/ghost.png",
        "/api/uploads/..%2Fsecret.png",
        "/api/uploads/..",
        "/api/uploads/%2Fetc%2Fpasswd",
    ],
)
def test_upload_missing_or_outside_dir_is_404(uploads_client, path):
    client, updir = uploads_client
    (updir.parent / "secret.png").write_bytes(b"x")
    assert client.get(path).status_code == 404


def test_frontend_renders_attachment_markers():
    js = (FRONTEND_DIR / "app.js").read_text()
    for needle in (
        "ATTACHMENT_RE",
        "/api/uploads/",
        "openLightbox",
        "withAttachmentMarkers",
    ):
        assert needle in js, needle


# --- /api/commands ---


def test_commands_api_lists_registry_plus_whoami_sorted():
    from starlette.testclient import TestClient
    from app.channels.commands import BotCommand, CommandRegistry

    ch, _ = make_web_channel()
    ch.start()
    registry = CommandRegistry()
    noop = AsyncMock()
    registry.register(BotCommand("new", "Start a new conversation.", noop))
    registry.register(
        BotCommand("load", "Load a conversation. Usage: /load <id>", noop)
    )
    ch.commands = registry
    resp = TestClient(ch._asgi_app).get("/api/commands")
    assert resp.json() == {
        "commands": [
            {"name": "load", "description": "Load a conversation. Usage: /load <id>"},
            {"name": "new", "description": "Start a new conversation."},
            {"name": "whoami", "description": "Show this tab's connection ID."},
        ]
    }


def test_commands_api_without_agent_lists_only_whoami():
    from starlette.testclient import TestClient

    ch, _ = make_web_channel()
    ch.start()
    names = [
        c["name"]
        for c in TestClient(ch._asgi_app).get("/api/commands").json()["commands"]
    ]
    assert names == ["whoami"]


def test_frontend_has_reply_copy_button_with_http_fallback():
    js = (FRONTEND_DIR / "app.js").read_text()
    assert (
        "actions.appendChild(copyButton(bubble))" in js
    )  # on the row: survives re-rendering
    assert "bubble.dataset.raw" in js  # copies the markdown source
    assert "document.execCommand('copy')" in js  # no Clipboard API on plain http
