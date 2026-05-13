import asyncio
import logging

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

    async def whoami(self,update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        reply_text = f"Your user ID is {update.effective_user.id} and your name is {update.effective_user.first_name}."
        await update.message.reply_text(reply_text)
        
    async def help(self,update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        List available commands and their descriptions.
        """
        reply_text = ("Available commands:\n"
                    "/whoami - Display your user ID and name.\n"
                    "/help - Show this help message.\n"
                    "Just send any text message to interact with the bot.")
        await update.message.reply_text(reply_text)


    async def stop(self,update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Stop the bot gracefully.
        """
        self.stopped = True
        await update.message.reply_text("Stopped.")
        log.info("Received /stop in Telegram channel, setting stopped=True.")

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
        self.app.add_handler(CommandHandler("whoami", self.whoami))
        self.app.add_handler(CommandHandler("stop", self.stop))
        self.app.add_handler(CommandHandler("help", self.help))

        # Add handler for unrecognized commands
        self.app.add_handler(MessageHandler(filters.COMMAND, self.help))
        
        # Add handler for text messages that are not commands
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
