import pathlib

from app.tools.get_skills_dir import GetSkillsDirTool

get_skills_dir = GetSkillsDirTool.call


def test_get_skills_dir_returns_string():
    result = get_skills_dir()
    assert isinstance(result, str)


def test_get_skills_dir_is_absolute():
    result = get_skills_dir()
    assert pathlib.Path(result).is_absolute()


def test_get_skills_dir_ends_with_skills():
    result = get_skills_dir()
    assert result.endswith("skills")


def test_get_skills_dir_exists():
    result = get_skills_dir()
    assert pathlib.Path(result).exists()
