"""Which served profile runs an inbound event on a multiplexed gateway.

Two sources, most specific first: the sender's ``/space`` selection for this chat lane (one sender
in one chat through one bot, stored by ``hermes_cli.spaces``) and the static
``gateway.profile_routes`` table (``gateway/profile_routing.py``).
``session_identity.resolve_identity`` asks once per event and pins the answer; session keys are
namespaced by the runtime profile, so every space keeps its own conversation in the same chat.
"""
from __future__ import annotations

import logging
from typing import Optional

from gateway.session import SessionSource

logger = logging.getLogger(__name__)


def space_lane_key(runner, source: SessionSource, bot_profile: Optional[str]) -> str:
    """The ``hermes_cli.spaces`` lane key of *source* received by *bot_profile*'s bot (``None`` = the
    primary bot, named explicitly so the key matches ``identity.transport_profile``)."""
    from hermes_cli.spaces import lane_key
    bot = (bot_profile or "").strip() or (getattr(runner, "_primary_profile_name", None) or "").strip() or "default"
    return lane_key(source.platform.value, bot, source.chat_id, getattr(source, "user_id", None))


def served_profile_names(config) -> set[str]:
    from gateway.run import _multiplex_profile_homes
    return {name for name, _home in _multiplex_profile_homes(config)}


def _space_selection_for_source(runner, source: SessionSource, adapter_profile: Optional[str]) -> Optional[str]:
    """The profile this lane's ``/space`` selection names, when this gateway serves it. A stale
    selection (agent deleted or no longer served) is ignored, never a reason to drop the event."""
    from hermes_cli.spaces import selected_profile
    profile = selected_profile(space_lane_key(runner, source, adapter_profile))
    if not profile:
        return None
    try:
        served = served_profile_names(runner.config)
    except Exception:  # health: allow BLE001 -- fall back to static routing, logged with traceback
        logger.warning("Ignoring /space selection: the served-profile set could not be resolved", exc_info=True)
        return None
    if profile not in served:
        logger.warning("Ignoring /space selection of %s/%s: profile %r is not served",
                       source.platform.value, source.chat_id, profile)
        return None
    return profile


class GatewayProfileRoutingMixin:

    def _profile_name_for_source(
        self, source: SessionSource, adapter_profile: Optional[str] = None,
    ) -> Optional[str]:
        """Resolve the profile name for an inbound source: the lane's ``/space`` selection, then the
        configured routes (most specific wins). ``None`` = default/active profile (or, for a secondary
        adapter, its own profile — the caller stamps it). Gated on ``multiplex_profiles``, since the
        scoped run only activates under multiplexing; otherwise keys would be profile-namespaced while
        the agent ran in ``agent:main``. ``adapter_profile`` is the profile owning the receiving bot;
        only routes declaring it as ``bot_profile`` apply (#104933)."""
        config = getattr(self, "config", None)
        if not getattr(config, "multiplex_profiles", False):
            return None
        if adapter_profile is None:
            # Sources built outside ``build_source`` may still carry the receiving adapter as provenance.
            owner = self._transport_owner(source) if callable(getattr(source, "_transport_adapter_ref", None)) else None
            if isinstance(owner, tuple):
                adapter_profile = owner[1]
        selected = _space_selection_for_source(self, source, adapter_profile)
        if selected:
            return selected
        routes = getattr(config, "profile_routes", None)
        if not routes:
            return None
        from gateway.profile_routing import ProfileRouteRejected, match_profile_route
        try:
            matched = match_profile_route(
                routes, platform=source.platform.value, guild_id=getattr(source, "guild_id", None),
                chat_id=source.chat_id, thread_id=getattr(source, "thread_id", None),
                parent_chat_id=getattr(source, "parent_chat_id", None),
                adapter_profile=adapter_profile, user_id=getattr(source, "user_id", None))
        except Exception as exc:
            logger.warning(
                "Rejecting %s/%s: profile route matching failed",
                source.platform, source.chat_id, exc_info=True)
            raise ProfileRouteRejected("matcher") from exc
        if matched:
            try:
                served = served_profile_names(config)
            except Exception as exc:
                logger.warning(
                    "Rejecting profile route %r because the served-profile set could not be resolved",
                    matched.name, exc_info=True)
                raise ProfileRouteRejected(matched.name) from exc
            if matched.profile not in served:
                logger.warning(
                    "Rejecting profile route %r: target profile %r is not served",
                    matched.name, matched.profile)
                raise ProfileRouteRejected(matched.name, matched.profile)
            return matched.profile
        logger.debug(
            "No profile route matched: platform=%s chat_id=%s thread_id=%s parent_chat_id=%s",
            source.platform.value, source.chat_id,
            getattr(source, "thread_id", None), getattr(source, "parent_chat_id", None))
        return None
