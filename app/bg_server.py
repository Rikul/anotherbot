import asyncio
import os

from . import config
from .infra.app_logging import log
from .core.background_agent import BackgroundAgent
from .channels.message_queue import MessageQueue
from .core.scheduled_tasks import ScheduledTasks


async def start_server() -> None:

    log.info("Starting server...")
    telegram_channel = None
    mq = MessageQueue()

    # Change CWD to PROJECT_HOME/workspace to ensure all file operations are relative to this directory
    # This is important for the agent to read/write files in the workspace
    workspace_dir = config.PROJECT_HOME / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(workspace_dir)

    if config.get("telegram"):
        bot_token = config.telegram.get("BOT_TOKEN")

        if not bot_token:
            log.error("Telegram BOT_TOKEN not set in config, skipping Telegram channel")
            return

        from .channels.telegram import TelegramChannel
        telegram_channel = TelegramChannel(mq, bot_token=bot_token, allow_from=config.telegram.get("ALLOW_FROM", []))


    if not telegram_channel:
        log.error("No channels configured, exiting...")
        return

    telegram_channel.start()
    telegram_agent = BackgroundAgent(mq=mq, channel=telegram_channel)

    channels = {"telegram": telegram_channel} if telegram_channel else {}
    allow_from = config.telegram.get("ALLOW_FROM", []) if config.get("telegram") else []
    default_metadata = {"chat_id": allow_from[0]} if allow_from else {}
    tasks = ScheduledTasks(mq=mq, channels=channels, default_metadata=default_metadata)

    await asyncio.gather(
        telegram_channel.run_polling(),
        telegram_agent.process_incoming(),
        mq.process_outgoing(),
        tasks.run()
    )