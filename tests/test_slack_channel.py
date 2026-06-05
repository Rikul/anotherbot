import pytest
from unittest.mock import patch, AsyncMock

from app.channels.channel import ChannelType
from app.channels.slack import SlackChannel, MAX_SLACK_LENGTH
from app.channels.message import OutgoingMessage
from app.channels.message_queue import MessageQueue


def make_slack_channel(allow_from=None):
    if allow_from is None:
        allow_from = ["U123"]  # default authorized user used across these tests
    mq = MessageQueue()
    with patch("app.channels.slack.AsyncApp"), \
         patch("app.channels.slack.AsyncSocketModeHandler"):
        sc = SlackChannel(mq=mq, bot_token="xoxb-test", app_token="xapp-test", allow_from=allow_from)
    return sc, mq


# --- Initialization ---

def test_registers_delivery_function():
    sc, mq = make_slack_channel()
    assert sc in mq._delivery
    assert mq._delivery[sc].__func__ is SlackChannel.send_message


def test_stores_tokens():
    sc, _ = make_slack_channel()
    assert sc.bot_token == "xoxb-test"
    assert sc.app_token == "xapp-test"


def test_channel_type():
    sc, _ = make_slack_channel()
    assert sc.channel_type == ChannelType.SLACK


def test_initial_stopped_false():
    sc, _ = make_slack_channel()
    assert sc.has_stopped is False


def test_clear_stopped_resets_state():
    sc, _ = make_slack_channel()
    sc.stopped = True
    sc.clear_stopped()
    assert sc.has_stopped is False


# --- default_metadata ---

def test_default_metadata_empty_before_any_message():
    sc, _ = make_slack_channel()
    assert sc.default_metadata == {}


# --- _handle_message ---

@pytest.mark.asyncio
async def test_handle_message_puts_to_incoming_queue():
    sc, mq = make_slack_channel()
    say = AsyncMock()

    await sc._handle_message(
        event={"user": "U123", "text": "hello", "channel": "C456"},
        say=say,
    )

    assert not mq.incoming.empty()
    msg = await mq.incoming.get()
    assert msg.content == "hello"
    assert msg.channel == ChannelType.SLACK
    assert msg.metadata == {"channel_id": "C456"}


@pytest.mark.asyncio
async def test_handle_message_ignores_bot_messages():
    sc, mq = make_slack_channel()
    say = AsyncMock()

    await sc._handle_message(
        event={"bot_id": "B123", "text": "i am a bot", "channel": "C456"},
        say=say,
    )

    assert mq.incoming.empty()


@pytest.mark.asyncio
async def test_handle_message_ignores_edit_and_delete_subtypes():
    sc, mq = make_slack_channel()
    say = AsyncMock()

    for subtype in ("message_changed", "message_deleted", "channel_join", "channel_leave"):
        await sc._handle_message(
            event={"user": "U123", "text": "hi", "channel": "C456", "subtype": subtype},
            say=say,
        )

    assert mq.incoming.empty()


@pytest.mark.asyncio
async def test_handle_message_allows_user_subtypes():
    sc, mq = make_slack_channel()

    await sc._handle_message(
        event={"user": "U123", "text": "check this out", "channel": "C456", "subtype": "file_share"},
        say=AsyncMock(),
    )

    assert not mq.incoming.empty()


@pytest.mark.asyncio
async def test_handle_message_ignores_event_with_no_channel():
    sc, mq = make_slack_channel()

    await sc._handle_message(
        event={"user": "U123", "text": "hello"},  # no "channel" key
        say=AsyncMock(),
    )

    assert mq.incoming.empty()


@pytest.mark.asyncio
async def test_handle_message_rejects_unauthorized_user():
    sc, mq = make_slack_channel(allow_from=["U111", "U222"])
    say = AsyncMock()

    await sc._handle_message(
        event={"user": "U999", "text": "hello", "channel": "C456"},
        say=say,
    )

    assert mq.incoming.empty()
    say.assert_called_once()
    assert "not authorized" in say.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_message_denies_when_allow_from_empty():
    sc, mq = make_slack_channel(allow_from=[])
    say = AsyncMock()

    await sc._handle_message(
        event={"user": "U999", "text": "hello", "channel": "C456"},
        say=say,
    )

    assert mq.incoming.empty()
    say.assert_called_once()


@pytest.mark.asyncio
async def test_handle_message_allows_authorized_user():
    sc, mq = make_slack_channel(allow_from=["U123"])

    await sc._handle_message(
        event={"user": "U123", "text": "hello", "channel": "C456"},
        say=AsyncMock(),
    )

    assert not mq.incoming.empty()


@pytest.mark.asyncio
async def test_handle_message_trims_whitespace():
    sc, mq = make_slack_channel()

    await sc._handle_message(
        event={"user": "U123", "text": "  hi there  ", "channel": "C456"},
        say=AsyncMock(),
    )

    msg = await mq.incoming.get()
    assert msg.content == "hi there"


@pytest.mark.asyncio
async def test_handle_message_ignores_empty_text():
    sc, mq = make_slack_channel()

    await sc._handle_message(
        event={"user": "U123", "text": "   ", "channel": "C456"},
        say=AsyncMock(),
    )

    assert mq.incoming.empty()


@pytest.mark.asyncio
async def test_handle_message_updates_last_channel_id():
    sc, mq = make_slack_channel()

    for ch in ["C1", "C2", "C3"]:
        await sc._handle_message(
            event={"user": "U123", "text": "hi", "channel": ch},
            say=AsyncMock(),
        )
        await mq.incoming.get()

    assert sc._last_channel_id == "C3"
    assert sc.default_metadata == {"channel_id": "C3"}


# --- commands ---

@pytest.mark.asyncio
async def test_whoami_replies_with_user_id():
    sc, mq = make_slack_channel()
    sc.send_message = AsyncMock()

    await sc._handle_message(
        event={"user": "U123", "text": "/whoami", "channel": "C456"},
        say=AsyncMock(),
    )

    sc.send_message.assert_called_once()
    sent = sc.send_message.call_args[0][0]
    assert "U123" in sent.content
    assert sent.metadata == {"channel_id": "C456"}
    assert mq.incoming.empty()


@pytest.mark.asyncio
async def test_stop_sets_stopped_flag():
    sc, mq = make_slack_channel()
    sc.send_message = AsyncMock()

    await sc._handle_message(
        event={"user": "U123", "text": "/stop", "channel": "C456"},
        say=AsyncMock(),
    )

    assert sc.has_stopped is True
    assert mq.incoming.empty()


@pytest.mark.asyncio
async def test_other_command_enqueued():
    sc, mq = make_slack_channel()

    await sc._handle_message(
        event={"user": "U123", "text": "/status", "channel": "C456"},
        say=AsyncMock(),
    )

    assert not mq.incoming.empty()
    msg = await mq.incoming.get()
    assert msg.content == "/status"


# --- send_message ---

@pytest.mark.asyncio
async def test_send_message_posts_to_channel():
    sc, _ = make_slack_channel()
    sc.app.client.chat_postMessage = AsyncMock()

    msg = OutgoingMessage(content="hello", channel=ChannelType.SLACK, metadata={"channel_id": "C456"})
    await sc.send_message(msg)

    sc.app.client.chat_postMessage.assert_called_once_with(channel="C456", text="hello")


@pytest.mark.asyncio
async def test_send_message_splits_long_content():
    sc, _ = make_slack_channel()
    sc.app.client.chat_postMessage = AsyncMock()

    long_content = "x" * (MAX_SLACK_LENGTH + 100)
    msg = OutgoingMessage(content=long_content, channel=ChannelType.SLACK, metadata={"channel_id": "C456"})
    await sc.send_message(msg)

    assert sc.app.client.chat_postMessage.call_count == 2


@pytest.mark.asyncio
async def test_send_message_logs_error_when_no_channel_id(caplog):
    import logging
    sc, _ = make_slack_channel()
    sc.app.client.chat_postMessage = AsyncMock()

    msg = OutgoingMessage(content="hello", channel=ChannelType.SLACK, metadata={})
    with caplog.at_level(logging.ERROR):
        await sc.send_message(msg)

    assert sc.app.client.chat_postMessage.call_count == 0
    assert "channel_id" in caplog.text


# --- _handle_slash_command ---

def make_command(cmd="/status", text="", user_id="U123", channel_id="C456"):
    return {"command": cmd, "text": text, "user_id": user_id, "channel_id": channel_id}


@pytest.mark.asyncio
async def test_slash_command_enqueued():
    sc, mq = make_slack_channel()
    ack = AsyncMock()

    await sc._handle_slash_command(ack, make_command("/status"), AsyncMock())

    ack.assert_called_once()
    assert not mq.incoming.empty()
    msg = await mq.incoming.get()
    assert msg.content == "/status"
    assert msg.metadata == {"channel_id": "C456"}


@pytest.mark.asyncio
async def test_slash_command_with_args_enqueued():
    sc, mq = make_slack_channel()
    ack = AsyncMock()

    await sc._handle_slash_command(ack, make_command("/model", text="gpt-4"), AsyncMock())

    ack.assert_called_once()
    msg = await mq.incoming.get()
    assert msg.content == "/model gpt-4"


@pytest.mark.asyncio
async def test_slash_command_whoami():
    sc, mq = make_slack_channel()
    sc.send_message = AsyncMock()
    ack = AsyncMock()

    await sc._handle_slash_command(ack, make_command("/whoami"), AsyncMock())

    sc.send_message.assert_called_once()
    assert "U123" in sc.send_message.call_args[0][0].content
    assert mq.incoming.empty()


@pytest.mark.asyncio
async def test_slash_command_stop():
    sc, mq = make_slack_channel()
    sc.send_message = AsyncMock()
    ack = AsyncMock()

    await sc._handle_slash_command(ack, make_command("/stop"), AsyncMock())

    assert sc.has_stopped is True
    assert mq.incoming.empty()


@pytest.mark.asyncio
async def test_slash_command_rejects_unauthorized_user():
    sc, mq = make_slack_channel(allow_from=["U123"])
    ack = AsyncMock()
    say = AsyncMock()

    await sc._handle_slash_command(ack, make_command("/status", user_id="U999"), say)

    ack.assert_called_once()
    assert mq.incoming.empty()
    say.assert_called_once()
    assert "not authorized" in say.call_args[0][0]


@pytest.mark.asyncio
async def test_slash_command_updates_last_channel_id():
    sc, mq = make_slack_channel()
    ack = AsyncMock()

    await sc._handle_slash_command(ack, make_command("/status", channel_id="C999"), AsyncMock())

    assert sc._last_channel_id == "C999"


# --- _last_channel_id updated before command branch in _handle_message ---

@pytest.mark.asyncio
async def test_handle_message_updates_last_channel_id_for_commands():
    sc, mq = make_slack_channel()
    sc.send_message = AsyncMock()

    await sc._handle_message(
        event={"user": "U123", "text": "/whoami", "channel": "C777"},
        say=AsyncMock(),
    )

    assert sc._last_channel_id == "C777"
