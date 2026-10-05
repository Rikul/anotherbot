"""The ``Channel`` interface every chat front end (Telegram, Discord, web) implements."""

from abc import ABC, abstractmethod
from enum import Enum


class ChannelType(Enum):
    """Known channel kinds; ``.value`` is the name used in the DB, config and runtime keys."""

    CLI = "cli"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    WEB = "web"


class Channel(ABC):
    """A chat front end that receives user messages and delivers agent replies.

    Each channel registers ``send_message`` with its own ``MessageQueue`` and
    puts user input on that queue's ``incoming`` queue.
    """

    @abstractmethod
    async def send_message(self, message) -> None:
        """Deliver one outgoing message to the user.

        Called by ``MessageQueue.process_outgoing``.
        """

    @abstractmethod
    async def process_message(self, message) -> None:
        """Handle a raw platform message (unused by channels that handle input inline)."""

    @abstractmethod
    async def error_handler(self, update, context) -> None:
        """Handle an error raised by the platform library."""

    @property
    @abstractmethod
    def channel_type(self) -> ChannelType:
        """This channel's ``ChannelType``."""

    @property
    @abstractmethod
    def has_stopped(self) -> bool:
        """True after the user asked the bot to stop (``/stop``); checked by the agent loop."""

    @abstractmethod
    def clear_stopped(self) -> None:
        """Reset the stop flag once the current agent turn has ended."""

    @property
    def default_metadata(self) -> dict:
        """Metadata for messages with no reply target, e.g. scheduled-task results."""
        return {}
