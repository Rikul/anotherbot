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
    web_channel = None
    web_agent = None
    slack_channel = None
    slack_agent = None

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

    if config.get("websocket"):
        from .channels.web_channel import WebChannel
        ws_config = config.get("websocket")
        ws_host = ws_config.get("HOST", "127.0.0.1")
        ws_port = ws_config.get("PORT", 8765)
        log.info(f"Starting web channel on {ws_host}:{ws_port}")
        web_mq = MessageQueue()
        web_channel = WebChannel(web_mq, host=ws_host, port=ws_port)
        web_channel.start()
        web_agent = BackgroundAgent(mq=web_mq, channel=web_channel, max_iterations=runtime.get("max_iterations", 250))

    if config.get("slack"):
        slack_bot_token = config.slack.get("BOT_TOKEN")
        slack_app_token = config.slack.get("APP_TOKEN")
        if not slack_bot_token or not slack_app_token:
            log.error("Slack BOT_TOKEN and APP_TOKEN are required, skipping Slack channel")
        else:
            from .channels.slack import SlackChannel
            slack_mq = MessageQueue()
            slack_channel = SlackChannel(slack_mq, bot_token=slack_bot_token, app_token=slack_app_token, allow_from=config.slack.get("ALLOW_FROM", []))
            slack_channel.start()
            slack_agent = BackgroundAgent(mq=slack_mq, channel=slack_channel, max_iterations=runtime.get("max_iterations", 250))

    if not telegram_channel and not discord_channel and not web_channel and not slack_channel:
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
    if web_channel:
        from .channels.channel import ChannelType
        channels[ChannelType.WEB.value] = web_channel
        mqs[ChannelType.WEB.value] = web_mq
    if slack_channel:
        channels["slack"] = slack_channel
        mqs["slack"] = slack_mq

    tasks = ScheduledTasks(mqs=mqs, channels=channels)

    coros = [tasks.run()]
    if telegram_channel:
        coros.extend([telegram_channel.run_polling(), telegram_agent.process_incoming(), telegram_mq.process_outgoing()])
    if discord_channel:
        coros.extend([discord_channel.run_polling(), discord_agent.process_incoming(), discord_mq.process_outgoing()])
    if web_channel:
        coros.extend([web_channel.run_polling(), web_agent.process_incoming(), web_mq.process_outgoing()])
    if slack_channel:
        coros.extend([slack_channel.run_polling(), slack_agent.process_incoming(), slack_mq.process_outgoing()])

    await asyncio.gather(*coros)
