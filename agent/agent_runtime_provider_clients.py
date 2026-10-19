"""Provider-owned client construction, including external-process boundaries."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def provider_supplied_client(agent, client_kwargs: dict) -> Any | None:
    """Ask the registered ProviderProfile for a custom client, if any. Resolves by provider name,
    then by ``base_url`` prefix so a URL-only runtime (``acp://…``) still reaches its profile.
    HTTP profiles may fall back to the standard client. External-process profiles must
    supply their transport: constructing an HTTP client would cross their auth boundary."""
    from providers import get_provider_profile

    provider_name = (getattr(agent, "provider", "") or "").strip()
    profile = get_provider_profile(provider_name) if provider_name else None
    if profile is None:
        base_url = str(client_kwargs.get("base_url", "") or "").strip()
        if base_url:
            profile = _profile_for_base_url(base_url)
    if profile is None:
        return None
    try:
        client = profile.create_client(**client_kwargs)
        if client is None and profile.auth_type == "external_process":
            raise RuntimeError(f"External-process provider {profile.name!r} did not supply a client")
        return client
    except Exception:
        if profile.auth_type == "external_process":
            raise
        logger.warning(
            "Provider profile %r failed to create a client; falling back to the standard client path",
            getattr(profile, "name", provider_name) or "?", exc_info=True,
        )
        return None


def _profile_for_base_url(base_url: str) -> Any | None:
    """Registered profile whose own base_url is a prefix of ``base_url`` (provider name did not
    resolve). Prefix, not equality: the replaced copilot-acp branch keyed on
    ``startswith("acp://copilot")``, so a path or user override under the same root must resolve."""
    try:
        from providers import list_providers
        candidates = list_providers()
    except Exception:
        return None
    target = base_url.rstrip("/").lower()
    for candidate in candidates or []:
        own = str(getattr(candidate, "base_url", "") or "").rstrip("/").lower()
        if own and (target == own or target.startswith(own + "/")):
            return candidate
    return None


