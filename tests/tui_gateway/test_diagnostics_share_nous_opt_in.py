"""diagnostics.share_nous obeys the same opt-in as the CLI.

This RPC is what the desktop's "Send diagnostics" button calls. The button is always on screen, so
the backend is the thing that decides: with ``telemetry.nous_diagnostics`` off nothing leaves the
machine, and the structured error the handler returns becomes the dialog's message -- which is why
it has to name the setting rather than read as a broken upload.
"""

from __future__ import annotations

import pytest

from tui_gateway import server


def _handler():
    fn = server._methods.get("diagnostics.share_nous")
    assert fn is not None, "diagnostics.share_nous not registered"
    return fn


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A real HERMES_HOME with no config of its own: the shipped default decides."""
    root = tmp_path / ".hermes"
    (root / "logs").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr("hermes_cli.debug.collect_share_bundle", lambda **_kw: {"report": "x"})
    monkeypatch.setattr("hermes_cli.debug.build_nous_bundle", lambda _bundle, redact=True: b"gz")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: pytest.fail("the disarmed upload opened a connection"))
    return root


def test_disarmed_share_returns_a_refusal_the_dialog_can_show(home):
    payload = _handler()("rid-off", {})["result"]

    assert payload["ok"] is False
    assert "telemetry.nous_diagnostics" in payload["error"]
    assert "view_url" not in payload


def test_arming_it_restores_the_upload(home, monkeypatch):
    """The gate must not be a one-way door: the user who asks for it gets the feature."""
    (home / "config.yaml").write_text("telemetry:\n  nous_diagnostics: true\n")
    monkeypatch.setattr(
        "hermes_cli.diagnostics_upload.share_to_nous",
        lambda _blob: {"viewUrl": "https://nas.example/view/1", "id": "1"})

    payload = _handler()("rid-on", {})["result"]

    assert payload["ok"] is True
    assert payload["view_url"] == "https://nas.example/view/1"
