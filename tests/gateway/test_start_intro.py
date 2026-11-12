"""The /start capability intro (gateway/intro.py).

``/start`` is the ping every messaging client sends when a user first opens the bot. It used to be
answered with an empty string, so the first thing a new owner saw from their own agent was nothing
at all. The contract: the reply says what this agent can do *here*, promises nothing the platform's
toolsets do not include, and tells the truth about voice.
"""

from unittest.mock import patch

from gateway.intro import _CAPABILITY_LINES, _MAX_CAPABILITY_LINES, build_start_intro


def _intro(toolsets, stt="local"):
    with (
        patch("hermes_cli.config.load_config", return_value={}),
        patch("hermes_cli.tools_config._get_platform_tools", return_value=set(toolsets)),
        patch("tools.transcription_tools.active_stt_provider", return_value=stt),
    ):
        return build_start_intro("telegram")


def test_intro_names_the_enabled_capabilities():
    text = _intro({"terminal", "memory"})
    assert "run commands" in text
    assert "remember what matters" in text
    assert "/help" in text


def test_intro_never_promises_a_disabled_capability():
    # A platform without the browser toolset must not advertise browser control: the toolset gate
    # would refuse it, and the user would have been told the agent can do something it cannot.
    text = _intro({"terminal"})
    assert "run commands" in text
    assert "browser" not in text.lower()


def test_voice_line_follows_the_real_stt_state():
    assert "Voice messages work" in _intro({"terminal"}, stt="groq")
    assert "aren't transcribed yet" in _intro({"terminal"}, stt="none")


def test_long_capability_lists_are_capped_with_a_pointer():
    # A first message is an introduction, not a catalogue of every toolset that happens to be on.
    text = _intro({toolset for toolset, _ in _CAPABILITY_LINES})
    assert text.count("\n• ") == _MAX_CAPABILITY_LINES
    assert "…and more." in text


def test_intro_is_never_empty_even_with_no_toolsets():
    # The empty reply was the bug; a locked-down platform still gets an introduction.
    text = _intro(set())
    assert "Hermes" in text
    assert len(text.strip()) > 40
