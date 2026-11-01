"""Configuration for shared-metrics transmission."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

#: No built-in ingest endpoint: packages go only to a collector the owner names in
#: ``telemetry.shared_metrics.endpoint``. Config only — deliberately NOT an environment variable:
#: AGENTS.md reserves HERMES_* for secrets, and an inherited variable could silently redirect
#: consented metrics to any host with nothing visible in config.
DEFAULT_ENDPOINT = ""

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

# Module-level latches: both mismatches are static misconfigurations,
# so each is reported once per process instead of on every hook fire.
_warned_send_without_collection = False
_warned_send_without_endpoint = False


@dataclass(frozen=True)
class SendConfig:
    """Resolved transmission settings."""

    #: Collection is on. Nothing is packaged or sent without it.
    enabled: bool
    #: Transmission is on AND permitted (that is, collection is also on).
    send: bool
    #: Where packages are POSTed; empty when the owner configured no collector.
    endpoint: str


def _endpoint_is_safe(endpoint: str) -> bool:
    """Reject plaintext destinations unless they are loopback (so tests can use local HTTP)."""
    try:
        parsed = urlparse(endpoint)
    except ValueError:
        return False
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and (parsed.hostname or "") in _LOCAL_HOSTS


def resolve_send_config(config: dict | None) -> SendConfig:
    """Resolve transmission settings from config. Sending needs an explicit endpoint.

    ``send`` is False whenever transmission cannot legitimately happen, so callers never
    have to re-check the combination.
    """
    global _warned_send_without_collection, _warned_send_without_endpoint

    raw = config if isinstance(config, dict) else {}
    telemetry = raw.get("telemetry")
    telemetry = telemetry if isinstance(telemetry, dict) else {}
    shared = telemetry.get("shared_metrics")
    shared = shared if isinstance(shared, dict) else {}

    enabled = shared.get("enabled") is True
    send_requested = shared.get("send") is True

    if send_requested and not enabled:
        # Loud, not silent: the user believes telemetry is being sent, and it never will be.
        if not _warned_send_without_collection:
            _warned_send_without_collection = True
            logger.error(
                "telemetry.shared_metrics.send is true but "
                "telemetry.shared_metrics.enabled is false — nothing is "
                "collected, so nothing can be sent. Enable collection or "
                "turn sending off."
            )
        return SendConfig(enabled=False, send=False, endpoint=DEFAULT_ENDPOINT)

    endpoint = shared.get("endpoint")
    endpoint = endpoint.strip() if isinstance(endpoint, str) else DEFAULT_ENDPOINT

    if send_requested and not endpoint:
        if not _warned_send_without_endpoint:
            _warned_send_without_endpoint = True
            logger.error(
                "telemetry.shared_metrics.send is true but no "
                "telemetry.shared_metrics.endpoint is set — there is no default "
                "collector, so nothing is sent. Set your own endpoint or turn "
                "sending off."
            )
        return SendConfig(enabled=enabled, send=False, endpoint=endpoint)

    if send_requested and not _endpoint_is_safe(endpoint):
        logger.error(
            "Refusing to send shared metrics to %r: telemetry must use https "
            "(or a localhost http endpoint for testing).",
            endpoint,
        )
        return SendConfig(enabled=enabled, send=False, endpoint=endpoint)

    return SendConfig(enabled=enabled, send=send_requested, endpoint=endpoint)
