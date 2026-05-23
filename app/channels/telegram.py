import asyncio
import logging

from .commands import CommandRegistry, BotCommand, make_status_cmd, help_cmd, model_cmd
from .message_queue import MessageQueue
from .channel import Channel, ChannelType
from .message import OutgoingMessage, IncomingMessage

from telegram import Update, constants
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

log = logging.getLogger(__name__)

MAX_TG_LENGTH = 2048


class TelegramChannel(Channel):
    def __init__(
        self, mq: MessageQueue, bot_token: str, allow_from: list[int] = None
    ) -> None:
        self.bot_token = bot_token
        self.allow_from = allow_from or []
        self.mq = mq
        self.stopped = False
        mq.register(self, self.send_message)
        self.registry = CommandRegistry()
        self.registry.register(BotCommand("model",  "Get or set the LLM model. Usage: /model [name]", model_cmd))
        self.registry.register(BotCommand("status", "Show bot status.", make_status_cmd(ChannelType.TELEGRAM.value)))
        self.registry.register(BotCommand("stop",   "Pause the bot.", self._stop_cmd))
        self.registry.register(BotCommand("help",   "Show this help message.", help_cmd(self.registry)))

    @property
    def has_stopped(self) -> bool:
        return self.stopped

    def clear_stopped(self) -> None:
        self.stopped = False
    
    @property
    def channel_type(self) -> ChannelType:
        return ChannelType.TELEGRAM

    @property
    def default_metadata(self) -> dict:
        return {"chat_id": self.allow_from[0]} if self.allow_from else {}

    async def error_handler(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        log.error("Telegram error:", exc_info=context.error)

        # optionally notify user if update was from a chat
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "⚠ An error occurred, please try again."
            )

    async def _stop_cmd(self, args: str = "") -> str:
        self.stopped = True
        log.info("Received /stop in Telegram channel, setting stopped=True.")
        return "Stopped."

    async def command_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message and update.message.text:
            content = update.message.text.strip()
            if content.startswith("/"):
                parts = content[1:].split(maxsplit=1)
                cmd_name = parts[0].lower()
                args = parts[1] if len(parts) > 1 else ""
                metadata = {"chat_id": update.effective_chat.id}
                if cmd_name == "whoami":
                    text = f"Your user ID is {update.effective_user.id} and your name is {update.effective_user.first_name}."
                    await self.send_message(OutgoingMessage(content=text, channel=ChannelType.TELEGRAM, metadata=metadata))
                    return
                reply = await self.registry.execute(cmd_name, args)
                if reply is not None:
                    await self.send_message(OutgoingMessage(content=reply, channel=ChannelType.TELEGRAM, metadata=metadata))
                else:
                    await self.mq.incoming.put(IncomingMessage(
                        content=content, channel=ChannelType.TELEGRAM, metadata=metadata
                    ))

    async def send_message(self, message: OutgoingMessage) -> None:
        # This function is called by the MessageQueue when there is an outgoing message for this channel
        # It should deliver the message to the user via Telegram API

        # For simplicity, let's assume we are sending messages back to the same chat where they came from
        # In a real implementation, you would want to track which chat/user sent which message and route responses accordingly

        chat_id = message.metadata.get("chat_id")
        if not chat_id:
            log.error("Cannot send Telegram message: no chat_id in message metadata")
            return
        log.info(f"Sending message to Telegram chat {chat_id}: {message.content}")
        for i in range(0, len(message.content), MAX_TG_LENGTH):
            chunk = message.content[i : i + MAX_TG_LENGTH]
            await self.app.bot.send_message(chat_id=chat_id, text=chunk)

    async def process_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        user_id = update.effective_user.id if update.effective_user else None
        if user_id is None or (self.allow_from and user_id not in self.allow_from):
            log.warning(
                f"Received message from unauthorized user id={user_id}, ignoring."
            )
            await update.message.reply_text(
                "Sorry, you are not authorized to use this bot."
            )
            return

        if update.message and update.message.text:
            content = update.message.text.strip()
            if content != "":
                await self.mq.incoming.put(
                    IncomingMessage(
                        content=content,
                        channel=ChannelType.TELEGRAM,
                        metadata={"chat_id": update.effective_chat.id},
                    )
                )
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
            else:
                await update.message.reply_text("Please send a non-empty message.")
        else:
            await update.message.reply_text("Sorry, I can only process text messages.")

    def start(self) -> None:
        log.info("Starting Telegram channel...")

        self.app = (
            ApplicationBuilder()
            .token(self.bot_token)
            .connect_timeout(30)
            .read_timeout(30)
            .write_timeout(30)
            .build()
        )

        self.app.add_handler(MessageHandler(filters.COMMAND, self.command_handler))
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.process_message)
        )
        self.app.add_error_handler(self.error_handler)

    async def run_polling(self) -> None:
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
        try:
            await asyncio.Event().wait()
        finally:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
