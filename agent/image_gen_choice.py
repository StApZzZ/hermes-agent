"""Which image generation backends a user can pick right now, for the chat and dashboard switches.

``image_gen.provider`` selects the backend ``image_generate`` uses; this lists the registered providers
that are available on this host (CLI installed, key present, login stored) so a picker never offers
one that would only fail. The tool set is built when a conversation starts, so a new choice applies
from the next conversation and never changes a running one's tools or prompt cache.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging

from agent.provider_registry import configured_provider_name, is_available_safe

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImageGenChoice:
    name: str
    label: str


def available_choices() -> list[ImageGenChoice]:
    """Registered image generation providers whose ``is_available()`` holds, sorted by name."""
    from agent.image_gen_registry import list_providers
    from hermes_cli.plugins import _ensure_plugins_discovered

    _ensure_plugins_discovered()
    return [ImageGenChoice(p.name, p.display_name) for p in list_providers()
            if is_available_safe(p, logger, "image_gen provider %s.is_available() raised %s")]


def selected_provider() -> str | None:
    """``image_gen.provider`` from config.yaml (``None`` when unset)."""
    return configured_provider_name("image_gen", logger)
