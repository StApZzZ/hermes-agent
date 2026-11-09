"""``/settings`` in a chat: one menu over the per-chat settings commands, plus image recognition.

On adapters with inline pickers (Telegram) each menu button opens the existing picker or reply of
that command (``/model``, ``/reasoning``, ...), so no setting gets a second implementation. The
image-recognition mode (``agent.image_input_mode``) had no chat command; it is read from
config.yaml on every inbound image, so changing it never touches a running agent or its prompt
cache. Bound onto ``GatewayRunner`` through ``GatewaySlashCommandsMixin``.
"""

from __future__ import annotations

import dataclasses
import logging

from agent.i18n import t
from gateway.platforms.event import MessageEvent

logger = logging.getLogger(__name__)

# Menu item → the slash command that owns it ("images" is owned here).
_MENU = (("model", "model"), ("reasoning", "reasoning"), ("fast", "fast"), ("images", None),
         ("voice", "voice"), ("personality", "personality"), ("approvals", "approvals"), ("status", "status"))
_IMAGE_MODES = ("auto", "native", "text")
_IMAGE_WORDS = frozenset({"images", "image", "vision", "изображения", "картинки"})


def _current_image_mode() -> str:
    """The mode the next inbound image is routed with: the gateway home's config.yaml, where
    ``_save_gateway_config_key`` writes it."""
    from gateway.run import _load_gateway_config

    mode = str((_load_gateway_config().get("agent") or {}).get("image_input_mode") or "auto").strip().lower()
    return mode if mode in _IMAGE_MODES else "auto"


class GatewaySettingsCommandsMixin:

    async def _handle_settings_command(self, event: MessageEvent) -> str | None:
        """``/settings`` opens the menu; ``/settings images <auto|native|text>`` sets the image mode."""
        args = event.get_command_args().split()
        if args and args[0].lower() in _IMAGE_WORDS:
            if len(args) > 1:
                return self._apply_image_mode(args[1].lower())
            return await self._send_image_mode_picker(event) or t(
                "gateway.settings.images_status", mode=self._image_mode_label(_current_image_mode()))
        session_key = self._session_key_for_source(event.source)

        async def _open(_chat_id: str, value: str) -> str:
            return await self._open_settings_item(event, value)

        sent = await self._try_send_choice_picker(
            event, session_key, title=t("gateway.settings.title"),
            choices=[{"value": item, "label": t(f"gateway.settings.item_{item}"), "is_current": False}
                     for item, _command in _MENU],
            on_choice_selected=_open,
        )
        if sent:
            return None
        return t("gateway.settings.text_menu", mode=self._image_mode_label(_current_image_mode()))

    async def _open_settings_item(self, event: MessageEvent, item: str) -> str:
        """Run the menu item's own command as if typed; its picker or reply arrives as a new message."""
        label = t(f"gateway.settings.item_{item}")
        command = dict(_MENU).get(item)
        if item == "images":
            reply = await self._send_image_mode_picker(event)
        elif command:
            handler = self._command_handler_table((command,))[command]
            reply = await handler(dataclasses.replace(event, text="/" + command))
        else:
            return t("gateway.settings.title")
        if reply:
            adapter = self._delivery_adapter_for(event.source)
            if adapter is not None:
                await adapter.send(str(event.source.chat_id), reply,
                                   metadata=self._thread_metadata_for_source(event.source))
        return t("gateway.settings.opened", item=label)

    @staticmethod
    def _image_mode_label(mode: str) -> str:
        return t(f"gateway.settings.mode_{mode}")

    async def _send_image_mode_picker(self, event: MessageEvent) -> str | None:
        """The mode picker; returns a text reply when the adapter has no pickers, else None."""
        current = _current_image_mode()

        async def _apply(_chat_id: str, value: str) -> str:
            return self._apply_image_mode(value)

        sent = await self._try_send_choice_picker(
            event, self._session_key_for_source(event.source),
            title=t("gateway.settings.images_title", mode=self._image_mode_label(current)),
            choices=[{"value": mode, "label": self._image_mode_label(mode), "is_current": mode == current}
                     for mode in _IMAGE_MODES],
            on_choice_selected=_apply,
        )
        return None if sent else t("gateway.settings.images_status", mode=self._image_mode_label(current))

    def _apply_image_mode(self, mode: str) -> str:
        if mode not in _IMAGE_MODES:
            return t("gateway.settings.images_unknown", arg=mode)
        if not self._save_gateway_config_key("agent.image_input_mode", mode):
            return t("gateway.settings.images_save_failed")
        return t("gateway.settings.images_saved", mode=self._image_mode_label(mode))
