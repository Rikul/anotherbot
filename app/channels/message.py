from .channel import Channel
from dataclasses import dataclass, field


@dataclass
class IncomingMessage:
    content: str
    channel: Channel
    metadata: dict = field(default_factory=dict)


@dataclass
class OutgoingMessage:
    content: str
    channel: Channel
    metadata: dict = field(default_factory=dict)
