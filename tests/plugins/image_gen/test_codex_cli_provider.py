"""The ``codex-cli`` image_gen plugin against a fake ``codex`` executable that speaks ``codex exec --json``.

The fake records its argv, stdin and environment, then saves a PNG where the real CLI does
(``$CODEX_HOME/generated_images/<thread_id>/``) and prints the JSON event stream.
"""

from __future__ import annotations

import base64
import importlib
import json
import os
from pathlib import Path
import sys

import pytest

codex_plugin = importlib.import_module("plugins.image_gen.codex-cli")
pytestmark = pytest.mark.platforms("posix")  # the fake CLI is a shebang script

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c6300010000000500010d0a2db40000000049454e44ae426082")

_FAKE_CODEX = '''#!{python}
import json, os, sys
from pathlib import Path
mode = os.environ["FAKE_CODEX_MODE"]
record = Path(os.environ["FAKE_CODEX_RECORD"])
record.write_text(json.dumps({{"argv": sys.argv[1:], "stdin": sys.stdin.read(),
    "env": {{k: os.environ.get(k) for k in ("OPENAI_API_KEY", "CODEX_API_KEY", "NO_BROWSER")}},
    "images": [Path(a.split("=", 1)[1]).read_bytes().hex() for a in sys.argv if a.startswith("--image=")]}}))
print(json.dumps({{"type": "thread.started", "thread_id": "thread-1"}}))
if mode == "limit":
    print(json.dumps({{"type": "turn.failed", "error": {{"message": "You've hit your usage limit."}}}}))
    sys.exit(1)
if mode == "draw":
    folder = Path(os.environ["CODEX_HOME"]) / "generated_images" / "thread-1"
    folder.mkdir(parents=True)
    (folder / "exec-1.png").write_bytes(bytes.fromhex("{png}"))
print(json.dumps({{"type": "item.completed", "item": {{"type": "agent_message", "text": "DONE"}}}}))
print(json.dumps({{"type": "turn.completed"}}))
'''


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    codex_home = tmp_path / "codex-home"
    home.mkdir()
    codex_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    exe = tmp_path / "codex"
    exe.write_text(_FAKE_CODEX.format(python=sys.executable, png=_PNG.hex()), encoding="utf-8")
    exe.chmod(0o755)
    record = tmp_path / "record.json"
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "CODEX_HOME": str(codex_home),
           "FAKE_CODEX_RECORD": str(record), "OPENAI_API_KEY": "must-not-reach-codex",
           "CODEX_API_KEY": "must-not-reach-codex"}
    monkeypatch.setattr("tools.environments.local.hermes_subprocess_env", lambda: dict(env))
    monkeypatch.setattr(codex_plugin, "_codex_command", lambda: str(exe))

    def run(mode, **kwargs):
        env["FAKE_CODEX_MODE"] = mode
        result = codex_plugin.CodexCLIImageGenProvider().generate(**kwargs)
        return result, (json.loads(record.read_text()) if record.exists() else None)

    return run, home, codex_home


def test_draws_through_codex_and_keeps_only_the_hermes_copy(fake_codex):
    run, home, codex_home = fake_codex
    result, seen = run("draw", prompt="a blue paper boat", aspect_ratio="portrait")

    assert result["success"] is True and result["provider"] == "codex-cli"
    image = Path(result["image"])
    assert image.read_bytes() == _PNG and image.is_relative_to(home)
    assert not (codex_home / "generated_images" / "thread-1").exists()
    # The request travels on stdin with the aspect; the sandbox flags pin the process to drawing.
    assert "a blue paper boat" in seen["stdin"] and "portrait" in seen["stdin"]
    assert not any("paper boat" in arg for arg in seen["argv"])
    for flag in ("read-only", "features.shell_tool=false", 'web_search="disabled"', 'forced_login_method="chatgpt"'):
        assert flag in seen["argv"]
    assert seen["env"]["OPENAI_API_KEY"] is None and seen["env"]["CODEX_API_KEY"] is None


def test_edit_passes_the_source_image_as_a_file(fake_codex):
    run, _home, _codex_home = fake_codex
    data_url = "data:image/png;base64," + base64.b64encode(_PNG).decode()
    result, seen = run("draw", prompt="make the boat red", image_url=data_url)

    assert result["success"] is True and result["modality"] == "image"
    assert seen["images"] == [_PNG.hex()]
    assert "attached image" in seen["stdin"]


def test_subscription_limit_reaches_the_user(fake_codex):
    run, _home, _codex_home = fake_codex
    result, _seen = run("limit", prompt="a cat")
    assert result["success"] is False and result["error_type"] == "api_error"
    assert "usage limit" in result["error"]


def test_no_image_is_an_error_not_a_silent_success(fake_codex):
    run, _home, _codex_home = fake_codex
    result, _seen = run("text-only", prompt="a cat")
    assert result["success"] is False and result["error_type"] == "empty_response"


def test_missing_cli_and_empty_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(codex_plugin, "_codex_command", lambda: None)
    provider = codex_plugin.CodexCLIImageGenProvider()
    assert provider.is_available() is False
    assert provider.generate(prompt="a cat")["error_type"] == "missing_dependency"
    assert provider.generate(prompt="  ")["error_type"] == "invalid_argument"
