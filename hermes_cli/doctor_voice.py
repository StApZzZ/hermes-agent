"""Doctor checks for the two failures a messaging gateway suffers in silence.

Both are invisible to every other check: the gateway stays "connected", the service stays
"active", and ``hermes doctor`` used to end on "All checks passed" while

* every voice message reached the agent as an untranscribed placeholder (no STT backend), and
* a second poller held the same bot token, so Telegram dropped queued messages on each recovery.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from hermes_cli.doctor_report import Finding, _section, check_info, check_ok, check_warn, doctor_check


@doctor_check(on_error="Speech-to-Text check failed", detail="({e})")
def _check_speech_to_text(should_fix: bool, f: Finding) -> None:
    """Resolve STT exactly as the voice path does, and say so when nothing can serve.

    The transcription failure note the agent receives deliberately never mentions STT setup
    (it would persist in history and make the model volunteer setup advice), so the operator
    has no other place to learn that voice input is dead.
    """
    _section("Speech-to-Text (voice messages)")
    from hermes_cli.config import load_config
    from tools.transcription_tools import active_stt_provider

    stt = load_config().get("stt") or {}
    if not isinstance(stt, dict):
        stt = {}
    if not stt.get("enabled", True):
        check_ok("Speech-to-text is off (stt.enabled: false)", "voice messages stay untranscribed by choice")
        return

    provider = active_stt_provider()
    if provider != "none":
        check_ok(f"Voice transcription ready (provider: {provider})")
        return

    configured = str(stt.get("provider") or "").strip()
    check_warn("No speech-to-text provider available",
               f"(stt.provider: {configured})" if configured else "(nothing configured or installed)")
    check_info("Voice messages reach the agent as an untranscribed placeholder — it answers without hearing them.")
    if _messaging_platforms():
        check_info(f"A messaging platform is connected ({', '.join(_messaging_platforms())}), so voice messages can arrive.")
    f.manual_issues.append(
        "Set up voice transcription: 'hermes setup tools' → Speech-to-Text. Local Whisper needs no API "
        "key ('hermes pm install --extra stt-whisper'); GROQ_API_KEY enables free Groq Whisper.")


def _messaging_platforms() -> list[str]:
    """Configured messaging platforms (the CLI is not one): these deliver voice messages."""
    from hermes_cli.tools_config import _get_enabled_platforms
    return [p for p in _get_enabled_platforms() if p != "cli"]


# One polling restart is routine (a killed gateway holds its getUpdates session ~30s). A conflict
# that keeps coming back means a SECOND live poller owns the same token: the two sessions evict each
# other, and every recovery drops Telegram's queued updates, so messages are lost outright.
_CONFLICT_MARKER = "polling conflict"
_CONFLICT_WINDOW = timedelta(hours=1)
_CONFLICT_THRESHOLD = 3
_LOG_TAIL_BYTES = 256_000
_LOG_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


@doctor_check(on_error="Messaging polling check failed", detail="({e})")
def _check_messaging_polling(should_fix: bool, f: Finding) -> None:
    """Report a bot token that two pollers are fighting over."""
    from hermes_cli.config import get_hermes_home

    log_path = Path(get_hermes_home()) / "logs" / "gateway.log"
    conflicts = count_recent_polling_conflicts(log_path)
    if conflicts < _CONFLICT_THRESHOLD:
        return
    _section("Messaging Polling")
    check_warn(f"{conflicts} polling conflicts in the last hour",
               "(another process is polling the same bot token)")
    check_info("Each recovery drops Telegram's queued updates, so messages sent in between are lost.")
    f.manual_issues.append(
        "Two gateways share one bot token. Leave exactly one running (one bot token, one poller): "
        "stop the other instance, or remove its token from that machine's .env.")


def count_recent_polling_conflicts(log_path: Path, *, now: datetime | None = None) -> int:
    """Polling conflicts logged in the last hour, from the tail of *log_path* (0 when unreadable)."""
    try:
        with open(log_path, "rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - _LOG_TAIL_BYTES))
            tail = handle.read().decode("utf-8", "replace")
    except OSError:
        return 0
    cutoff = (now or datetime.now()) - _CONFLICT_WINDOW
    hits = 0
    for line in tail.splitlines():
        if _CONFLICT_MARKER not in line:
            continue
        stamp = _LOG_TIMESTAMP.match(line)
        if stamp and datetime.strptime(stamp.group(1), "%Y-%m-%d %H:%M:%S") >= cutoff:
            hits += 1
    return hits
