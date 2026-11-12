"""The reply a brand-new chat gets: what this agent can actually do here.

Messaging clients send ``/start`` the first time a user opens the bot, so it is the one moment every
user passes through. It used to be swallowed with an empty reply — a new owner's first contact with
their own agent was silence, and nothing ever said that the thing on the other end runs a terminal,
drives a browser, remembers across sessions and can be put on a schedule.

The lines are built from the toolsets actually enabled for that platform, so the intro never
promises a capability the toolset gate would then refuse.
"""

from __future__ import annotations

# Toolset key -> i18n key for one plain-language line. Order is the order a new user should read
# them in (what it DOES before what it knows), not registry order.
_CAPABILITY_LINES: tuple[tuple[str, str], ...] = (
    ("terminal", "gateway.intro.cap_terminal"),
    ("file", "gateway.intro.cap_file"),
    ("web", "gateway.intro.cap_web"),
    ("browser", "gateway.intro.cap_browser"),
    ("vision", "gateway.intro.cap_vision"),
    ("memory", "gateway.intro.cap_memory"),
    ("cronjob", "gateway.intro.cap_cron"),
    ("skills", "gateway.intro.cap_skills"),
    ("delegation", "gateway.intro.cap_delegation"),
    ("image_gen", "gateway.intro.cap_image_gen"),
    ("session_search", "gateway.intro.cap_session_search"),
    ("todo", "gateway.intro.cap_todo"),
)

# A first message is an introduction, not a catalogue: the rest is one `/help` away.
_MAX_CAPABILITY_LINES = 8


def build_start_intro(platform: str) -> str:
    """The ``/start`` reply for *platform*: a short capability overview in plain language."""
    from agent.i18n import t

    enabled = _enabled_toolsets(platform)
    shown = [key for toolset, key in _CAPABILITY_LINES if toolset in enabled]
    parts = [t("gateway.intro.header"), "", t("gateway.intro.lede")]
    if shown:
        parts += ["", t("gateway.intro.can_header")]
        parts += [f"• {t(key)}" for key in shown[:_MAX_CAPABILITY_LINES]]
        if len(shown) > _MAX_CAPABILITY_LINES:
            parts.append(t("gateway.intro.cap_more"))
    parts += ["", _voice_line(), t("gateway.intro.help_hint")]
    return "\n".join(parts)


def _enabled_toolsets(platform: str) -> set[str]:
    """Toolsets enabled for *platform*, resolved the way the tools configurator resolves them."""
    from hermes_cli.config import load_config
    from hermes_cli.tools_config import _get_platform_tools

    return _get_platform_tools(load_config(), platform)


def _voice_line() -> str:
    """Tell the truth about voice: an owner whose STT never got set up otherwise only finds out by
    sending a voice message and getting an answer to something the agent never heard.

    STT is a config-only capability (never a platform toolset), so the provider resolver is the
    only honest source here.
    """
    from agent.i18n import t
    from tools.transcription_tools import active_stt_provider

    return t("gateway.intro.voice_on") if active_stt_provider() != "none" else t("gateway.intro.voice_off")
