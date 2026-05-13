import discord
import logging

from .message_queue import MessageQueue
from .channel import Channel, ChannelType
from .message import OutgoingMessage, IncomingMessage

log = logging.getLogger(__name__)

MAX_DISCORD_LENGTH = 2000


class DiscordChannel(discord.Client, Channel):
    user: discord.ClientUser  # filled after login

    def __init__(self, mq: MessageQueue, token: str) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.mq = mq
        self.token = token
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
        log.info(f"Discord: logged in as {self.user} (ID: {self.user.id})")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.id == self.user.id:
            return
        if message.content and message.content.strip():
            self._last_channel_id = message.channel.id
            await self.mq.incoming.put(
                IncomingMessage(
                    content=message.content.strip(),
                    channel=ChannelType.DISCORD,
                    metadata={"channel_id": message.channel.id},
                )
            )

    async def _resolve_destination(self, channel_id: int | None) -> discord.abc.Messageable | None:
        if channel_id:
            channel = self.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.fetch_channel(channel_id)
                except discord.NotFound:
                    log.error(f"Discord channel {channel_id} not found")
                    return None
            return channel
        # No channel context — DM the app owner
        try:
            app_info = await self.application_info()
            return await app_info.owner.create_dm()
        except Exception as e:
            log.error(f"Discord: failed to open DM with owner: {e}")
            return None

    async def send_message(self, msg: OutgoingMessage) -> None:
        channel_id = msg.metadata.get("channel_id")
        dest = await self._resolve_destination(channel_id)
        if dest is None:
            return
        for i in range(0, len(msg.content), MAX_DISCORD_LENGTH):
            await dest.send(msg.content[i : i + MAX_DISCORD_LENGTH])

    async def process_message(self, message) -> None:
        pass  # handled by on_message discord event

    async def error_handler(self, update, context) -> None:
        log.error(f"Discord error: {context}")

    def start(self) -> None:
        log.info("Starting Discord channel...")

    async def run_polling(self) -> None:
        await discord.Client.start(self, self.token)
