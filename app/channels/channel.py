from abc import ABC, abstractmethod
from enum import Enum

class ChannelType(Enum):
    CLI = "cli"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    WEB = "web"

class Channel(ABC):

    @abstractmethod
    async def send_message(self, message) -> None:
        pass

    @abstractmethod
    async def process_message(self, message) -> None:
        pass

    @abstractmethod
    def error_handler(self, update, context) -> None:
        pass

    @property
    @abstractmethod
    def channel_type(self) -> ChannelType:
        pass

    @property
    @abstractmethod
    def has_stopped(self) -> bool:
        pass

    @abstractmethod
    def clear_stopped(self) -> None:
        pass

    @property
    def default_metadata(self) -> dict:
        return {}
