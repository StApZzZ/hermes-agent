"""The agent's space (fellow agents + shared notes) is part of its session-start memory block."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("hermes_constants._get_platform_default_hermes_home", lambda: tmp_path / ".hermes")
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    for name in ("coder", "home"):
        (home / "profiles" / name).mkdir(parents=True)
        (home / "profiles" / name / "config.yaml").write_text("{}\n", encoding="utf-8")
    return home


def _agent(home: Path):
    return SimpleNamespace(
        _memory_store=MagicMock(format_for_system_prompt=lambda kind: f"{kind.upper()} BLOCK"),
        _memory_enabled=True, _user_profile_enabled=True, _memory_manager=None,
        _session_db=SimpleNamespace(db_path=str(home / "state.db")))


def test_member_agent_reads_its_space_notes_after_its_own_memory(root):
    from agent.system_prompt import _memory_parts
    from hermes_cli import spaces

    spaces.create_space("acme", kind="project", title="ACME")
    spaces.add_agent("acme", "coder")
    spaces.add_note("acme", "The staging database is read-only")

    parts = _memory_parts(_agent(root / "profiles" / "coder"))

    assert parts[:2] == ["MEMORY BLOCK", "USER BLOCK"]
    assert "SPACE: ACME (acme) [project]" in parts[2]
    assert "The staging database is read-only" in parts[2]
    assert _memory_parts(_agent(root / "profiles" / "home")) == ["MEMORY BLOCK", "USER BLOCK"]


def test_shared_memory_follows_the_agent_memory_outside_any_space(root):
    from agent.system_prompt import _memory_parts
    from hermes_cli import memory_scopes

    memory_scopes.add_entries(memory_scopes.shared_store(), ["The user's name is Alex"])

    parts = _memory_parts(_agent(root / "profiles" / "home"))

    assert parts[:2] == ["MEMORY BLOCK", "USER BLOCK"]
    assert "SHARED MEMORY" in parts[2] and "The user's name is Alex" in parts[2]
