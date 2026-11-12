"""Quick Setup's speech-to-text step (hermes_cli/setup_quick.py::_quick_setup_voice).

Quick Setup is the recommended first-time path and skips the Tools step, so the one place a user
connects Telegram also has to be the place voice transcription is offered. Without it the bot
accepts voice messages it can never hear.
"""

from unittest.mock import patch

from hermes_cli.setup_quick import _quick_setup_voice


def _run(platforms, provider, answer=0):
    with (
        patch("hermes_cli.tools_config._get_enabled_platforms", return_value=platforms),
        patch("tools.transcription_tools.active_stt_provider", return_value=provider),
        patch("hermes_cli.setup.prompt_choice", return_value=answer) as prompt,
        patch("hermes_cli.tools_config._configure_toolset") as configure,
        patch("hermes_cli.setup.save_config"),
    ):
        _quick_setup_voice({})
    return prompt, configure


def test_messaging_without_stt_offers_setup_and_configures_it():
    prompt, configure = _run(["cli", "telegram"], "none")
    assert prompt.called
    assert "telegram" in prompt.call_args[0][0]
    assert configure.call_args[0][0] == "stt"


def test_declining_leaves_stt_untouched():
    prompt, configure = _run(["cli", "telegram"], "none", answer=1)
    assert prompt.called
    assert not configure.called


def test_cli_only_install_is_not_asked():
    # No messaging platform, no voice messages to transcribe: the question would be noise.
    prompt, configure = _run(["cli"], "none")
    assert not prompt.called
    assert not configure.called


def test_working_stt_is_not_asked_again():
    prompt, configure = _run(["cli", "telegram"], "local")
    assert not prompt.called
    assert not configure.called
