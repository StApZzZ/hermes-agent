"""/settings: one chat menu over the settings commands, plus the image-recognition mode.

The menu opens each command's own picker (no second implementation of any setting); the image
mode is written to config.yaml, where inbound image routing reads it on the next picture.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import gateway.run as gateway_run
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import SendResult
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource


def _event(text="/settings"):
    return MessageEvent(text=text, source=SessionSource(
        platform=Platform.TELEGRAM, user_id="12345", chat_id="67890", user_name="tester"))


class _PickerAdapter:
    def __init__(self, success=True):
        self.calls, self.sent, self._success = [], [], success

    async def send_choice_picker(self, **kwargs):
        self.calls.append(kwargs)
        return SendResult(success=self._success, message_id="m1")

    async def send(self, chat_id, text, metadata=None):
        self.sent.append(text)
        return SendResult(success=True, message_id="m2")


class _TextOnlyAdapter:
    async def send(self, chat_id, text, metadata=None):
        return SendResult(success=True, message_id="m2")


def _runner(adapter):
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {}
    runner._reasoning_config = None
    runner._show_reasoning = False
    runner._running_agents = {}
    runner._session_db = None
    runner.hooks = MagicMock()
    runner.hooks.emit = AsyncMock()
    runner._delivery_adapter_for = lambda source: adapter
    runner._thread_metadata_for_source = lambda source, anchor=None: {}
    runner._reply_anchor_for_event = lambda event: None
    return runner


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    return tmp_path


def _saved_mode(home):
    from agent.image_routing import decide_image_input_mode

    cfg = gateway_run._load_gateway_config()
    # The routing decision the next inbound image takes (an unknown model never forces native).
    return cfg["agent"]["image_input_mode"], decide_image_input_mode("fixture", "fixture-model", cfg)


@pytest.mark.asyncio
async def test_menu_lists_every_setting_and_opens_the_commands_own_picker(home):
    adapter = _PickerAdapter()
    runner = _runner(adapter)

    assert await runner._handle_settings_command(_event()) is None
    menu = adapter.calls[0]
    assert [c["value"] for c in menu["choices"]] == [
        "model", "reasoning", "fast", "images", "voice", "personality", "approvals", "status"]

    reply = await menu["on_choice_selected"]("67890", "reasoning")
    assert "Reasoning" in reply
    reasoning_picker = adapter.calls[1]
    assert "none" in [c["value"] for c in reasoning_picker["choices"]]  # /reasoning's picker, unchanged


@pytest.mark.asyncio
async def test_images_item_saves_the_mode_that_routes_the_next_image(home):
    adapter = _PickerAdapter()
    runner = _runner(adapter)
    await runner._handle_settings_command(_event())

    await adapter.calls[0]["on_choice_selected"]("67890", "images")
    image_picker = adapter.calls[1]
    assert [(c["value"], c["is_current"]) for c in image_picker["choices"]] == [
        ("auto", True), ("native", False), ("text", False)]

    reply = await image_picker["on_choice_selected"]("67890", "native")
    assert "Native" in reply
    assert _saved_mode(home) == ("native", "native")


@pytest.mark.asyncio
async def test_typed_image_mode_and_text_fallback_without_pickers(home):
    runner = _runner(_TextOnlyAdapter())

    menu = await runner._handle_settings_command(_event())
    assert "/settings images auto|native|text" in menu and "/model" in menu

    assert "Description" in await runner._handle_settings_command(_event("/settings images text"))
    assert _saved_mode(home) == ("text", "text")
    assert "Unknown mode" in await runner._handle_settings_command(_event("/settings images sideways"))
    assert _saved_mode(home)[0] == "text"


@pytest.mark.asyncio
async def test_text_only_commands_reply_in_the_chat(home, monkeypatch):
    adapter = _PickerAdapter()
    runner = _runner(adapter)
    monkeypatch.setattr(runner, "_handle_approvals_command", AsyncMock(return_value="Approval mode: manual"),
                        raising=False)
    await runner._handle_settings_command(_event())

    await adapter.calls[0]["on_choice_selected"]("67890", "approvals")
    assert adapter.sent == ["Approval mode: manual"]


def _telegram_adapter():
    from plugins.platforms.telegram.adapter import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(enabled=True, token="***", extra={})
    adapter._choice_picker_state = {}
    adapter._edit_result_text = AsyncMock()
    return adapter


@pytest.mark.asyncio
async def test_telegram_keeps_a_picker_opened_by_a_picker_choice():
    """Choosing a menu item sends the next picker inside the callback; its state must survive."""
    adapter = _telegram_adapter()
    nested = {"msg_id": 2, "choices": [{"value": "native"}], "on_choice_selected": AsyncMock()}

    async def open_next(_chat_id, _value):
        adapter._choice_picker_state["67890"] = nested
        return "Settings → Images"

    adapter._choice_picker_state["67890"] = {"msg_id": 1, "choices": [{"value": "images"}],
                                             "on_choice_selected": open_next}
    await adapter._handle_choice_picker_callback(SimpleNamespace(answer=AsyncMock()), "cp:0", "67890")

    assert adapter._choice_picker_state["67890"] is nested
