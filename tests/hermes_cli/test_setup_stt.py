"""`hermes setup stt`: local Whisper model + language pick, written to stt.* in config.yaml."""

from unittest.mock import patch

import pytest

from hermes_cli import setup_stt
from hermes_cli.config import load_config
from tools.transcription_common import LOCAL_WHISPER_MODELS, STT_MODEL_CATALOG

_GIB = 1024 ** 3
_NAMES = [model.name for model in LOCAL_WHISPER_MODELS]
_LANGUAGE_CODES = [code for code, _ in setup_stt._LANGUAGES]


def _run(menu_answers, *, ram=8 * _GIB, gpu="none", install_ok=True, config=None):
    config = load_config() if config is None else config
    with patch.object(setup_stt, "_menu", side_effect=menu_answers) as menu, \
            patch.object(setup_stt, "_install_faster_whisper", return_value=install_ok), \
            patch.object(setup_stt, "_prefetch_model") as prefetch, \
            patch.object(setup_stt, "_offer_gateway_restart"), \
            patch("hermes_platform.host.facts.ram_total_bytes", return_value=ram), \
            patch("hermes_platform.host.facts.gpu_class", return_value=gpu):
        setup_stt.setup_stt(config)
    return menu, prefetch


@pytest.mark.parametrize(("ram", "gpu", "expected"), [
    (8 * _GIB, "none", "turbo"),
    (4 * _GIB, "none", "small"),
    (2 * _GIB, "none", "base"),
    (None, "unknown", "small"),
    (2 * _GIB, "nvidia", "turbo"),
])
def test_recommendation_follows_the_machine(ram, gpu, expected):
    assert setup_stt.recommended_local_model(ram, gpu) == expected


def test_choice_is_written_as_local_whisper_with_a_global_language_hint():
    _, prefetch = _run([_NAMES.index("turbo"), _LANGUAGE_CODES.index("ru")])

    stt = load_config()["stt"]
    assert (stt["provider"], stt["enabled"], stt["language"]) == ("local", True, "ru")
    assert stt["local"]["model"] == "turbo"
    # A per-provider hint would shadow the global one the user just picked.
    assert stt["local"]["language"] == ""
    prefetch.assert_called_once_with("turbo")


def test_auto_detect_clears_the_shipped_english_hint():
    _run([_NAMES.index("small"), _LANGUAGE_CODES.index("")])

    assert load_config()["stt"]["language"] == ""


def test_fresh_setup_preselects_the_recommendation():
    menu, _ = _run([None], ram=8 * _GIB)

    assert menu.call_args_list[0].args[2] == _NAMES.index("turbo")


def test_backing_out_or_a_failed_install_changes_nothing():
    before = load_config()["stt"]
    _run([None])
    _run([_NAMES.index("small"), None])
    _run([_NAMES.index("small"), _LANGUAGE_CODES.index("ru")], install_ok=False)

    assert load_config()["stt"] == before


def test_rows_flag_models_too_big_for_the_machine():
    large = next(m for m in LOCAL_WHISPER_MODELS if m.name == "large-v3")
    tiny = next(m for m in LOCAL_WHISPER_MODELS if m.name == "tiny")

    assert "may not fit" in setup_stt.local_model_label(large, ram_bytes=4 * _GIB)
    assert "may not fit" not in setup_stt.local_model_label(tiny, ram_bytes=4 * _GIB)


def test_every_offered_model_is_in_the_runtime_catalog_with_the_default_first():
    assert set(STT_MODEL_CATALOG["local"]) == set(_NAMES)
    assert STT_MODEL_CATALOG["local"][0] == "base"


def test_setup_accepts_the_stt_section():
    from hermes_cli.main import _build_cli_parser

    parser, _ = _build_cli_parser()
    assert parser.parse_args(["setup", "stt"]).section == "stt"
