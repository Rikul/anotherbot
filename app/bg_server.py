import asyncio
import os

from . import config
from .infra.app_logging import log
from .core.background_agent import BackgroundAgent
from .channels.message_queue import MessageQueue
from .core.scheduled_tasks import ScheduledTasks
from .core import runtime

async def start_server() -> None:
    log.info("Starting server...")

    telegram_channel = None
    telegram_agent = None
    discord_channel = None
    discord_agent = None

    # Change CWD to PROJECT_HOME/workspace to ensure all file operations are relative to this directory
    # This is important for the agent to read/write files in the workspace
    workspace_dir = config.PROJECT_HOME / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(workspace_dir)

    if config.get("telegram"):
        bot_token = config.telegram.get("BOT_TOKEN")
        if not bot_token:
            log.error("Telegram BOT_TOKEN not set in config, skipping Telegram channel")
        else:
            from .channels.telegram import TelegramChannel
            telegram_mq = MessageQueue()
            telegram_channel = TelegramChannel(telegram_mq, bot_token=bot_token, allow_from=config.telegram.get("ALLOW_FROM", []))
            telegram_channel.start()
            telegram_agent = BackgroundAgent(mq=telegram_mq, channel=telegram_channel, max_iterations=runtime.get("max_iterations", 250))

    if config.get("discord"):
        discord_token = config.discord.get("TOKEN")
        if not discord_token:
            log.error("Discord TOKEN not set in config, skipping Discord channel")
        else:
            from .channels.discord import DiscordChannel
            discord_mq = MessageQueue()
            discord_channel = DiscordChannel(discord_mq, token=discord_token, allow_from=config.discord.get("ALLOW_FROM", []))
            discord_channel.start()
            discord_agent = BackgroundAgent(mq=discord_mq, channel=discord_channel, max_iterations=runtime.get("max_iterations", 250))

    if not telegram_channel and not discord_channel:
        log.error("No channels configured, exiting...")
        return

    channels = {}
    mqs = {}
    if telegram_channel:
        channels["telegram"] = telegram_channel
        mqs["telegram"] = telegram_mq
    if discord_channel:
        channels["discord"] = discord_channel
        mqs["discord"] = discord_mq

    tasks = ScheduledTasks(mqs=mqs, channels=channels)

    coros = [tasks.run()]
    if telegram_channel:
        coros.extend([telegram_channel.run_polling(), telegram_agent.process_incoming(), telegram_mq.process_outgoing()])
    if discord_channel:
        coros.extend([discord_channel.run_polling(), discord_agent.process_incoming(), discord_mq.process_outgoing()])

    await asyncio.gather(*coros)
