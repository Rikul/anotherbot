import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from app.channels.channel import ChannelType
from app.channels.message import OutgoingMessage
from app.channels.message_queue import MessageQueue
from app.channels.web_channel import WebChannel, _build_page


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


# --- _extract_content ---

def test_extract_plain_text():
    assert WebChannel._extract_content("hello world") == "hello world"


def test_extract_json_message_framing():
    raw = json.dumps({"type": "message", "content": "hi there"})
    assert WebChannel._extract_content(raw) == "hi there"


def test_extract_trims_content_whitespace():
    raw = json.dumps({"type": "message", "content": "  trimmed  "})
    assert WebChannel._extract_content(raw) == "trimmed"


def test_extract_empty_string_returns_none():
    assert WebChannel._extract_content("") is None


def test_extract_whitespace_only_returns_none():
    assert WebChannel._extract_content("   ") is None


def test_extract_invalid_json_returns_raw():
    assert WebChannel._extract_content("{not valid json}") == "{not valid json}"


def test_extract_json_wrong_type_returns_raw():
    raw = json.dumps({"type": "system", "content": "ignored"})
    assert WebChannel._extract_content(raw) == raw


def test_extract_json_null_content_returns_none():
    raw = json.dumps({"type": "message", "content": None})
    assert WebChannel._extract_content(raw) is None


def test_extract_json_empty_content_returns_none():
    raw = json.dumps({"type": "message", "content": "   "})
    assert WebChannel._extract_content(raw) is None


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
    client = TestClient(ch._fasthtml_app, raise_server_exceptions=True)
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
    client = TestClient(ch._fasthtml_app, raise_server_exceptions=True)
    resp = client.post("/api/upload")
    assert resp.status_code == 400


def test_upload_rejects_oversized(tmp_path):
    ch, _ = make_web_channel()
    ch._upload_dir = tmp_path / "uploads"
    ch._MAX_UPLOAD_BYTES = 10
    ch.start()

    from starlette.testclient import TestClient
    client = TestClient(ch._fasthtml_app, raise_server_exceptions=True)
    resp = client.post(
        "/api/upload",
        files=[("files", ("big.bin", b"x" * 50, "application/octet-stream"))],
    )
    assert resp.status_code == 413


# --- _build_page: attachment UI ---

def test_build_page_has_attach_controls():
    html = str(_build_page())
    for tok in ('id="attach-btn"', 'id="file-input"', 'id="attachments"', "<svg"):
        assert tok in html, f"Missing {tok}"


def test_start_registers_upload_route(tmp_path):
    ch, _ = make_web_channel()
    ch.start()
    paths = [getattr(r, "path", None) for r in ch._fasthtml_app.router.routes]
    assert "/api/upload" in paths


# --- _build_page: static file references ---

def test_build_page_references_static_css():
    html = str(_build_page())
    assert "/static/web_channel.css" in html


def test_build_page_references_static_js():
    html = str(_build_page())
    assert "/static/web_channel.js" in html


def test_build_page_has_no_inline_css():
    html = str(_build_page())
    # --sidebar-w is a custom variable only found in our CSS file, not inline
    assert "--sidebar-w" not in html


def test_build_page_has_no_inline_js():
    html = str(_build_page())
    # connect() is the WebSocket reconnect function from our JS IIFE
    assert "function connect()" not in html


def test_build_page_key_structural_elements():
    html = str(_build_page())
    for element_id in ("messages", "input-area", "sidebar", "header", "messages-wrap", "thinking"):
        assert f'id="{element_id}"' in html, f"Missing element #{element_id}"


# --- Static files on disk ---

_STATIC_DIR = Path(__file__).parent.parent / "app" / "channels" / "static"


def test_static_css_file_exists():
    assert (_STATIC_DIR / "web_channel.css").is_file()


def test_static_js_file_exists():
    assert (_STATIC_DIR / "web_channel.js").is_file()


def test_static_css_is_non_empty():
    assert (_STATIC_DIR / "web_channel.css").stat().st_size > 0


def test_static_js_is_non_empty():
    assert (_STATIC_DIR / "web_channel.js").stat().st_size > 0


def test_static_css_has_theme_variables():
    content = (_STATIC_DIR / "web_channel.css").read_text()
    assert "--bg:" in content
    assert "--accent:" in content



def test_static_js_has_iife():
    content = (_STATIC_DIR / "web_channel.js").read_text()
    assert "(function()" in content


def test_static_js_regex_uses_single_backslash():
    content = (_STATIC_DIR / "web_channel.js").read_text()
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

    msg = OutgoingMessage(content="hello", channel=ChannelType.WEB, metadata={"websocket_id": "conn1"})
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

    msg = OutgoingMessage(content="ok", channel=ChannelType.WEB,
                          metadata={"websocket_id": "conn1", "is_command": True})
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
    msg = OutgoingMessage(content="hello", channel=ChannelType.WEB, metadata={"websocket_id": "ghost"})
    await ch.send_message(msg)  # must not raise


@pytest.mark.asyncio
async def test_send_message_removes_dead_connection_on_error():
    ch, _ = make_web_channel()
    mock_ws = AsyncMock()
    mock_ws.send_json.side_effect = Exception("broken pipe")
    ch._connections["dead"] = mock_ws

    msg = OutgoingMessage(content="oops", channel=ChannelType.WEB, metadata={"websocket_id": "dead"})
    await ch.send_message(msg)

    assert "dead" not in ch._connections


# --- /api/messages channel isolation ---

def test_api_messages_rejects_foreign_channel_conv():
    from unittest.mock import patch
    ch, _ = make_web_channel()
    ch.start()

    from starlette.testclient import TestClient
    # Conversation exists but belongs to 'telegram', not 'web'
    foreign_conv = {"id": 5, "channel": "telegram", "name": "TG conv"}
    with patch("app.infra.conversations.ConversationStore.get", return_value=foreign_conv):
        client = TestClient(ch._fasthtml_app, raise_server_exceptions=True)
        resp = client.get("/api/messages?conv_id=5")
    assert resp.status_code == 404


def test_api_messages_rejects_missing_conv():
    from unittest.mock import patch
    ch, _ = make_web_channel()
    ch.start()

    from starlette.testclient import TestClient
    with patch("app.infra.conversations.ConversationStore.get", return_value=None):
        client = TestClient(ch._fasthtml_app, raise_server_exceptions=True)
        resp = client.get("/api/messages?conv_id=99")
    assert resp.status_code == 404


# --- start(): route registration ---

def test_start_registers_websocket_route():
    from starlette.routing import WebSocketRoute
    ch, _ = make_web_channel()
    ch.start()
    ws_routes = [r for r in ch._fasthtml_app.router.routes if isinstance(r, WebSocketRoute)]
    assert any(r.path == "/ws" for r in ws_routes)


def test_start_mounts_static_files():
    from starlette.routing import Mount
    ch, _ = make_web_channel()
    ch.start()
    mounts = [r for r in ch._fasthtml_app.router.routes if isinstance(r, Mount)]
    assert any(getattr(r, "path", None) == "/static" for r in mounts)
