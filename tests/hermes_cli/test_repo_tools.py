"""Behaviour of `hermes repos`: real folders on disk, the real config writer, nothing faked but $HOME."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import agent.skill_utils as su
from hermes_cli import repo_tools


def put(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def skill(path: Path, name: str) -> None:
    put(path / name / "SKILL.md", f"---\nname: {name}\ndescription: {name}\n---\nbody\n")


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    hermes = home / ".hermes"
    put(hermes / "config.yaml", "skills:\n  external_dirs: []\n")
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    monkeypatch.setattr(Path, "home", lambda: home)
    su._external_dirs_cache_clear()
    root = home / "repos"

    # An MCP server and a CLI from one Python project, with its venv and settings.
    maps = root / "maps"
    put(maps / "pyproject.toml", '[project]\nname = "maps"\n[project.scripts]\n'
        'maps-mcp = "maps.server:main"\nmaps-search = "maps.cli:main"\nmaps-bot = "maps.bot:main"\n')
    put(maps / ".venv/bin/maps-mcp")
    put(maps / ".venv/bin/maps-search")
    put(maps / ".venv/bin/maps-bot")
    put(maps / ".env.example", "TOKEN=\n")
    put(maps / ".env", "TOKEN=x\n")
    put(maps / "README.md", "# Maps\n\nFind [companies](https://x) without a website.\n")
    skill(maps / ".venv/lib/python3.12/site-packages/typer/.agents/skills", "vendored")

    # A single-file server a user unit already serves over HTTP.
    tg = root / "telegram-mcp"
    (tg / ".git").mkdir(parents=True)
    put(tg / "server.py", '# /// script\n# dependencies = ["mcp"]\n# ///\nfrom mcp.server.fastmcp import FastMCP\n'
        'URL = "http://127.0.0.1:8733/mcp"\n')
    put(home / ".config/systemd/user/telegram-mcp.service",
        f"[Service]\nExecStart=%h/.local/bin/uv run --script {tg}/server.py --http\n")

    # Project skills in Claude Code's folder, a skill pack, and a Node server not built yet.
    vpn = root / "vpn"
    (vpn / ".git").mkdir(parents=True)
    skill(vpn / ".claude/skills", "deploy-panel")
    skill(vpn / "engineering-skills/delivery", "release-notes")
    put(vpn / "package.json", json.dumps({"name": "mcp-vpn", "bin": {"mcp-vpn": "./dist/index.js"},
                                          "dependencies": {"@modelcontextprotocol/sdk": "^1"}}))

    # A Hermes checkout among them: its bundled skills are Hermes's own.
    hermes_src = root / "hermes-agent"
    (hermes_src / ".git").mkdir(parents=True)
    put(hermes_src / "run_agent.py")
    (hermes_src / "hermes_cli").mkdir()
    skill(hermes_src / "skills/devops", "bundled")
    yield {"home": home, "hermes": hermes, "root": root, "maps": maps, "tg": tg, "vpn": vpn}
    su._external_dirs_cache_clear()


def config(world) -> dict:
    import hermes_yaml
    return hermes_yaml.safe_load((world["hermes"] / "config.yaml").read_text()) or {}


def test_apply_sets_up_servers_skills_and_tools_from_the_repos(world):
    repo_tools.apply(repo_tools.scan([world["root"]]))

    cfg = config(world)
    servers = cfg["mcp_servers"]
    assert servers["maps"] == {"command": str(world["maps"] / ".venv/bin/maps-mcp"), "args": [],
                               "cwd": str(world["maps"])}
    assert servers["telegram"] == {"url": "http://127.0.0.1:8733/mcp"}
    assert "vpn" not in servers  # not built: nothing to launch yet
    assert cfg["skills"]["trusted_project_dirs"] == [str(world["vpn"])]
    external = cfg["skills"]["external_dirs"]
    assert str(world["vpn"] / "engineering-skills") in external
    assert not any("hermes-agent" in d or ".venv" in d for d in external)

    generated = (repo_tools.repo_skills_dir() / "maps" / "SKILL.md").read_text()
    assert str(world["maps"] / ".venv/bin/maps-search") in generated
    assert "maps-bot" not in generated and "companies without a website" in generated
    assert str(repo_tools.repo_skills_dir()) in external


def test_claude_code_project_skills_load_once_the_repo_is_trusted(world, monkeypatch):
    repo_tools.apply(repo_tools.scan([world["root"]]))
    su._external_dirs_cache_clear()
    monkeypatch.chdir(world["vpn"])

    assert (world["vpn"] / ".claude" / "skills").resolve() in su.get_project_skills_dirs()


def test_a_server_without_its_settings_is_added_switched_off(world):
    (world["maps"] / ".env").unlink()

    repo_tools.apply(repo_tools.scan([world["root"]]))

    assert config(world)["mcp_servers"]["maps"]["enabled"] is False


def test_rerun_updates_its_own_entries_and_never_replaces_yours(world):
    put(world["hermes"] / "config.yaml",
        "mcp_servers:\n  telegram:\n    url: https://my.example/mcp\nskills:\n  external_dirs: []\n")
    repo_tools.apply(repo_tools.scan([world["root"]]))
    (world["maps"] / ".env").unlink()

    report = repo_tools.apply(repo_tools.scan([world["root"]]))

    servers = config(world)["mcp_servers"]
    assert servers["telegram"] == {"url": "https://my.example/mcp"}
    assert servers["maps"]["enabled"] is False
    assert any("keep  mcp telegram" in line for line in report)
    assert config(world)["skills"]["trusted_project_dirs"] == [str(world["vpn"])]


def test_scan_changes_nothing(world):
    before = (world["hermes"] / "config.yaml").read_text()

    findings = repo_tools.scan([world["root"]])

    assert {f.repo.name for f in findings} == {"maps", "telegram-mcp", "vpn"}
    assert (world["hermes"] / "config.yaml").read_text() == before
    assert not repo_tools.repo_skills_dir().exists()


def test_by_default_it_reads_the_folder_holding_the_current_project(world, monkeypatch):
    monkeypatch.chdir(world["maps"])  # a Python project without .git

    assert repo_tools.default_roots() == [world["root"].resolve()]


def test_a_path_can_name_one_project_and_filters_narrow_what_is_set_up(world):
    one = repo_tools.scan([world["maps"]])
    assert [f.repo.name for f in one] == ["maps"]

    picked = repo_tools.scan([world["root"]], only=["vpn", "maps"], kinds=["skills"])
    assert [f.repo.name for f in picked] == ["vpn"]  # maps offers no skills
    assert not picked[0].mcp and not picked[0].commands

    repo_tools.apply(repo_tools.scan([world["root"]], kinds=["mcp"]))
    cfg = config(world)
    assert set(cfg["mcp_servers"]) == {"maps", "telegram"}
    assert not cfg["skills"].get("trusted_project_dirs")
