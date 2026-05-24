import pytest
import discord
from unittest.mock import patch, MagicMock, AsyncMock

from app.channels.channel import ChannelType
from app.channels.discord import DiscordChannel, MAX_DISCORD_LENGTH
from app.channels.message import OutgoingMessage
from app.channels.message_queue import MessageQueue


def make_discord_channel(allow_from=None):
    mq = MessageQueue()
    with patch.object(discord.Client, "__init__", return_value=None):
        dc = DiscordChannel(mq=mq, token="test-token", allow_from=allow_from)
    # discord.Client.user is a read-only property returning self._connection.user
    dc._connection = MagicMock()
    dc._connection.user.id = 999
    return dc, mq


def make_not_found():
    resp = MagicMock()
    resp.status = 404
    resp.reason = "Not Found"
    return discord.NotFound(resp, "unknown channel")


# --- Initialization ---

def test_registers_delivery_function():
    dc, mq = make_discord_channel()
    assert dc in mq._delivery
    assert mq._delivery[dc].__func__ is DiscordChannel.send_message


def test_stores_token():
    dc, _ = make_discord_channel()
    assert dc.token == "test-token"


def test_channel_type():
    dc, _ = make_discord_channel()
    assert dc.channel_type == ChannelType.DISCORD


def test_initial_stopped_false():
    dc, _ = make_discord_channel()
    assert dc.has_stopped is False


# --- on_message ---

@pytest.mark.asyncio
async def test_on_message_puts_to_incoming_queue():
    dc, mq = make_discord_channel()
    message = MagicMock()
    message.author.id = 123
    message.channel.id = 456
    message.content = "hello"

    await dc.on_message(message)

    assert not mq.incoming.empty()
    msg = await mq.incoming.get()
    assert msg.content == "hello"
    assert msg.channel == ChannelType.DISCORD
    assert msg.metadata == {"channel_id": 456}


@pytest.mark.asyncio
async def test_on_message_ignores_self():
    dc, mq = make_discord_channel()
    message = MagicMock()
    message.author.id = 999  # same as dc.user.id
    message.content = "echo"

    await dc.on_message(message)

    assert mq.incoming.empty()


@pytest.mark.asyncio
async def test_on_message_ignores_whitespace_only():
    dc, mq = make_discord_channel()
    message = MagicMock()
    message.author.id = 123
    message.content = "   "

    await dc.on_message(message)

    assert mq.incoming.empty()


@pytest.mark.asyncio
async def test_on_message_rejects_unauthorized_user():
    dc, mq = make_discord_channel(allow_from=[111, 222])
    message = MagicMock()
    message.author.id = 456  # not in allow_from, not the bot itself
    message.content = "hello"
    message.reply = AsyncMock()

    await dc.on_message(message)

    assert mq.incoming.empty()
    message.reply.assert_called_once()
    assert "not authorized" in message.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_on_message_allows_when_allow_from_empty():
    dc, mq = make_discord_channel(allow_from=[])
    message = MagicMock()
    message.author.id = 456  # any user allowed when allow_from is empty
    message.channel.id = 1
    message.content = "hello"

    await dc.on_message(message)

    assert not mq.incoming.empty()


@pytest.mark.asyncio
async def test_on_message_allows_authorized_user():
    dc, mq = make_discord_channel(allow_from=[123])
    message = MagicMock()
    message.author.id = 123
    message.channel.id = 1
    message.content = "hello"

    await dc.on_message(message)

    assert not mq.incoming.empty()


@pytest.mark.asyncio
async def test_on_message_trims_whitespace():
    dc, mq = make_discord_channel()
    message = MagicMock()
    message.author.id = 123
    message.channel.id = 456
    message.content = "  hi there  "

    await dc.on_message(message)

    msg = await mq.incoming.get()
    assert msg.content == "hi there"


# --- send_message ---

@pytest.mark.asyncio
async def test_send_message_sends_to_channel():
    dc, _ = make_discord_channel()
    mock_channel = AsyncMock()
    dc.get_channel = MagicMock(return_value=mock_channel)

    msg = OutgoingMessage(content="reply", channel=dc, metadata={"channel_id": 456})
    await dc.send_message(msg)

    mock_channel.send.assert_called_once_with("reply")


@pytest.mark.asyncio
async def test_send_message_fetches_when_not_in_cache():
    dc, _ = make_discord_channel()
    mock_channel = AsyncMock()
    dc.get_channel = MagicMock(return_value=None)
    dc.fetch_channel = AsyncMock(return_value=mock_channel)

    msg = OutgoingMessage(content="reply", channel=dc, metadata={"channel_id": 456})
    await dc.send_message(msg)

    dc.fetch_channel.assert_called_once_with(456)
    mock_channel.send.assert_called_once_with("reply")


@pytest.mark.asyncio
async def test_send_message_dms_owner_when_no_channel_id():
    dc, _ = make_discord_channel()
    mock_dm = AsyncMock()
    mock_owner = AsyncMock()
    mock_owner.create_dm = AsyncMock(return_value=mock_dm)
    mock_app_info = MagicMock()
    mock_app_info.owner = mock_owner
    dc.application_info = AsyncMock(return_value=mock_app_info)

    msg = OutgoingMessage(content="task result", channel=dc, metadata={})
    await dc.send_message(msg)

    mock_owner.create_dm.assert_called_once()
    mock_dm.send.assert_called_once_with("task result")


@pytest.mark.asyncio
async def test_send_message_logs_error_when_channel_not_found(caplog):
    import logging
    dc, _ = make_discord_channel()
    dc.get_channel = MagicMock(return_value=None)
    dc.fetch_channel = AsyncMock(side_effect=make_not_found())

    msg = OutgoingMessage(content="reply", channel=dc, metadata={"channel_id": 456})
    with caplog.at_level(logging.ERROR):
        await dc.send_message(msg)

    assert "456" in caplog.text


@pytest.mark.asyncio
async def test_send_message_splits_long_content():
    dc, _ = make_discord_channel()
    mock_channel = AsyncMock()
    dc.get_channel = MagicMock(return_value=mock_channel)

    long_content = "x" * (MAX_DISCORD_LENGTH + 100)
    msg = OutgoingMessage(content=long_content, channel=dc, metadata={"channel_id": 456})
    await dc.send_message(msg)

    assert mock_channel.send.call_count == 2


# --- default_metadata / last channel tracking ---

def test_default_metadata_empty_before_any_message():
    dc, _ = make_discord_channel()
    assert dc.default_metadata == {}


@pytest.mark.asyncio
async def test_default_metadata_set_after_message():
    dc, mq = make_discord_channel()
    message = MagicMock()
    message.author.id = 123
    message.channel.id = 789
    message.content = "hello"

    await dc.on_message(message)

    assert dc.default_metadata == {"channel_id": 789}


@pytest.mark.asyncio
async def test_on_message_updates_last_channel_id():
    dc, mq = make_discord_channel()
    for channel_id in [111, 222, 333]:
        message = MagicMock()
        message.author.id = 123
        message.channel.id = channel_id
        message.content = "hi"
        await dc.on_message(message)

    assert dc._last_channel_id == 333


# --- has_stopped / clear_stopped ---

def test_clear_stopped_resets_state():
    dc, _ = make_discord_channel()
    dc.stopped = True
    dc.clear_stopped()
    assert dc.has_stopped is False


# --- command handling ---

@pytest.mark.asyncio
async def test_on_message_whoami_replies_with_user_info():
    dc, mq = make_discord_channel()
    dc.send_message = AsyncMock()
    message = MagicMock()
    message.author.id = 123
    message.author.display_name = "Alice"
    message.channel.id = 456
    message.content = "/whoami"

    await dc.on_message(message)

    dc.send_message.assert_called_once()
    sent = dc.send_message.call_args[0][0]
    assert "123" in sent.content
    assert "Alice" in sent.content
    assert sent.metadata == {"channel_id": 456}
    assert mq.incoming.empty()


@pytest.mark.asyncio
async def test_on_message_command_enqueued():
    dc, mq = make_discord_channel()
    dc.send_message = AsyncMock()
    message = MagicMock()
    message.author.id = 123
    message.author.display_name = "Alice"
    message.channel.id = 456
    message.content = "/status"

    await dc.on_message(message)

    assert not mq.incoming.empty()
    msg = await mq.incoming.get()
    assert msg.content == "/status"
    dc.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_on_message_command_with_args_enqueued():
    dc, mq = make_discord_channel()
    dc.send_message = AsyncMock()
    message = MagicMock()
    message.author.id = 123
    message.author.display_name = "Alice"
    message.channel.id = 456
    message.content = "/model gpt-4"

    await dc.on_message(message)

    assert not mq.incoming.empty()
    msg = await mq.incoming.get()
    assert msg.content == "/model gpt-4"


@pytest.mark.asyncio
async def test_on_message_unknown_command_enqueued():
    dc, mq = make_discord_channel()
    message = MagicMock()
    message.author.id = 123
    message.author.display_name = "Alice"
    message.channel.id = 456
    message.content = "/notacommand"

    await dc.on_message(message)

    assert not mq.incoming.empty()
    msg = await mq.incoming.get()
    assert msg.content == "/notacommand"
