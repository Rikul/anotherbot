import asyncio
import logging

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from .message_queue import MessageQueue
from .channel import Channel, ChannelType
from .message import OutgoingMessage, IncomingMessage

log = logging.getLogger(__name__)

MAX_SLACK_LENGTH = 3000


class SlackChannel(Channel):
    def __init__(
        self,
        mq: MessageQueue,
        bot_token: str,
        app_token: str,
        allow_from: list[str] = None,
    ) -> None:
        self.bot_token = bot_token
        self.app_token = app_token
        self.allow_from = allow_from or []
        self.mq = mq
        self.stopped = False
        self._last_channel_id: str | None = None
        self._bot_user_id: str | None = None
        self.app = AsyncApp(token=bot_token)
        mq.register(self, self.send_message)
        self._register_handlers()

    def _register_handlers(self) -> None:
        self.app.event("message")(self._handle_message)

    @property
    def has_stopped(self) -> bool:
        return self.stopped

    def clear_stopped(self) -> None:
        self.stopped = False

    @property
    def channel_type(self) -> ChannelType:
        return ChannelType.SLACK

    @property
    def default_metadata(self) -> dict:
        return {"channel_id": self._last_channel_id} if self._last_channel_id else {}

    async def _handle_message(self, event: dict, say) -> None:
        # ignore bot messages and message edits/deletes
        if event.get("bot_id") or event.get("subtype"):
            return

        user_id = event.get("user", "")
        if self.allow_from and user_id not in self.allow_from:
            log.warning(f"Slack: ignoring message from unauthorized user id={user_id}")
            await say("Sorry, you are not authorized to use this bot.")
            return

        content = (event.get("text") or "").strip()
        if not content:
            return

        channel_id = event.get("channel", "")
        metadata = {"channel_id": channel_id}

        if content.startswith("/"):
            cmd_name = content[1:].split(maxsplit=1)[0].lower()
            if cmd_name == "whoami":
                await self.send_message(OutgoingMessage(
                    content=f"Your user ID is {user_id}.",
                    channel=ChannelType.SLACK,
                    metadata=metadata,
                ))
                return
            if cmd_name == "stop":
                self.stopped = True
                await self.send_message(OutgoingMessage(
                    content="Stopped.",
                    channel=ChannelType.SLACK,
                    metadata=metadata,
                ))
                return

        self._last_channel_id = channel_id
        await self.mq.incoming.put(
            IncomingMessage(
                content=content,
                channel=ChannelType.SLACK,
                metadata=metadata,
            )
        )

    async def send_message(self, msg: OutgoingMessage) -> None:
        channel_id = msg.metadata.get("channel_id")
        if not channel_id:
            log.error("Slack: cannot send message, no channel_id in metadata")
            return
        for i in range(0, len(msg.content), MAX_SLACK_LENGTH):
            chunk = msg.content[i : i + MAX_SLACK_LENGTH]
            await self.app.client.chat_postMessage(channel=channel_id, text=chunk)

    async def process_message(self, message) -> None:
        pass  # handled by slack bolt event handlers

    def error_handler(self, update, context) -> None:
        log.error(f"Slack error: {context}")

    def start(self) -> None:
        log.info("Starting Slack channel...")

    async def run_polling(self) -> None:
        handler = AsyncSocketModeHandler(self.app, self.app_token)
        await handler.start_async()
        try:
            await asyncio.Event().wait()
        finally:
            await handler.close_async()
