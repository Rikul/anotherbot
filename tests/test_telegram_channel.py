import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.channels.channel import ChannelType
from app.channels.message import OutgoingMessage
from app.channels.message_queue import MessageQueue
from app.channels.telegram import TelegramChannel


def make_telegram_channel(allow_from=None):
    mq = MessageQueue()
    with patch("app.channels.telegram.ApplicationBuilder"):
        tc = TelegramChannel(mq=mq, bot_token="test-token", allow_from=allow_from)
    return tc, mq


# --- Initialization ---

def test_registers_delivery_function():
    mq = MessageQueue()
    with patch("app.channels.telegram.ApplicationBuilder"):
        tc = TelegramChannel(mq=mq, bot_token="test-token")
    # register(self, fn) uses the channel object as the key
    assert tc in mq._delivery
    assert mq._delivery[tc].__func__ is TelegramChannel.send_message


def test_stores_bot_token_and_allow_from():
    tc, _ = make_telegram_channel(allow_from=[111, 222])
    assert tc.bot_token == "test-token"
    assert tc.allow_from == [111, 222]


def test_allow_from_defaults_to_empty_list():
    tc, _ = make_telegram_channel()
    assert tc.allow_from == []


# --- send_message ---

@pytest.mark.asyncio
async def test_send_message_sends_to_correct_chat():
    tc, _ = make_telegram_channel()
    tc.app = MagicMock()
    tc.app.bot.send_message = AsyncMock()

    msg = OutgoingMessage(content="hi", channel=ChannelType.TELEGRAM, metadata={"chat_id": 42})
    await tc.send_message(msg)

    tc.app.bot.send_message.assert_called_once_with(chat_id=42, text="hi")


@pytest.mark.asyncio
async def test_send_message_skips_when_no_chat_id(caplog):
    import logging
    tc, _ = make_telegram_channel()
    tc.app = MagicMock()
    tc.app.bot.send_message = AsyncMock()

    msg = OutgoingMessage(content="hi", channel=ChannelType.TELEGRAM, metadata={})
    with caplog.at_level(logging.ERROR):
        await tc.send_message(msg)

    tc.app.bot.send_message.assert_not_called()
    assert "no chat_id" in caplog.text


# --- process_message ---

def make_update(text="hello", user_id=123, chat_id=456):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.effective_message = update.message
    return update


@pytest.mark.asyncio
async def test_process_message_puts_to_incoming_queue():
    tc, mq = make_telegram_channel()
    update = make_update(text="do something", user_id=1, chat_id=10)

    await tc.process_message(update, AsyncMock())

    assert not mq.incoming.empty()
    msg = await mq.incoming.get()
    assert msg.content == "do something"
    assert msg.channel == ChannelType.TELEGRAM
    assert msg.metadata == {"chat_id": 10}


@pytest.mark.asyncio
async def test_process_message_rejects_unauthorized_user():
    tc, mq = make_telegram_channel(allow_from=[999])
    update = make_update(user_id=123)

    await tc.process_message(update, MagicMock())

    assert mq.incoming.empty()
    update.message.reply_text.assert_called_once()
    assert "not authorized" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_process_message_allows_when_allow_from_empty():
    tc, mq = make_telegram_channel(allow_from=[])
    update = make_update(user_id=999, text="hi")

    await tc.process_message(update, AsyncMock())

    assert not mq.incoming.empty()


@pytest.mark.asyncio
async def test_process_message_rejects_empty_text():
    tc, mq = make_telegram_channel()
    update = make_update(text="   ")

    await tc.process_message(update, MagicMock())

    assert mq.incoming.empty()
    update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_process_message_rejects_non_text_message():
    tc, mq = make_telegram_channel()
    update = make_update()
    update.message.text = None  # no text

    await tc.process_message(update, MagicMock())

    assert mq.incoming.empty()
    update.message.reply_text.assert_called_once()


# --- error_handler ---

@pytest.mark.asyncio
async def test_error_handler_notifies_user_on_chat_update():
    tc, _ = make_telegram_channel()
    from telegram import Update as TelegramUpdate

    update = MagicMock(spec=TelegramUpdate)
    update.effective_message.reply_text = AsyncMock()
    context = MagicMock()
    context.error = RuntimeError("boom")

    await tc.error_handler(update, context)

    update.effective_message.reply_text.assert_called_once()
    assert "error" in update.effective_message.reply_text.call_args[0][0].lower()


# --- command_handler ---

@pytest.mark.asyncio
async def test_stop_sets_has_stopped():
    tc, mq = make_telegram_channel()
    tc.send_message = AsyncMock()

    await tc.command_handler(make_update(text="/stop"), MagicMock())

    assert tc.has_stopped is True
    assert mq.incoming.empty()
    tc.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_clear_stopped_resets_state():
    tc, _ = make_telegram_channel()
    tc.stopped = True
    assert tc.has_stopped is True

    tc.clear_stopped()
    assert tc.has_stopped is False


@pytest.mark.asyncio
async def test_command_handler_whoami_replies_with_user_info():
    tc, _ = make_telegram_channel()
    tc.send_message = AsyncMock()

    await tc.command_handler(make_update(text="/whoami", user_id=42), MagicMock())

    tc.send_message.assert_called_once()
    text = tc.send_message.call_args[0][0].content
    assert "42" in text


@pytest.mark.asyncio
async def test_command_handler_dispatches_with_args():
    tc, mq = make_telegram_channel()
    tc.send_message = AsyncMock()

    await tc.command_handler(make_update(text="/model gpt-4"), MagicMock())

    assert not mq.incoming.empty()
    msg = await mq.incoming.get()
    assert msg.content == "/model gpt-4"


# --- error_handler ---

@pytest.mark.asyncio
async def test_error_handler_handles_non_update_object():
    tc, _ = make_telegram_channel()
    context = MagicMock()
    context.error = RuntimeError("boom")

    # Should not raise even when update is not a Telegram Update
    await tc.error_handler("not-an-update", context)
