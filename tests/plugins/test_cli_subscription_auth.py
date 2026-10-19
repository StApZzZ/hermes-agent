"""`hermes auth <action> <engine>-cli` runs the CLI's own account command; Hermes holds no tokens."""

import argparse
import json
import os
from pathlib import Path
import sys

import pytest


@pytest.mark.platforms("posix")
def test_auth_status_runs_the_native_cli_without_api_keys(tmp_path, monkeypatch):
    from hermes_cli.auth_plugin_providers import dispatch_plugin_auth

    capture = tmp_path / "capture.json"
    fake = tmp_path / "bin" / "codex"
    fake.parent.mkdir()
    fake.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                    f"open({str(capture)!r}, 'w').write(json.dumps({{'argv': sys.argv[1:], "
                    "'key': os.environ.get('OPENAI_API_KEY')}))\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake.parent) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-cli")

    assert dispatch_plugin_auth("status", argparse.Namespace(), "codex-cli") is True
    assert json.loads(capture.read_text()) == {"argv": ["login", "status"], "key": None}


def test_gemini_status_refuses_an_api_key_selection(tmp_path, monkeypatch, capsys):
    from hermes_cli.auth_plugin_providers import dispatch_plugin_auth

    settings = Path(tmp_path) / ".gemini" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"security": {"auth": {"selectedType": "gemini-api-key"}}}))
    monkeypatch.setattr("tools.environments.local.hermes_subprocess_env", lambda: {"HOME": str(tmp_path)})

    with pytest.raises(SystemExit) as exited:
        dispatch_plugin_auth("status", argparse.Namespace(), "gemini-cli")
    assert exited.value.code == 1
    assert "Sign in with Google" in capsys.readouterr().out
