"""Unit tests for the standalone SSH development bridge (no SSH server needed)."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_cli.subcommands import remote


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))


def parse(*argv):
    root = argparse.ArgumentParser()
    subs = root.add_subparsers(dest="command", required=True)
    remote.build_remote_parser(subs)
    return root.parse_args(["remote", *argv])


def test_doctor_is_read_only_and_uses_quoted_path(monkeypatch):
    called = []
    monkeypatch.setattr(remote, "_run", lambda argv, **kw: called.append(argv) or 0)
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
    monkeypatch.setattr(remote.shutil, "which", lambda name: "/opt/vscode/bin/code" if name == "code" else None)
    monkeypatch.setattr(remote, "_run", lambda argv, **kw: called.append(argv) or 0)
    args = parse("open", "devbox", "--workspace", "/srv/repo", "--new-window")
    assert args.func(args) == 0
    assert called == [["/opt/vscode/bin/code", "--remote", "ssh-remote+devbox", "/srv/repo/", "--new-window"]]


def test_claude_uses_reconnectable_remote_tmux(monkeypatch):
    called = []
    monkeypatch.setattr(remote, "_run", lambda argv, **kw: called.append(argv) or 0)
    args = parse("claude", "devbox", "--workspace", "/srv/repo", "--session", "feature42", "--resume")
    assert args.func(args) == 0
    assert called[0][0:2] == ["ssh", "-tt"]
    assert "new-session -A" in called[0][-1]
    assert "feature42" in called[0][-1]
    assert "claude --resume" in called[0][-1]


def test_acp_pipe_does_not_allocate_tty(monkeypatch):
    called = []
    monkeypatch.setattr(remote, "_run", lambda argv, **kw: called.append(argv) or 0)
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
    assert proxy["acp.agents"]["Hermes Remote"]["command"] == "ssh"
    assert "exec hermes acp" in proxy["acp.agents"]["Hermes Remote"]["args"][-1]
    assert proxy["acp.defaultWorkingDirectory"] == "/srv/repo"


@pytest.mark.parametrize("value", ["~/repo", "relative", "C:/repo", "/repo\rX", "/x\n", "/x\0", "/x/../y"])
def test_invalid_workspace_is_reported_on_stderr(value, capsys):
    assert remote.main(["acp", "devbox", "--workspace", value]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "absolute Linux" in captured.err


def test_missing_workspace_is_actionable(capsys):
    assert remote.main(["open", "devbox"]) == 2
    assert "--workspace" in capsys.readouterr().err


def test_workspace_sessions_do_not_collide():
    first = remote._session_name("devbox", "/srv/one", None, "claude")
    assert first == remote._session_name("devbox", "/srv/one", None, "claude")
    assert first != remote._session_name("devbox", "/srv/two", None, "claude")
    assert first != remote._session_name("devbox", "/srv/one", None, "hermes")
    assert remote._session_name("devbox", "/srv/one", "work", "claude") != remote._session_name("devbox", "/srv/two", "work", "claude")


@pytest.mark.parametrize("name", ["a.b", "a:b", "-x", "x;id", "x" * 65])
def test_invalid_tmux_names(name):
    with pytest.raises(ValueError):
        remote._session_name("devbox", "/srv/one", name, "claude")


def test_saved_workspace_and_overrides(tmp_path, monkeypatch, capsys):
    config = tmp_path / "workspaces.json"
    config.write_text(json.dumps({"project": {"host": "devbox", "workspace": "/srv/one",
        "agent": "claude", "session": "feature", "tmux": False}}), encoding="utf-8")
    assert remote.main(["list", "--config", str(config)]) == 0
    assert json.loads(capsys.readouterr().out)["project"]["agent"] == "claude"
    called = []
    monkeypatch.setattr(remote, "_run", lambda argv, **kw: called.append(argv) or 0)
    assert remote.main(["start", "project", "--config", str(config), "--workspace", "/srv/two"]) == 0
    assert "cd -- /srv/two" in called[0][-1]
    assert "exec claude" in called[0][-1]
    assert "tmux" not in called[0][-1]


@pytest.mark.parametrize("contents", ['[]', '{', '{"x":{"host":"devbox"}}',
    '{"x":{"host":"devbox","workspace":"/srv","agent":"shell"}}',
    '{"x":{"host":"devbox","workspace":"/srv","tmux":"false"}}',
    '{"x":{"host":"devbox","workspace":"/srv","token":"secret"}}'])
def test_bad_config_fails_closed(tmp_path, contents, capsys):
    config = tmp_path / "config.json"
    config.write_text(contents, encoding="utf-8")
    assert remote.main(["list", "--config", str(config)]) == 2
    assert capsys.readouterr().out == ""


def test_ssh_config_and_protocol_options(tmp_path):
    argv = remote._ssh_argv("devbox", "exec hermes acp", batch=True, ssh_config=tmp_path / "ssh config")
    assert argv[argv.index("-F") + 1] == str((tmp_path / "ssh config").resolve())
    for option in ("StrictHostKeyChecking=yes", "BatchMode=yes", "ForwardAgent=no", "ClearAllForwardings=yes"):
        assert option in argv
    assert "-T" in argv and "-tt" not in argv


def test_run_preserves_exit_code_and_bytes(capfdbinary):
    rc = remote._run([sys.executable, "-c", "import os,sys; os.write(1, b'hello\\x00\\r\\n'); os.write(2,b'error'); sys.exit(23)"])
    assert rc == 23
    out, err = capfdbinary.readouterr()
    assert out == b"hello\x00\r\n"
    assert err == b"error"


def test_missing_executable_only_writes_stderr(capsys, tmp_path):
    assert remote._run([str(tmp_path / "does-not-exist")]) == 127
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Executable not found" in captured.err


def test_interrupt_reaps_child(monkeypatch):
    events = []
    class Child:
        def wait(self, timeout=None):
            events.append(("wait", timeout))
            if timeout is None:
                raise KeyboardInterrupt
            return 0
        def poll(self):
            return None
        def terminate(self):
            events.append("terminate")
    monkeypatch.setattr(remote.subprocess, "Popen", lambda *a, **kw: Child())
    assert remote._run(["ssh"]) == 130
    assert "terminate" in events and ("wait", 5) in events


def test_windows_vscode_batch_wrapper_is_bypassed(tmp_path, monkeypatch):
    install = tmp_path / "VS Code"
    cli = install / "resources/app/out/cli.js"
    cli.parent.mkdir(parents=True)
    cli.touch()
    (install / "Code.exe").touch()
    monkeypatch.setattr(remote.shutil, "which", lambda name: str(install / "bin/code.cmd"))
    calls = []
    monkeypatch.setattr(remote, "_run", lambda argv, **kw: calls.append((argv, kw)) or 0)
    workspace = '/srv/repo & echo %PATH% " quoted'
    assert remote._open("devbox", workspace, False) == 0
    argv, kwargs = calls[0]
    assert argv[:2] == [str(install / "Code.exe"), str(cli)]
    assert argv[-1] == workspace + "/"
    assert kwargs["env"]["ELECTRON_RUN_AS_NODE"] == "1"


def test_real_main_remote_dispatch_does_not_import_agent(tmp_path):
    script = '''
import importlib.abc, runpy, sys
class BlockRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'pm', 'agent', 'run_agent', 'yaml', 'torch', 'openai', 'anthropic'} or fullname == 'hermes_cli.config':
            raise AssertionError('Local runtime imported: ' + fullname)
sys.meta_path.insert(0, BlockRuntime())
sys.argv = ['hermes', 'remote', 'config', 'devbox', '--workspace', '/srv/repo', '--proxy']
runpy.run_module('hermes_cli.main', run_name='__main__')
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["acp.agents"]["Hermes Remote"]["command"] == "ssh"
    assert result.stderr == ""


def test_standalone_script_works_from_other_directory(tmp_path):
    script = Path(remote.__file__).resolve()
    result = subprocess.run([sys.executable, str(script), "config", "devbox", "--workspace", "/srv/repo"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["acp.agents"]["Hermes Remote"]["args"] == ["acp"]
