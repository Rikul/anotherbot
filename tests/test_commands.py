import pytest
from unittest.mock import AsyncMock, patch

from app.channels.commands import BotCommand, CommandRegistry, _status, help_cmd_handler


def make_registry(*extra: BotCommand) -> CommandRegistry:
    r = CommandRegistry()
    r.register(BotCommand("status", "Show bot status.", _status))
    r.register(BotCommand("help", "Show this help message.", help_cmd_handler(r)))
    for cmd in extra:
        r.register(cmd)
    return r


# --- BotCommand ---

def test_botcommand_stores_fields():
    handler = AsyncMock(return_value="hi")
    cmd = BotCommand("test", "A test command.", handler)
    assert cmd.name == "test"
    assert cmd.description == "A test command."
    assert cmd.handler is handler


# --- CommandRegistry.list ---

def test_registry_list_empty_by_default():
    assert CommandRegistry().list() == []


def test_registry_list_returns_registered_commands():
    r = make_registry()
    names = {c.name for c in r.list()}
    assert {"status", "help"} <= names


def test_registry_register_overwrites_same_name():
    r = CommandRegistry()
    r.register(BotCommand("x", "first", AsyncMock(return_value="a")))
    r.register(BotCommand("x", "second", AsyncMock(return_value="b")))
    assert len(r.list()) == 1
    assert r.list()[0].description == "second"


# --- CommandRegistry.execute ---

@pytest.mark.asyncio
async def test_execute_returns_none_for_unknown_command():
    assert await CommandRegistry().execute("nope") is None


@pytest.mark.asyncio
async def test_execute_calls_handler_and_returns_result():
    handler = AsyncMock(return_value="pong")
    r = CommandRegistry()
    r.register(BotCommand("ping", "Ping.", handler))
    result = await r.execute("ping")
    assert result == "pong"
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_execute_returns_error_string_on_exception():
    async def boom():
        raise RuntimeError("oops")
    r = CommandRegistry()
    r.register(BotCommand("bad", "Broken.", boom))
    result = await r.execute("bad")
    assert isinstance(result, str)
    assert result  # non-empty


# --- _status ---

@pytest.mark.asyncio
async def test_status_includes_model_name():
    with patch("app.config._config", {"model": "my-test-model"}):
        result = await _status()
    assert "my-test-model" in result


@pytest.mark.asyncio
async def test_status_includes_uptime():
    with patch("app.config._config", {"model": "m"}):
        result = await _status()
    assert "Uptime" in result


# --- help_cmd_handler ---

@pytest.mark.asyncio
async def test_help_handler_lists_all_registered_commands():
    r = CommandRegistry()
    r.register(BotCommand("foo", "Foo command.", AsyncMock(return_value="")))
    r.register(BotCommand("bar", "Bar command.", AsyncMock(return_value="")))
    r.register(BotCommand("help", "Help.", help_cmd_handler(r)))
    result = await r.execute("help")
    assert "/foo" in result
    assert "/bar" in result
    assert "/help" in result


@pytest.mark.asyncio
async def test_help_handler_reflects_commands_registered_after_creation():
    r = CommandRegistry()
    r.register(BotCommand("help", "Help.", help_cmd_handler(r)))
    r.register(BotCommand("late", "Registered after help.", AsyncMock(return_value="")))
    result = await r.execute("help")
    assert "/late" in result
