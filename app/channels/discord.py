"""Discord channel, built on discord.py."""

import logging

import discord

from .message_queue import MessageQueue
from .channel import Channel, ChannelType
from .message import OutgoingMessage, IncomingMessage

log = logging.getLogger(__name__)

MAX_DISCORD_LENGTH = 2000


class DiscordChannel(discord.Client, Channel):
    """Discord bot front end (also a ``discord.Client``).

    Only users in ``allow_from`` may talk to the bot (an empty list allows
    everyone). Replies go to the channel the message came from; scheduled-task
    results go to the last active channel, or as a DM to the bot's owner.
    """

    user: discord.ClientUser  # filled after login

    def __init__(
        self, mq: MessageQueue, token: str, allow_from: list[int] = None
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.mq = mq
        self.token = token
        self.allow_from = allow_from or []
        self.stopped = False
        self._last_channel_id: int | None = None
        mq.register(self, self.send_message)

    @property
    def has_stopped(self) -> bool:
        return self.stopped

    def clear_stopped(self) -> None:
        self.stopped = False

    @property
    def channel_type(self) -> ChannelType:
        return ChannelType.DISCORD

    @property
    def default_metadata(self) -> dict:
        return {"channel_id": self._last_channel_id} if self._last_channel_id else {}

    async def on_ready(self) -> None:
        """discord.py event: log the bot's identity once connected."""
        log.info("Discord: logged in as %s (ID: %s)", self.user, self.user.id)

    async def on_message(self, message: discord.Message) -> None:
        """discord.py event: check the sender, answer /whoami and /stop, forward the rest."""
        if message.author.id == self.user.id:
            return
        user_id = message.author.id
        if self.allow_from and user_id not in self.allow_from:
            log.warning(
                "Discord: ignoring message from unauthorized user id=%s", user_id
            )
            await message.reply("Sorry, you are not authorized to use this bot.")
            return
        content = message.content.strip() if message.content else ""
        if not content:
            return

        if content.startswith("/"):
            cmd_name = content[1:].split(maxsplit=1)[0].lower()
            metadata = {"channel_id": message.channel.id}
            if cmd_name == "whoami":
                name = message.author.display_name
                await self.send_message(
                    OutgoingMessage(
                        content=f"Your user ID is {user_id} and your name is {name}.",
                        channel=ChannelType.DISCORD,
                        metadata=metadata,
                    )
                )
                return
            if cmd_name == "stop":
                self.stopped = True
                await self.send_message(
                    OutgoingMessage(
                        content="Stopped.",
                        channel=ChannelType.DISCORD,
                        metadata=metadata,
                    )
                )
                return
        self._last_channel_id = message.channel.id
        await self.mq.incoming.put(
            IncomingMessage(
                content=content,
                channel=ChannelType.DISCORD,
                metadata={"channel_id": message.channel.id},
            )
        )

    async def _resolve_destination(
        self, channel_id: int | None
    ) -> discord.abc.Messageable | None:
        if channel_id:
            channel = self.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.fetch_channel(channel_id)
                except discord.NotFound:
                    log.error("Discord channel %s not found", channel_id)
                    return None
            return channel
        # No channel context — DM the app owner
        try:
            app_info = await self.application_info()
            return await app_info.owner.create_dm()
        except Exception as e:  # pylint: disable=broad-exception-caught  # best-effort fallback
            log.error("Discord: failed to open DM with owner: %s", e)
            return None

    async def send_message(self, message: OutgoingMessage) -> None:
        channel_id = message.metadata.get("channel_id")
        dest = await self._resolve_destination(channel_id)
        if dest is None:
            return
        for i in range(0, len(message.content), MAX_DISCORD_LENGTH):
            await dest.send(message.content[i : i + MAX_DISCORD_LENGTH])

    async def process_message(self, message) -> None:
        pass  # handled by on_message discord event

    async def error_handler(self, update, context) -> None:
        log.error("Discord error: %s", context)

    # Channel.start() (sync setup hook) shadows discord.Client.start(token);
    # run_polling() calls discord.Client.start explicitly.
    def start(self) -> None:  # pylint: disable=arguments-differ,invalid-overridden-method
        log.info("Starting Discord channel...")

    async def run_polling(self) -> None:
        """Log in and process Discord events until cancelled."""
        await discord.Client.start(self, self.token)
