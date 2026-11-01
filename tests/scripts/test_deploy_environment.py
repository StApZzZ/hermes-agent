"""hermes-deploy keeps the user's files intact while it points them at the deployed environment."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "scripts" / "deploy"


def _env(tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HERMES_", "XDG_"))}
    env.update(HOME=str(tmp_path / "home"), XDG_CACHE_HOME=str(tmp_path / "cache"))
    (tmp_path / "home").mkdir(exist_ok=True)
    return env


def _stub(tmp_path: Path, env: dict[str, str], name: str, body: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / name
    stub.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    stub.chmod(0o755)
    if not env["PATH"].startswith(f"{bin_dir}{os.pathsep}"):
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"


@pytest.mark.platforms("posix")
def test_vscode_settings_merge_keeps_jsonc_user_settings(tmp_path):
    settings = tmp_path / "settings.json"
    original = ('{\n  // mine\n  "editor.fontSize": 14, /* block */\n  "url": "http://a//b",\n'
                '  "remote.SSH.defaultExtensions": ["Anthropic.Claude-Code",],\n}\n')
    settings.write_text(original, encoding="utf-8")
    patch = json.dumps({"remote.SSH.defaultExtensions": ["anthropic.claude-code", "openai.chatgpt"],
                        "remote.SSH.remotePlatform": {"box": "linux"}})
    for _ in range(2):
        subprocess.run(["python3", str(DEPLOY / "vscode_settings.py"), str(settings), "-"], input=patch, text=True, encoding="utf-8", errors="replace",
                       env=_env(tmp_path), capture_output=True, check=True, timeout=30)

    merged = json.loads(settings.read_text(encoding="utf-8-sig"))
    assert merged == {"editor.fontSize": 14, "url": "http://a//b",
                      "remote.SSH.defaultExtensions": ["Anthropic.Claude-Code", "openai.chatgpt"],
                      "remote.SSH.remotePlatform": {"box": "linux"}}
    backups = list((tmp_path / "cache" / "hermes-deploy").iterdir())
    assert [b.read_text(encoding="utf-8-sig") for b in backups] == [original]


def _add(tar: tarfile.TarFile, name: str, data: bytes = b"x") -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


@pytest.mark.platforms("posix")
def test_restore_takes_only_hermes_and_cli_state(tmp_path):
    archive = tmp_path / "backup.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        _add(tar, ".hermes/config.yaml", b"terminal:\n  cwd: /home/someone-else/work\n")
        _add(tar, "./.claude/settings.json", b"{}")
        _add(tar, ".gitconfig", b"[user]\n")
        _add(tar, "repos/project/README.md")
        _add(tar, ".hermes/../../escaped")
        _add(tar, ".bashrc")
    env = _env(tmp_path)
    calls = tmp_path / "hermes-calls"
    # Never the real hermes: `gateway stop` would reach the developer's own gateway service.
    _stub(tmp_path, env, "hermes", f'echo "$* $(ls ~/.claude 2>/dev/null)" >> {calls}')
    home = Path(env["HOME"])
    (home / ".hermes").mkdir()
    (home / ".hermes" / "config.yaml").write_text("old: true\n", encoding="utf-8")

    result = subprocess.run(["bash", str(DEPLOY / "bootstrap.sh"), "restore", str(archive)], env=env,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False)

    assert result.returncode == 0, result.stderr
    restored = sorted(str(p.relative_to(home)) for p in home.rglob("*") if p.is_file())
    kept = [p for p in restored if p.startswith(".hermes/backups/")]
    assert [p for p in restored if p not in kept] == [".claude/settings.json", ".gitconfig", ".hermes/config.yaml"]
    assert [Path(p).name for p in kept] == ["config.yaml"]
    assert (home / kept[0]).read_text(encoding="utf-8-sig") == "old: true\n"
    assert not (tmp_path / "escaped").exists()
    assert "/home/someone-else/work" in result.stderr
    assert calls.read_text(encoding="utf-8-sig").split("\n")[0].strip() == "gateway stop"   # before anything is extracted


@pytest.mark.platforms("posix")
def test_ssh_host_block_is_written_once_above_existing_config(tmp_path):
    env = _env(tmp_path)
    _stub(tmp_path, env, "curl", "exit 7")   # the unreachable-host hint asks for the egress IP: no network in tests
    key = tmp_path / "key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True, timeout=30)
    config = tmp_path / "ssh_config"
    config.write_text("Host *\n  ServerAliveCountMax 3\n", encoding="utf-8")
    with socket.socket() as probe:   # a port just freed refuses at once; a fixed low port may be dropped (timeout)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    argv = ["bash", str(DEPLOY / "hermes-deploy.sh"), "--host", "127.0.0.1", "--port", str(port), "--name", "box",
            "--key", str(key), "--ssh-config", str(config), "--ide", "none"]

    for _ in range(2):
        result = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=60, check=False)
        assert result.returncode == 5, result.stderr   # nothing listens there

    text = config.read_text(encoding="utf-8-sig")
    assert text.count("Host box\n") == 1
    assert text.startswith("# >>> hermes-deploy box\nHost box\n  HostName 127.0.0.1\n")
    assert text.endswith("Match all\n# <<< hermes-deploy box\n\nHost *\n  ServerAliveCountMax 3\n")
