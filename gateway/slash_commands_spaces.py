"""``/space`` in a chat: list spaces, or run this sender's lane under another space's agent.
Bound onto ``GatewayRunner`` through ``GatewaySlashCommandsMixin``; the lane selection is read back
by ``run_profile_routing.py`` on the next inbound event."""

from __future__ import annotations

from agent.i18n import t
from gateway.platforms.event import MessageEvent

_LEAVE_WORDS = frozenset({"off", "reset", "default", "none"})


class GatewaySpaceCommandsMixin:

    async def _handle_space_command(self, event: MessageEvent) -> str:
        """``/space`` lists spaces; ``/space <name> [agent]`` switches; ``/space off`` leaves."""
        from gateway.session_identity import identity_of
        from hermes_cli.slash_exec import CommandContext, execute_command
        source = event.source
        identity = identity_of(source)
        multiplexed = bool(getattr(getattr(self, "config", None), "multiplex_profiles", False)
                           and identity is not None and identity.multiplexed)
        args = event.get_command_args().split()
        if not args:
            current = identity.runtime_profile if identity is not None else None
            return execute_command("space", CommandContext(surface="gateway", options={"current_profile": current})).text
        if not multiplexed:
            return t("gateway.space.needs_multiplex")
        from gateway.run_profile_routing import space_lane_key
        key = space_lane_key(self, source, identity.transport_profile)
        if args[0].lower() in _LEAVE_WORDS:
            from hermes_cli.spaces import clear_lane
            clear_lane(key)
            return t("gateway.space.cleared")
        return self._switch_space_lane(key, identity.transport_profile, args[0], args[1] if len(args) > 1 else None)

    def _switch_space_lane(self, key: str, transport_profile: str, name: str, agent) -> str:
        from hermes_cli import spaces
        try:
            space = spaces.get_space(name)
        except (KeyError, ValueError):
            return t("gateway.space.unknown", name=name)
        agent = agent or space.primary
        if not agent:
            return t("gateway.space.no_agent", space=space.name)
        if agent not in space.agents:
            return t("gateway.space.not_member", agent=agent, space=space.name)
        from gateway.run_profile_routing import served_profile_names
        if agent not in served_profile_names(self.config):
            return t("gateway.space.not_served", agent=agent)
        if agent == transport_profile:
            spaces.clear_lane(key)  # the receiving bot's own profile: no re-homing needed
        else:
            spaces.select_lane(key, space.name, agent)
        return t("gateway.space.switched", space=space.label, agent=agent)
