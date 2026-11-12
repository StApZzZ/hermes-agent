"""Doctor's voice-input and polling-health checks (hermes_cli/doctor_voice.py).

Both cover failures that leave every other signal green: the gateway is "connected", the service is
"active", and doctor used to finish on "All checks passed" while voice input was dead or a second
poller was eating the user's messages. The contract here is that doctor says so, and that it names
the fix.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

from hermes_cli.doctor_voice import (
    _check_messaging_polling,
    _check_speech_to_text,
    count_recent_polling_conflicts,
)

_CONFLICT = (
    "WARNING hermes_plugins.platforms__telegram.adapter: [Telegram] Telegram polling conflict (1/5) "
    "— previous session still held open on Telegram's servers."
)


def _log(home, lines):
    """Write *lines* where doctor looks for them: ``<hermes home>/logs/gateway.log``."""
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "gateway.log").write_text("\n".join(lines) + "\n")
    return logs / "gateway.log"


def _stamped(when, text):
    return f"{when:%Y-%m-%d %H:%M:%S},123 {text}"


def test_speech_to_text_ready_records_no_issue(capsys):
    with (
        patch("hermes_cli.config.load_config", return_value={"stt": {"enabled": True}}),
        patch("tools.transcription_tools.active_stt_provider", return_value="local"),
    ):
        finding = _check_speech_to_text(False)
    assert finding.manual_issues == []
    assert "Voice transcription ready (provider: local)" in capsys.readouterr().out


def test_speech_to_text_without_provider_warns_and_names_the_fix(capsys):
    with (
        patch("hermes_cli.config.load_config", return_value={"stt": {"enabled": True, "provider": "local"}}),
        patch("tools.transcription_tools.active_stt_provider", return_value="none"),
        patch("hermes_cli.tools_config._get_enabled_platforms", return_value=["cli", "telegram"]),
    ):
        finding = _check_speech_to_text(False)
    out = capsys.readouterr().out
    assert "No speech-to-text provider available" in out
    # The operator learns what the silence costs, and that voice can actually arrive here.
    assert "untranscribed placeholder" in out
    assert "telegram" in out
    assert any("hermes setup tools" in issue for issue in finding.manual_issues)


def test_speech_to_text_disabled_is_not_an_issue(capsys):
    with patch("hermes_cli.config.load_config", return_value={"stt": {"enabled": False}}):
        finding = _check_speech_to_text(False)
    assert finding.manual_issues == []
    assert "Speech-to-text is off" in capsys.readouterr().out


def test_recurring_conflicts_are_reported_as_a_second_poller(tmp_path, capsys):
    now = datetime.now()
    _log(tmp_path, [_stamped(now - timedelta(minutes=i), _CONFLICT) for i in (1, 2, 3, 4)])
    with patch("hermes_cli.config.get_hermes_home", return_value=tmp_path):
        finding = _check_messaging_polling(False)
    out = capsys.readouterr().out
    assert "polling conflicts in the last hour" in out
    assert "same bot token" in out
    assert any("one bot token, one poller" in issue for issue in finding.manual_issues)


def test_quiet_gateway_prints_nothing(tmp_path, capsys):
    _log(tmp_path, [_stamped(datetime.now(), "INFO gateway.run: response ready")])
    with patch("hermes_cli.config.get_hermes_home", return_value=tmp_path):
        finding = _check_messaging_polling(False)
    assert finding.manual_issues == []
    assert capsys.readouterr().out == ""


def test_conflict_count_ignores_old_lines_and_missing_files(tmp_path):
    now = datetime.now()
    log = _log(tmp_path, [
        _stamped(now - timedelta(hours=5), _CONFLICT),   # yesterday's war, already settled
        _stamped(now - timedelta(minutes=2), _CONFLICT),
        _stamped(now, "INFO gateway.run: inbound message"),
    ])
    assert count_recent_polling_conflicts(log, now=now) == 1
    assert count_recent_polling_conflicts(tmp_path / "absent.log", now=now) == 0
