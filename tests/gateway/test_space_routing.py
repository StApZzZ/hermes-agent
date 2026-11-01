"""``/space`` in a chat: a sender's lane runs under the chosen space's agent on a multiplexed gateway."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import Platform
from gateway.platforms.event import MessageEvent
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from gateway.session_identity import RoutingIdentity


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("hermes_constants._get_platform_default_hermes_home", lambda: tmp_path / ".hermes")
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    for name in ("work", "coder"):
        (home / "profiles" / name).mkdir(parents=True)
        (home / "profiles" / name / "config.yaml").write_text("{}\n", encoding="utf-8")
    from hermes_cli import spaces
    spaces.create_space("work", kind="work")
    spaces.add_agent("work", "work", primary=True)
    spaces.add_agent("work", "coder")
    return home


@pytest.fixture
def runner(root):
    runner = MagicMock(spec=GatewayRunner)
    runner.config = MagicMock(profile_routes=[], multiplex_profiles=True)
    runner._primary_profile_name = "default"
    runner._transport_owner = lambda _source: None
    for name in ("_profile_name_for_source", "_handle_space_command", "_switch_space_lane"):
        setattr(runner, name, getattr(GatewayRunner, name).__get__(runner))
    return runner


def _served(*names):
    return patch("gateway.run._multiplex_profile_homes", return_value=[(n, Path(n)) for n in names])


def _source(user_id="42", chat_id="42"):
    return SessionSource(platform=Platform.TELEGRAM, chat_id=chat_id, user_id=user_id, chat_type="dm")


def _command(runner, root, text, source):
    source._identity = RoutingIdentity(transport_profile="default", runtime_profile="default",
                                       authorization_home=root, runtime_home=root)
    return asyncio.run(runner._handle_space_command(MessageEvent(text=text, source=source)))


def test_switching_rehomes_only_the_senders_lane(runner, root):
    with _served("default", "work", "coder"):
        reply = _command(runner, root, "/space work", _source())
        assert "work" in reply
        assert runner._profile_name_for_source(_source()) == "work"
        assert runner._profile_name_for_source(_source(user_id="7", chat_id="7")) is None

        _command(runner, root, "/space work coder", _source())
        assert runner._profile_name_for_source(_source()) == "coder"

        _command(runner, root, "/space off", _source())
        assert runner._profile_name_for_source(_source()) is None


def test_switch_refuses_agents_outside_the_space_or_not_served(runner, root):
    from hermes_cli.spaces import lane_key, selected_profile

    with _served("default", "work"):
        _command(runner, root, "/space work default", _source())
        _command(runner, root, "/space work coder", _source())
        _command(runner, root, "/space nowhere", _source())
    assert selected_profile(lane_key("telegram", "default", "42", "42")) is None


def test_selection_of_an_agent_no_longer_served_falls_back_to_routes(runner, root):
    with _served("default", "work", "coder"):
        _command(runner, root, "/space work coder", _source())
    with _served("default", "work"):
        assert runner._profile_name_for_source(_source()) is None


def test_standalone_gateway_lists_spaces_but_does_not_switch(runner, root):
    runner.config.multiplex_profiles = False
    with _served("default", "work", "coder"):
        listing = _command(runner, root, "/space", _source())
        _command(runner, root, "/space work", _source())
    assert "work" in listing and "coder" in listing
    from hermes_cli.spaces import lane_key, selected_profile
    assert selected_profile(lane_key("telegram", "default", "42", "42")) is None
