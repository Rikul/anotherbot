"""Message envelopes passed between channels and agents via ``MessageQueue``."""

from dataclasses import dataclass, field

from .channel import Channel


@dataclass
class IncomingMessage:
    """A user message on its way to the agent.

    ``channel`` is the ``ChannelType`` it came from; ``metadata`` carries the
    reply target (``chat_id``, ``channel_id`` or ``websocket_id``) and, for the
    web channel, ``is_command`` and uploaded ``files``.
    """

    content: str
    channel: Channel
    metadata: dict = field(default_factory=dict)


@dataclass
class OutgoingMessage:
    """A message on its way to a user.

    ``channel`` is the ``Channel`` instance whose registered delivery function
    sends it; ``metadata`` is the reply target taken from the incoming message.
    """

    content: str
    channel: Channel
    metadata: dict = field(default_factory=dict)
