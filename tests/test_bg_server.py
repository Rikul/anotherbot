import pytest
from unittest.mock import patch, MagicMock, AsyncMock, ANY

from app.bg_server import start_server


def _make_config(telegram_token=None, discord_token=None):
    """Build a mock config that returns only the configured channels."""
    mock_config = MagicMock()
    mock_config.PROJECT_HOME = MagicMock()

    active = {}
    if telegram_token:
        active["telegram"] = True
        mock_config.telegram.get.side_effect = lambda key, default=None: {
            "BOT_TOKEN": telegram_token,
            "ALLOW_FROM": [],
        }.get(key, default)

    if discord_token:
        active["discord"] = True
        mock_config.discord.get.side_effect = lambda key, default=None: {
            "TOKEN": discord_token,
        }.get(key, default)

    mock_config.get.side_effect = lambda key: active.get(key)
    return mock_config


@pytest.mark.asyncio
async def test_start_server_discord_only_starts_discord_agent():
    mock_gather = AsyncMock()
    mock_config = _make_config(discord_token="discord-token")

    with patch("app.bg_server.config", mock_config), \
         patch("app.bg_server.os.chdir"), \
         patch("app.channels.discord.DiscordChannel") as MockDC, \
         patch("app.bg_server.BackgroundAgent") as MockAgent, \
         patch("app.bg_server.ScheduledTasks"), \
         patch("app.bg_server.MessageQueue") as MockMQ, \
         patch("asyncio.gather", mock_gather):

        mock_dc = MockDC.return_value
        mock_mq = MockMQ.return_value

        await start_server()

    MockDC.assert_called_once_with(mock_mq, token="discord-token", allow_from=[])
    MockAgent.assert_called_once_with(mq=mock_mq, channel=mock_dc, max_iterations=ANY)
    assert mock_gather.called


@pytest.mark.asyncio
async def test_start_server_discord_only_no_telegram_crash():
    """start_server must not AttributeError on config.telegram when only Discord is set."""
    mock_gather = AsyncMock()
    mock_config = _make_config(discord_token="discord-token")
    # config.telegram is not set — accessing it should not be reached
    del mock_config.telegram

    with patch("app.bg_server.config", mock_config), \
         patch("app.bg_server.os.chdir"), \
         patch("app.channels.discord.DiscordChannel"), \
         patch("app.bg_server.BackgroundAgent"), \
         patch("app.bg_server.ScheduledTasks"), \
         patch("app.bg_server.MessageQueue"), \
         patch("asyncio.gather", mock_gather):

        await start_server()  # must not raise


@pytest.mark.asyncio
async def test_start_server_discord_only_gather_excludes_telegram():
    mock_gather = AsyncMock()
    mock_config = _make_config(discord_token="discord-token")

    with patch("app.bg_server.config", mock_config), \
         patch("app.bg_server.os.chdir"), \
         patch("app.channels.discord.DiscordChannel") as MockDC, \
         patch("app.bg_server.BackgroundAgent") as MockAgent, \
         patch("app.bg_server.ScheduledTasks"), \
         patch("app.bg_server.MessageQueue") as MockMQ, \
         patch("asyncio.gather", mock_gather):

        mock_dc = MockDC.return_value
        mock_agent = MockAgent.return_value
        mock_mq = MockMQ.return_value

        await start_server()

    # tasks.run + discord run_polling + discord process_incoming + discord process_outgoing = 4
    coros = mock_gather.call_args[0]
    assert len(coros) == 4
    mock_dc.run_polling.assert_called_once()
    mock_agent.process_incoming.assert_called_once()
    mock_mq.process_outgoing.assert_called_once()


@pytest.mark.asyncio
async def test_start_server_exits_when_no_channels_configured():
    mock_gather = AsyncMock()
    mock_config = _make_config()  # neither telegram nor discord

    with patch("app.bg_server.config", mock_config), \
         patch("app.bg_server.os.chdir"), \
         patch("asyncio.gather", mock_gather):

        await start_server()

    mock_gather.assert_not_called()


@pytest.mark.asyncio
async def test_start_server_both_channels_uses_separate_mqs():
    mock_gather = AsyncMock()
    mock_config = _make_config(telegram_token="tg-token", discord_token="discord-token")
    mq_instances = []

    def make_mq():
        mq = MagicMock()
        mq_instances.append(mq)
        return mq

    with patch("app.bg_server.config", mock_config), \
         patch("app.bg_server.os.chdir"), \
         patch("app.channels.telegram.TelegramChannel"), \
         patch("app.channels.discord.DiscordChannel"), \
         patch("app.bg_server.BackgroundAgent"), \
         patch("app.bg_server.ScheduledTasks"), \
         patch("app.bg_server.MessageQueue", side_effect=make_mq), \
         patch("asyncio.gather", mock_gather):

        await start_server()

    # One MQ per channel
    assert len(mq_instances) == 2
    assert mq_instances[0] is not mq_instances[1]


@pytest.mark.asyncio
async def test_start_server_discord_missing_token_skips_channel():
    mock_gather = AsyncMock()
    mock_config = _make_config(discord_token=None)
    mock_config.get.side_effect = lambda key: {"discord": True}.get(key)
    mock_config.discord.get.side_effect = lambda key, default=None: None  # TOKEN missing

    with patch("app.bg_server.config", mock_config), \
         patch("app.bg_server.os.chdir"), \
         patch("asyncio.gather", mock_gather):

        await start_server()

    mock_gather.assert_not_called()


# --- web channel auth guard (config from env vars) ---

def _closing_gather():
    """Mock asyncio.gather that closes the coroutines it receives (they're never run)."""
    async def gather(*coros, **kwargs):
        for c in coros:
            c.close()
    return AsyncMock(side_effect=gather)


def _web_server_patches():
    return [
        patch("app.bg_server.os.chdir"),
        patch("app.bg_server.BackgroundAgent"),
        patch("app.bg_server.ScheduledTasks"),
    ]


@pytest.mark.asyncio
async def test_exposed_web_without_password_is_skipped_and_server_exits(monkeypatch):
    import app.config as config
    monkeypatch.setenv("WEBSOCKET_HOST", "0.0.0.0")
    config.load()
    mock_gather = _closing_gather()
    patches = _web_server_patches()
    for p in patches:
        p.start()
    try:
        with patch("asyncio.gather", mock_gather):
            await start_server()
    finally:
        for p in patches:
            p.stop()
    mock_gather.assert_not_called()  # web was the only channel


@pytest.mark.asyncio
async def test_exposed_web_without_password_keeps_other_channels(monkeypatch):
    import app.config as config
    monkeypatch.setenv("WEBSOCKET_HOST", "0.0.0.0")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord-token")
    config.load()
    mock_gather = _closing_gather()
    patches = _web_server_patches()
    for p in patches:
        p.start()
    try:
        with patch("app.channels.discord.DiscordChannel"), \
             patch("app.channels.web_channel.WebChannel.run_polling") as web_polling, \
             patch("asyncio.gather", mock_gather):
            await start_server()
    finally:
        for p in patches:
            p.stop()
    assert mock_gather.called
    web_polling.assert_not_called()


@pytest.mark.asyncio
async def test_exposed_web_with_password_starts(monkeypatch):
    import app.config as config
    from app.channels.web_auth import WebAuthMiddleware
    monkeypatch.setenv("WEBSOCKET_HOST", "0.0.0.0")
    monkeypatch.setenv("WEB_PASSWORD", "s3cret")
    config.load()
    mock_gather = _closing_gather()
    patches = _web_server_patches()
    for p in patches:
        p.start()
    try:
        started = []

        async def fake_run_polling(self):
            pass

        def record(self):
            started.append(self)
            return fake_run_polling(self)

        with patch("app.channels.web_channel.WebChannel.run_polling", record), \
             patch("asyncio.gather", mock_gather):
            await start_server()
    finally:
        for p in patches:
            p.stop()
    assert mock_gather.called
    assert len(started) == 1
    assert isinstance(started[0]._asgi_app, WebAuthMiddleware)
