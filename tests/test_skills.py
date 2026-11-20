"""Skill discovery, the use_skill tool, /skills and /<skill> invocation."""

import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.channels.commands import (
    BotCommand,
    CommandRegistry,
    skill_completions,
    skills_cmd,
)
from app.channels.message import IncomingMessage
from app.channels.channel import ChannelType
from app.core.agent import get_default_sys_prompt
from app.core.tool_calls import _HELPER_AGENT_TOOLS, is_read_only, tool_registry
from app.infra import skills
from app.tools.skill import UseSkillTool

REAL_BUILTIN_DIR = skills.BUILTIN_SKILLS_DIR


def write_skill(root: Path, folder: str, text: str) -> Path:
    path = root / folder
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(text, encoding="utf-8")
    return path


def fm(name: str, description: str, body: str = "Do the thing.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"


@pytest.fixture
def dirs(monkeypatch, tmp_path):
    """Empty built-in and user skill dirs."""
    builtin, user = tmp_path / "builtin", tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    monkeypatch.setattr(skills, "BUILTIN_SKILLS_DIR", builtin)
    monkeypatch.setattr(skills, "USER_SKILLS_DIR", user)
    return builtin, user


# --- parsing -------------------------------------------------------------------


def test_parse_frontmatter_and_body():
    fields, body = skills.parse_skill_file(
        '---\nname: demo\ndescription: "Quoted: with colon"\n---\n\n# Title\nText\n'
    )
    assert fields == {"name": "demo", "description": "Quoted: with colon"}
    assert body == "# Title\nText"


def test_parse_without_frontmatter_returns_whole_text():
    assert skills.parse_skill_file("# Just a body\n") == ({}, "# Just a body\n")


def test_parse_unterminated_frontmatter_is_body():
    text = "---\nname: x\nno end"
    assert skills.parse_skill_file(text) == ({}, text)


# --- discovery -------------------------------------------------------------------


def test_discovers_builtin_and_user_skills_sorted(dirs):
    builtin, user = dirs
    write_skill(builtin, "zeta", fm("zeta", "Last one."))
    write_skill(user, "alpha", fm("alpha", "User one."))
    found = skills.discover_skills()
    assert list(found) == ["alpha", "zeta"]
    assert found["alpha"].source == "user"
    assert found["zeta"].source == "built-in"


def test_user_skill_overrides_builtin(dirs):
    builtin, user = dirs
    write_skill(builtin, "browser", fm("browser", "Built-in."))
    write_skill(user, "browser", fm("browser", "Mine."))
    skill = skills.discover_skills()["browser"]
    assert (skill.source, skill.description) == ("user", "Mine.")


def test_old_style_skill_uses_folder_name_and_first_paragraph(dirs):
    builtin, _ = dirs
    write_skill(
        builtin,
        "legacy",
        "# Legacy Skill\n\n## Overview\nDoes legacy\nthings well.\n\nMore text.\n",
    )
    skill = skills.discover_skills()["legacy"]
    assert skill.description == "Does legacy things well."


def test_invalid_names_and_folders_without_skill_md_are_skipped(dirs):
    builtin, _ = dirs
    write_skill(builtin, "bad", fm("Bad Name!", "x"))
    (builtin / "empty").mkdir()
    (builtin / "not-a-dir.md").write_text("x")
    assert skills.discover_skills() == {}


def test_long_description_is_truncated(dirs):
    builtin, _ = dirs
    write_skill(builtin, "long", fm("long", "word " * 200))
    desc = skills.discover_skills()["long"].description
    assert len(desc) == skills.MAX_DESCRIPTION_CHARS and desc.endswith("…")


def test_missing_user_dir_is_fine(dirs, monkeypatch, tmp_path):
    monkeypatch.setattr(skills, "USER_SKILLS_DIR", tmp_path / "nope")
    assert skills.discover_skills() == {}


# --- load_skill / use_skill ----------------------------------------------------------


def test_use_skill_returns_body_dir_and_files(dirs):
    builtin, _ = dirs
    path = write_skill(builtin, "demo", fm("demo", "Demo.", "# Steps\n1. go"))
    (path / "scripts").mkdir()
    (path / "scripts" / "run.py").write_text("print(1)")
    (path / ".hidden").write_text("x")
    out = UseSkillTool.call(name="Demo")
    assert out.startswith('<skill name="demo" source="built-in">')
    assert f"Skill directory: {path}" in out
    assert "- scripts/run.py" in out
    assert ".hidden" not in out and "- SKILL.md" not in out
    assert "# Steps\n1. go" in out
    assert "description: Demo." not in out  # frontmatter stripped


def test_use_skill_unknown_lists_available(dirs):
    builtin, _ = dirs
    write_skill(builtin, "one", fm("one", "One."))
    assert UseSkillTool.call(name="two") == (
        "Error: no skill named 'two'. Available skills: one"
    )


def test_use_skill_is_registered_read_only_and_available_to_helpers():
    assert tool_registry["use_skill"] is UseSkillTool
    assert is_read_only("use_skill")
    assert "use_skill" in _HELPER_AGENT_TOOLS
    assert "get_skills_dir" not in tool_registry


# --- prompt -------------------------------------------------------------------------


def test_prompt_section_lists_skills(dirs):
    builtin, _ = dirs
    write_skill(builtin, "demo", fm("demo", "Demo skill."))
    section = skills.skills_prompt_section()
    assert "## Skills" in section
    assert "<skills>\n- demo: Demo skill.\n</skills>" in section


def test_prompt_section_empty_without_skills(dirs):
    assert skills.skills_prompt_section() == ""


def test_prompt_section_is_capped(dirs, monkeypatch):
    builtin, _ = dirs
    for i in range(5):
        write_skill(builtin, f"s{i}", fm(f"s{i}", "x"))
    monkeypatch.setattr(skills, "MAX_PROMPT_SKILLS", 3)
    section = skills.skills_prompt_section()
    assert "- s2: x" in section and "- s3: x" not in section
    assert "(2 more skills not listed" in section


def test_system_prompt_includes_builtin_skills():
    prompt = get_default_sys_prompt()
    for name in ("browser", "code-review", "planning", "long-tasks"):
        assert f"\n- {name}: " in prompt


# --- shipped skills ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "folder", sorted(p.name for p in REAL_BUILTIN_DIR.iterdir() if p.is_dir())
)
def test_shipped_skills_have_valid_frontmatter(folder):
    text = (REAL_BUILTIN_DIR / folder / "SKILL.md").read_text(encoding="utf-8")
    fields, body = skills.parse_skill_file(text)
    assert fields.get("name") == folder
    assert re.match(skills.NAME_RE, fields["name"])
    assert 20 <= len(fields.get("description", "")) <= skills.MAX_DESCRIPTION_CHARS
    assert body.strip()


# --- /skills and /<skill> ------------------------------------------------------------


def test_skill_invocation_with_request(dirs):
    builtin, _ = dirs
    write_skill(builtin, "browser", fm("browser", "Browse."))
    assert skills.skill_invocation("/Browser  screenshot\nexample.com ") == (
        'Use the "browser" skill for this request:\n\nscreenshot\nexample.com'
    )
    assert skills.skill_invocation("/browser\nmultiline first").endswith(
        "multiline first"
    )


def test_skill_invocation_without_request(dirs):
    builtin, _ = dirs
    write_skill(builtin, "planning", fm("planning", "Plan."))
    assert skills.skill_invocation("/planning").startswith('Use the "planning" skill.')


@pytest.mark.parametrize("text", ["/nope", "browser", "/", "/../x", ""])
def test_skill_invocation_returns_none(dirs, text):
    builtin, _ = dirs
    write_skill(builtin, "browser", fm("browser", "Browse."))
    assert skills.skill_invocation(text) is None


@pytest.mark.asyncio
async def test_skills_command_lists_skills(dirs):
    builtin, user = dirs
    write_skill(builtin, "browser", fm("browser", "Browse."))
    write_skill(user, "mine", fm("mine", "Mine."))
    out = await skills_cmd("")
    assert "/browser — Browse." in out
    assert "/mine (user) — Mine." in out
    assert str(user) in out


@pytest.mark.asyncio
async def test_skills_command_without_skills(dirs):
    assert (await skills_cmd("")).startswith("No skills found.")


def test_skill_completions_skip_registered_commands(dirs):
    builtin, _ = dirs
    write_skill(builtin, "new", fm("new", "Shadowed."))
    write_skill(builtin, "browser", fm("browser", "Browse."))
    registry = CommandRegistry()
    registry.register(BotCommand("new", "New conversation.", AsyncMock()))
    assert skill_completions(registry) == [("browser", "skill: Browse.")]


async def _run_one(agent, mq, content):
    await mq.incoming.put(
        IncomingMessage(content=content, channel=ChannelType.TELEGRAM, metadata={})
    )
    task = asyncio.create_task(agent.process_incoming())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_background_agent_runs_skill_as_a_turn(dirs):
    from tests.test_background_agent import make_agent

    builtin, _ = dirs
    write_skill(builtin, "browser", fm("browser", "Browse."))
    agent, _, mq = make_agent()
    with patch.object(agent, "agent_loop", AsyncMock()) as loop:
        await _run_one(agent, mq, "/browser open example.com")
    loop.assert_awaited_once()
    assert loop.await_args.args[0] == (
        'Use the "browser" skill for this request:\n\nopen example.com'
    )
    assert mq.outgoing.empty()


@pytest.mark.asyncio
async def test_background_agent_command_beats_skill_and_unknown_still_reported(dirs):
    from tests.test_background_agent import make_agent

    builtin, _ = dirs
    write_skill(builtin, "help", fm("help", "Shadowed by /help."))
    agent, _, mq = make_agent()
    with patch.object(agent, "agent_loop", AsyncMock()) as loop:
        await _run_one(agent, mq, "/help")
        await _run_one(agent, mq, "/nosuch")
    loop.assert_not_awaited()
    assert (await mq.outgoing.get()).content.startswith("Available commands:")
    assert (await mq.outgoing.get()).content == "Unknown command: /nosuch"


@pytest.mark.asyncio
async def test_background_agent_skill_turn_is_not_a_command_reply(dirs):
    """The web channel marks every "/" message is_command; a skill turn isn't one."""
    from tests.test_background_agent import make_agent

    builtin, _ = dirs
    write_skill(builtin, "planning", fm("planning", "Plan."))
    agent, _, mq = make_agent()
    with patch.object(agent, "agent_loop", AsyncMock()) as loop:
        await mq.incoming.put(
            IncomingMessage(
                content="/planning a feature",
                channel=ChannelType.WEB,
                metadata={"websocket_id": "t", "is_command": True},
            )
        )
        task = asyncio.create_task(agent.process_incoming())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    assert loop.await_args.args[1]["is_command"] is False


def test_skill_file_listing_prunes_hidden_dirs_and_stops_at_cap(dirs, monkeypatch):
    builtin, _ = dirs
    path = write_skill(builtin, "big", fm("big", "Big."))
    for sub in (".venv", "__pycache__"):
        (path / sub).mkdir()
        (path / sub / "x.py").write_text("")
    (path / "refs").mkdir()
    for i in range(5):
        (path / "refs" / f"r{i}.md").write_text("")
    monkeypatch.setattr(skills, "MAX_LISTED_FILES", 2)
    out = UseSkillTool.call(name="big")
    assert "- refs/r0.md\n- refs/r1.md\n- …" in out
    assert ".venv" not in out and "__pycache__" not in out and "r3.md" not in out
