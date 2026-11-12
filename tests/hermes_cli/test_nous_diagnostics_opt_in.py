"""The diagnostics upload to Nous is armed by the user, never by the default install.

The bundle carries logs, so it carries conversation content. This fork ships the path disarmed and
gates it at the first egress call, which is the one place every caller passes through: the CLI's
``hermes debug share --nous``, the gateway's ``diagnostics.share_nous`` RPC, and the desktop's
"Send diagnostics" button behind it.
"""

from unittest.mock import patch

import pytest

from hermes_cli.diagnostics_upload import nous_diagnostics_enabled, request_upload_url, share_to_nous


class _Response:
    status = 200

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        return self._body


def test_a_fresh_install_has_the_upload_disarmed(tmp_path, monkeypatch):
    """Against a real HERMES_HOME with no config of its own: the shipped default decides."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert nous_diagnostics_enabled() is False


def test_the_setting_arms_it(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / "config.yaml").write_text("telemetry:\n  nous_diagnostics: true\n")
    assert nous_diagnostics_enabled() is True


def test_disarmed_upload_never_reaches_the_network():
    """Not merely an error: the request must not leave the machine at all."""
    def _must_not_be_called(*_args, **_kwargs):
        raise AssertionError("the disarmed upload opened a connection")

    with (
        patch("hermes_cli.config.load_config", return_value={}),
        patch("urllib.request.urlopen", _must_not_be_called),
    ):
        with pytest.raises(RuntimeError) as exc:
            request_upload_url(size_bytes=10)
    # The refusal has to name the setting, because it is what the user reads in the desktop dialog.
    assert "telemetry.nous_diagnostics" in str(exc.value)


def test_share_to_nous_is_closed_by_the_same_gate():
    """share_to_nous is the documented entry; it must not have a way around the egress check."""
    with (
        patch("hermes_cli.config.load_config", return_value={"telemetry": {"nous_diagnostics": False}}),
        patch("urllib.request.urlopen", side_effect=AssertionError("uploaded while disarmed")),
    ):
        with pytest.raises(RuntimeError, match="telemetry.nous_diagnostics"):
            share_to_nous(b"bundle")


def test_armed_upload_proceeds():
    """The gate refuses, it does not remove the feature."""
    calls = []

    def _urlopen(req, timeout=None):
        calls.append(req.full_url)
        return _Response(b'{"uploadUrl": "https://upload.example/put", "viewUrl": "https://v/1", "id": "1"}')

    with (
        patch("hermes_cli.config.load_config", return_value={"telemetry": {"nous_diagnostics": True}}),
        patch("urllib.request.urlopen", _urlopen),
    ):
        info = request_upload_url(size_bytes=10)
    assert info["uploadUrl"] == "https://upload.example/put"
    assert calls and calls[0].endswith("/api/diagnostics/upload-url")
