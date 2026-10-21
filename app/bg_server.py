"""Background server: starts every configured channel with its own agent and queue."""

import asyncio
import contextlib
import os
import signal

from . import config
from .infra.app_logging import log
from .infra import restart
from .core.background_agent import BackgroundAgent
from .channels.message_queue import MessageQueue
from .core.scheduled_tasks import ScheduledTasks
from .core import runtime
from .channels.channel import Channel, ChannelType


def telegram_channel_agent() -> tuple[
    Channel | None, BackgroundAgent | None, MessageQueue | None
]:
    """Create and start the Telegram channel and its agent if configured; otherwise return Nones."""
    telegram_channel = None
    telegram_agent = None
    telegram_mq = None

    if config.get("telegram"):
        bot_token = config.telegram.get("BOT_TOKEN")
        if not bot_token:
            log.error("Telegram BOT_TOKEN not set in config, skipping Telegram channel")
        else:
            from .channels.telegram import TelegramChannel  # pylint: disable=import-outside-toplevel  # only load configured channels' deps

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
    """Create and start the Discord channel and its agent if configured; otherwise return Nones."""
    discord_channel = None
    discord_agent = None
    discord_mq = None

    if config.get("discord"):
        discord_token = config.discord.get("TOKEN")
        if not discord_token:
            log.error("Discord TOKEN not set in config, skipping Discord channel")
        else:
            from .channels.discord import DiscordChannel  # pylint: disable=import-outside-toplevel  # only load configured channels' deps

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
    """Create and start the web channel and its agent if configured; otherwise return Nones.

    If the web channel refuses to start (exposed without ``WEB_PASSWORD``) it is
    skipped with an error log, so the other channels still run.
    """
    web_channel = None
    web_agent = None
    web_mq = None

    if config.get("websocket"):
        from .channels.web_channel import WebChannel  # pylint: disable=import-outside-toplevel  # only load configured channels' deps

        ws_config = config.get("websocket")
        ws_host = ws_config.get("HOST", "127.0.0.1")
        ws_port = ws_config.get("PORT", 8765)
        log.info("Starting web channel on %s:%s", ws_host, ws_port)
        web_mq = MessageQueue()
        web_channel = WebChannel(
            web_mq, host=ws_host, port=ws_port, password=config.get("web_password")
        )
        try:
            web_channel.start()
        except RuntimeError as e:
            # e.g. exposed on 0.0.0.0 without WEB_PASSWORD: skip the web UI,
            # keep the other channels running
            log.error("Web channel disabled: %s", e)
            web_channel = None
        else:
            web_agent = BackgroundAgent(
                mq=web_mq,
                channel=web_channel,
                max_iterations=runtime.get("max_iterations", 250),
            )
            web_channel.commands = web_agent.registry  # for the input's autocomplete

    return web_channel, web_agent, web_mq


def _write_pid_file() -> None:
    """Record this process's PID so restart.sh can signal it."""
    try:
        (config.PROJECT_HOME / "background.pid").write_text(f"{os.getpid()}\n")
    except OSError as e:
        log.warning("Could not write PID file: %s", e)


async def start_server() -> None:
    """Run all configured channels, their agents and the task scheduler.

    Runs until cancelled or until a restart is requested (``/restart`` or
    SIGHUP); in the latter case it returns once running turns have finished
    and ``app.main`` re-executes the process. Returns immediately (after
    logging an error) if no channel is usable.
    """
    log.info("Starting server...")

    # Run from PROJECT_HOME/workspace so the agent's relative file operations
    # (read/write files, shell commands) happen inside the workspace.
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

    _write_pid_file()
    previous_sighup = restart.install_sighup_handler(asyncio.get_running_loop())

    existing = asyncio.all_tasks()
    server = asyncio.ensure_future(asyncio.gather(*coros))
    # gather() wraps each coroutine in a task. Cancelling gather resolves as
    # soon as the first child is cancelled, so keep the tasks to wait for all
    # of them (e.g. uvicorn closing WebSockets) before restarting.
    children = asyncio.all_tasks() - existing - {server}
    restart_wait = asyncio.ensure_future(restart.wait_requested())
    try:
        await asyncio.wait({server, restart_wait}, return_when=asyncio.FIRST_COMPLETED)
        if restart_wait.done():
            log.info("Restart: waiting for running turns to finish...")
            await restart.wait_until_idle(list(mqs.values()))
            server.cancel()
            # A channel failing while shutting down must not block the restart
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await server
            if children:
                await asyncio.wait(children, timeout=15)
        else:
            server.result()
    finally:
        restart_wait.cancel()
        server.cancel()
        # On restart the handler stays until exec_restart(), so a second
        # SIGHUP during shutdown can't kill the bot.
        if previous_sighup is not None and not restart.requested():
            signal.signal(signal.SIGHUP, previous_sighup)
