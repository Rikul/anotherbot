import asyncio
import os

from . import config
from .infra.app_logging import log
from .core.background_agent import BackgroundAgent
from .channels.message_queue import MessageQueue
from .core.scheduled_tasks import ScheduledTasks
from .core import runtime
from .channels.channel import Channel, ChannelType


def telegram_channel_agent() -> tuple[
    Channel | None, BackgroundAgent | None, MessageQueue | None
]:
    telegram_channel = None
    telegram_agent = None
    telegram_mq = None

    if config.get("telegram"):
        bot_token = config.telegram.get("BOT_TOKEN")
        if not bot_token:
            log.error("Telegram BOT_TOKEN not set in config, skipping Telegram channel")
        else:
            from .channels.telegram import TelegramChannel

            telegram_mq = MessageQueue()
            telegram_channel = TelegramChannel(
                telegram_mq,
                bot_token=bot_token,
                allow_from=config.telegram.get("ALLOW_FROM", []),
            )
            telegram_channel.start()
            telegram_agent = BackgroundAgent(
                mq=telegram_mq,
                channel=telegram_channel,
                max_iterations=runtime.get("max_iterations", 250),
            )

    return telegram_channel, telegram_agent, telegram_mq


def discord_channel_agent() -> tuple[
    Channel | None, BackgroundAgent | None, MessageQueue | None
]:
    discord_channel = None
    discord_agent = None
    discord_mq = None

    if config.get("discord"):
        discord_token = config.discord.get("TOKEN")
        if not discord_token:
            log.error("Discord TOKEN not set in config, skipping Discord channel")
        else:
            from .channels.discord import DiscordChannel

            discord_mq = MessageQueue()
            discord_channel = DiscordChannel(
                discord_mq,
                token=discord_token,
                allow_from=config.discord.get("ALLOW_FROM", []),
            )
            discord_channel.start()
            discord_agent = BackgroundAgent(
                mq=discord_mq,
                channel=discord_channel,
                max_iterations=runtime.get("max_iterations", 250),
            )

    return discord_channel, discord_agent, discord_mq


def web_channel_agent() -> tuple[
    Channel | None, BackgroundAgent | None, MessageQueue | None
]:
    web_channel = None
    web_agent = None
    web_mq = None

    if config.get("websocket"):
        from .channels.web_channel import WebChannel

        ws_config = config.get("websocket")
        ws_host = ws_config.get("HOST", "127.0.0.1")
        ws_port = ws_config.get("PORT", 8765)
        log.info(f"Starting web channel on {ws_host}:{ws_port}")
        web_mq = MessageQueue()
        web_channel = WebChannel(
            web_mq, host=ws_host, port=ws_port, password=config.get("web_password")
        )
        try:
            web_channel.start()
        except RuntimeError as e:
            # e.g. exposed on 0.0.0.0 without WEB_PASSWORD — skip the web UI, keep other channels running
            log.error(f"Web channel disabled: {e}")
            web_channel = None
        else:
            web_agent = BackgroundAgent(
                mq=web_mq,
                channel=web_channel,
                max_iterations=runtime.get("max_iterations", 250),
            )

    return web_channel, web_agent, web_mq


async def start_server() -> None:
    log.info("Starting server...")

    # Change CWD to PROJECT_HOME/workspace to ensure all file operations are relative to this directory
    # This is important for the agent to read/write files in the workspace
    workspace_dir = config.PROJECT_HOME / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(workspace_dir)

    # Keys must be the string values: ScheduledTasks looks channels up by the
    # task's delivery_channel string (e.g. "telegram"), and ChannelType is a plain Enum.
    channel_setups = {
        ChannelType.TELEGRAM.value: telegram_channel_agent(),
        ChannelType.DISCORD.value: discord_channel_agent(),
        ChannelType.WEB.value: web_channel_agent(),
    }

    active_channels = {
        name: s for name, s in channel_setups.items() if s[0] is not None
    }
    if not active_channels:
        log.error("No channels configured, exiting...")
        return

    channels = {name: ch for name, (ch, _, _) in active_channels.items()}
    mqs = {name: mq for name, (_, _, mq) in active_channels.items()}
    tasks = ScheduledTasks(mqs=mqs, channels=channels)

    coros = [tasks.run()]
    for ch, agent, mq in active_channels.values():
        coros += [ch.run_polling(), agent.process_incoming(), mq.process_outgoing()]

    await asyncio.gather(*coros)
