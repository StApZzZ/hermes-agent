"""Unit tests for the standalone SSH development bridge (no SSH server needed)."""

import argparse
import json

import pytest

from hermes_cli.subcommands import remote


def parse(*argv):
    root = argparse.ArgumentParser()
    subs = root.add_subparsers(dest="command", required=True)
    remote.build_remote_parser(subs)
    return root.parse_args(["remote", *argv])


def test_doctor_is_read_only_and_uses_quoted_path(monkeypatch):
    called = []
    monkeypatch.setattr(remote, "_run", lambda argv: called.append(argv) or 0)
    name = "/home/dev/project' && touch /tmp/pwned; #"
    args = parse("doctor", "devbox", "--workspace", name)
    assert args.func(args) == 0
    assert called[0][:2] == ["ssh", "-T"]
    assert "touch /tmp/pwned" in called[0][-1]
    assert "'\\''" in called[0][-1] or "'\"'\"'" in called[0][-1]


def test_host_validation_rejects_ssh_options():
    assert remote._checked_host("devbox") == "devbox"
    with pytest.raises(ValueError):
        remote._checked_host("-oProxyCommand=evil")
    with pytest.raises(ValueError):
        remote._checked_host("host;id")


def test_workspace_must_be_absolute():
    with pytest.raises(ValueError):
        remote._checked_workspace("~/project")
    assert remote._checked_workspace("/home/dev/./project") == "/home/dev/project"


def test_open_invokes_vscode_not_wsl(monkeypatch):
    called = []
    monkeypatch.setattr(remote.shutil, "which", lambda name: "C:/VSCode/bin/code.cmd" if name == "code" else None)
    monkeypatch.setattr(remote, "_run", lambda argv: called.append(argv) or 0)
    args = parse("open", "devbox", "--workspace", "/srv/repo", "--new-window")
    assert args.func(args) == 0
    assert called == [["C:/VSCode/bin/code.cmd", "--remote", "ssh-remote+devbox", "/srv/repo/", "--new-window"]]


def test_claude_uses_reconnectable_remote_tmux(monkeypatch):
    called = []
    monkeypatch.setattr(remote, "_run", lambda argv: called.append(argv) or 0)
    args = parse("claude", "devbox", "--workspace", "/srv/repo", "--session", "feature42", "--resume")
    assert args.func(args) == 0
    assert called[0][0:2] == ["ssh", "-tt"]
    assert "tmux new-session -A" in called[0][-1]
    assert "feature42" in called[0][-1]
    assert "claude --resume" in called[0][-1]


def test_acp_pipe_does_not_allocate_tty(monkeypatch):
    called = []
    monkeypatch.setattr(remote, "_run", lambda argv: called.append(argv) or 0)
    args = parse("acp", "devbox", "--workspace", "/srv/repo")
    assert args.func(args) == 0
    assert called[0][1] == "-T"
    assert "BatchMode=yes" in called[0]
    assert "exec hermes acp" in called[0][-1]


def test_config_modes(capsys):
    args = parse("config", "devbox", "--workspace", "/srv/repo")
    assert args.func(args) == 0
    default = json.loads(capsys.readouterr().out)
    assert default["acp.agents"]["Hermes Remote"] == {"command": "hermes", "args": ["acp"]}
    args = parse("config", "devbox", "--workspace", "/srv/repo", "--proxy")
    assert args.func(args) == 0
    proxy = json.loads(capsys.readouterr().out)
    assert proxy["acp.agents"]["Hermes Remote"]["args"] == ["remote", "acp", "devbox", "--workspace", "/srv/repo"]
