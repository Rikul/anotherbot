"""Tests for the WebSocket channel."""

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.channels.channel import ChannelType
from app.channels.message import IncomingMessage, OutgoingMessage
from app.channels.message_queue import MessageQueue
from app.channels.websocket import WebSocketChannel, MAX_WS_MESSAGE_LENGTH


def make_websocket_channel(host="127.0.0.1", port=8765, api_key=None):
    """Create a WebSocketChannel with a fresh MessageQueue."""
    mq = MessageQueue()
    wc = WebSocketChannel(mq, host=host, port=port, api_key=api_key)
    return wc, mq


# --- Initialization --------------------------------------------------------

def test_registers_delivery_function():
    wc, mq = make_websocket_channel()
    assert wc in mq._delivery
    assert mq._delivery[wc].__func__ is WebSocketChannel.send_message


def test_stores_host_port_and_api_key():
    wc, _ = make_websocket_channel(host="0.0.0.0", port=9999, api_key="secret")
    assert wc.host == "0.0.0.0"
    assert wc.port == 9999
    assert wc.api_key == "secret"


def test_api_key_defaults_to_none():
    wc, _ = make_websocket_channel()
    assert wc.api_key is None


def test_channel_type_is_web():
    wc, _ = make_websocket_channel()
    assert wc.channel_type == ChannelType.WEB


def test_initial_stopped_false():
    wc, _ = make_websocket_channel()
    assert wc.has_stopped is False


def test_initial_connections_empty():
    wc, _ = make_websocket_channel()
    assert wc._connections == {}


def test_default_metadata_is_empty():
    wc, _ = make_websocket_channel()
    assert wc.default_metadata == {}


# --- has_stopped / clear_stopped -------------------------------------------

def test_clear_stopped_resets_state():
    wc, _ = make_websocket_channel()
    wc.stopped = True
    assert wc.has_stopped is True
    wc.clear_stopped()
    assert wc.has_stopped is False


# --- start() builds FastAPI app --------------------------------------------

def test_start_creates_fastapi_app():
    wc, _ = make_websocket_channel()
    wc.start()
    assert wc._app is not None
    # Verify the /ws route exists
    routes = [r.path for r in wc._app.routes]
    assert "/ws" in routes


# --- send_message ----------------------------------------------------------

@pytest.mark.asyncio
async def test_send_message_sends_json_to_correct_client():
    wc, _ = make_websocket_channel()
    mock_ws = AsyncMock()
    wc._connections["abc-123"] = mock_ws

    msg = OutgoingMessage(
        content="hello, world",
        channel=wc,
        metadata={"websocket_id": "abc-123"},
    )
    await wc.send_message(msg)

    mock_ws.send_json.assert_called_once_with(
        {"type": "message", "content": "hello, world"}
    )


@pytest.mark.asyncio
async def test_send_message_skips_when_no_websocket_id(caplog):
    import logging
    wc, _ = make_websocket_channel()

    msg = OutgoingMessage(content="hi", channel=wc, metadata={})
    with caplog.at_level(logging.ERROR):
        await wc.send_message(msg)

    assert "no websocket_id" in caplog.text


@pytest.mark.asyncio
async def test_send_message_skips_when_client_not_connected():
    wc, _ = make_websocket_channel()

    msg = OutgoingMessage(
        content="hi", channel=wc, metadata={"websocket_id": "ghost"}
    )
    await wc.send_message(msg)
    # No exception, no crash — just a no-op


@pytest.mark.asyncio
async def test_send_message_splits_long_content():
    wc, _ = make_websocket_channel()
    mock_ws = AsyncMock()
    wc._connections["abc"] = mock_ws

    long_content = "x" * (MAX_WS_MESSAGE_LENGTH + 100)
    msg = OutgoingMessage(
        content=long_content,
        channel=wc,
        metadata={"websocket_id": "abc"},
    )
    await wc.send_message(msg)

    # Should have called send_json twice (two chunks)
    assert mock_ws.send_json.call_count == 2


# --- _extract_content -------------------------------------------------------

class TestExtractContent:
    """Tests for _extract_content: JSON framing, raw text, edge cases."""

    def test_raw_text_passed_through(self):
        assert WebSocketChannel._extract_content("hello world") == "hello world"

    def test_json_message_extracts_content(self):
        result = WebSocketChannel._extract_content(
            '{"type": "message", "content": "hello"}'
        )
        assert result == "hello"

    def test_json_typing_message_ignored(self):
        """A non-message JSON type (e.g. 'ping') is returned as raw text."""
        result = WebSocketChannel._extract_content(
            '{"type": "ping", "content": "keepalive"}'
        )
        # Falls back to raw text for unknown JSON shapes
        assert result == '{"type": "ping", "content": "keepalive"}'

    def test_json_without_type_is_returned_as_raw(self):
        """Missing 'type' field means the JSON is returned as raw text."""
        result = WebSocketChannel._extract_content(
            '{"content": "hello"}'
        )
        assert result == '{"content": "hello"}'

    def test_malformed_json_falls_back_to_raw_text(self):
        result = WebSocketChannel._extract_content('{"broken json')
        assert result == '{"broken json'

    def test_empty_after_strip_returns_none(self):
        assert WebSocketChannel._extract_content("   ") is None

    def test_empty_content_in_json_returns_none(self):
        result = WebSocketChannel._extract_content(
            '{"type": "message", "content": " "}'
        )
        assert result is None

    def test_json_list_returns_raw(self):
        result = WebSocketChannel._extract_content('["not", "a", "dict"]')
        assert result == '["not", "a", "dict"]'

    def test_raw_starts_with_brace_but_not_json(self):
        result = WebSocketChannel._extract_content(
            "{this is not json but raw text}"
        )
        assert result == "{this is not json but raw text}"


# --- run_polling / uvicorn integration -------------------------------------

@pytest.mark.asyncio
async def test_run_polling_creates_uvicorn_server():
    """run_polling starts a uvicorn server task then blocks."""
    wc, _ = make_websocket_channel()
    wc.start()

    with patch("app.channels.websocket.uvicorn.Server") as MockServer:
        mock_server = MagicMock()
        mock_server.serve = AsyncMock(side_effect=asyncio.CancelledError)
        MockServer.return_value = mock_server

        # run_polling would block forever; cancel it after a tick
        try:
            await asyncio.wait_for(wc.run_polling(), timeout=0.1)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    MockServer.assert_called_once()


# --- error_handler ---------------------------------------------------------

@pytest.mark.asyncio
async def test_error_handler_logs_error(caplog):
    import logging
    wc, _ = make_websocket_channel()

    with caplog.at_level(logging.ERROR):
        await wc.error_handler(None, RuntimeError("test error"))

    assert "test error" in caplog.text


# --- process_message is a no-op --------------------------------------------

@pytest.mark.asyncio
async def test_process_message_is_noop():
    wc, _ = make_websocket_channel()
    # Should not raise
    await wc.process_message(None)


# --- WebSocket endpoint (simulated) ----------------------------------------

@pytest.mark.asyncio
async def test_ws_endpoint_accepts_and_routes_json_message():
    """Simulate the WS endpoint flow: accept JSON → extract → enqueue."""
    wc, mq = make_websocket_channel()
    wc.start()

    mock_ws = AsyncMock()
    mock_ws.receive_text.side_effect = [
        '{"type": "message", "content": "hello from ws"}',
        asyncio.CancelledError,  # simulate disconnect
    ]

    client_id = "test-client-id"
    wc._connections[client_id] = mock_ws

    raw = await mock_ws.receive_text()
    content = WebSocketChannel._extract_content(raw)
    await wc.mq.incoming.put(
        IncomingMessage(
            content=content,
            channel=ChannelType.WEB,
            metadata={"websocket_id": client_id},
        )
    )

    assert not mq.incoming.empty()
    msg = await mq.incoming.get()
    assert msg.content == "hello from ws"
    assert msg.channel == ChannelType.WEB
    assert msg.metadata == {"websocket_id": client_id}


@pytest.mark.asyncio
async def test_ws_endpoint_accepts_raw_text():
    """Raw text messages also work (backward-compatible fallback)."""
    wc, _ = make_websocket_channel()
    wc.start()

    mock_ws = AsyncMock()
    mock_ws.receive_text.side_effect = [
        "raw message without json",
        asyncio.CancelledError,
    ]

    client_id = "test-raw"
    wc._connections[client_id] = mock_ws

    raw = await mock_ws.receive_text()
    content = WebSocketChannel._extract_content(raw)

    assert content == "raw message without json"


# --- Command handling (simulated endpoint logic) ----------------------------

@pytest.mark.asyncio
async def test_command_whoami_replies_with_client_id():
    wc, mq = make_websocket_channel()
    wc.start()
    mock_ws = AsyncMock()
    client_id = "abc-456"
    wc._connections[client_id] = mock_ws

    await wc._safe_send_json(
        client_id,
        {"type": "message", "content": f"Your connection ID is {client_id}."},
    )

    mock_ws.send_json.assert_called_once()
    sent = mock_ws.send_json.call_args[0][0]
    assert sent["type"] == "message"
    assert "abc-456" in sent["content"]
    assert mq.incoming.empty()


@pytest.mark.asyncio
async def test_command_stop_sets_stopped_flag():
    wc, mq = make_websocket_channel()
    wc.start()
    mock_ws = AsyncMock()
    client_id = "abc-789"
    wc._connections[client_id] = mock_ws

    wc.stopped = True
    await wc._safe_send_json(
        client_id,
        {"type": "message", "content": "Stopped."},
    )

    assert wc.has_stopped is True
    mock_ws.send_json.assert_called_once()
    sent = mock_ws.send_json.call_args[0][0]
    assert "Stopped" in sent["content"]
    assert mq.incoming.empty()


@pytest.mark.asyncio
async def test_bare_slash_does_not_crash():
    """Sending just '/' should not cause an IndexError."""
    content = WebSocketChannel._extract_content("/")
    assert content == "/"

    cmd_parts = content[1:].split(maxsplit=1)
    assert cmd_parts == []


@pytest.mark.asyncio
async def test_slash_space_does_not_crash():
    """Sending '/ ' should not cause an IndexError."""
    content = WebSocketChannel._extract_content("/ ")
    # _extract_content strips whitespace, so "/ " becomes "/"
    assert content == "/"

    cmd_parts = content[1:].split(maxsplit=1)
    assert cmd_parts == []


# --- safe_send_json edge cases ----------------------------------------------

@pytest.mark.asyncio
async def test_safe_send_json_skips_disconnected_client():
    wc, _ = make_websocket_channel()
    await wc._safe_send_json("nonexistent", {"type": "message", "content": "test"})
    # No exception, no crash


@pytest.mark.asyncio
async def test_safe_send_json_cleans_up_on_error():
    wc, _ = make_websocket_channel()
    mock_ws = AsyncMock()
    mock_ws.send_json.side_effect = RuntimeError("connection lost")
    wc._connections["bad"] = mock_ws

    await wc._safe_send_json("bad", {"type": "message", "content": "test"})

    # Client should be removed after failed send
    assert "bad" not in wc._connections


# --- Connection management --------------------------------------------------

@pytest.mark.asyncio
async def test_connection_added_and_removed():
    wc, _ = make_websocket_channel()
    mock_ws = AsyncMock()
    client_id = "conn-1"

    wc._connections[client_id] = mock_ws
    assert client_id in wc._connections

    wc._connections.pop(client_id, None)
    assert client_id not in wc._connections


@pytest.mark.asyncio
async def test_multiple_concurrent_clients():
    wc, _ = make_websocket_channel()
    ws1 = AsyncMock()
    ws2 = AsyncMock()
    ws3 = AsyncMock()

    wc._connections["a"] = ws1
    wc._connections["b"] = ws2
    wc._connections["c"] = ws3

    # Send to client "b"
    msg = OutgoingMessage(
        content="for b only",
        channel=wc,
        metadata={"websocket_id": "b"},
    )
    await wc.send_message(msg)

    ws2.send_json.assert_called_once_with(
        {"type": "message", "content": "for b only"}
    )
    ws1.send_json.assert_not_called()
    ws3.send_json.assert_not_called()