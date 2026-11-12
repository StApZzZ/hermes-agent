"""Speech-to-text setup (``hermes setup stt``): local Whisper through faster-whisper.

One pass picks a model (each size shown with its download, memory and what it is good for on this
machine) and a language, installs the dependency, writes ``stt.*``, downloads the model so the first
voice message does not wait for it, and offers a gateway restart. Cloud STT providers stay in
``hermes tools`` -> Speech-to-Text. setup.py names are resolved through the module object so test
patches on ``hermes_cli.setup.<name>`` take effect.
"""

from __future__ import annotations

import subprocess
import sys

from tools.transcription_common import DEFAULT_LOCAL_MODEL, LOCAL_WHISPER_MODELS, LocalWhisperModel

_GIB = 1024 ** 3
# Largest multilingual size preselected for a machine with at least this much RAM (no NVIDIA GPU).
_RAM_RECOMMENDATIONS = ((6 * _GIB, "turbo"), (3 * _GIB, "small"), (0, "base"))
# A model whose peak memory passes this share of the machine's RAM is flagged as a tight fit.
_TIGHT_RAM_SHARE = 0.6

# (Whisper language code, label); "" is auto-detect.
_LANGUAGES = (
    ("", "Auto-detect (each message; often wrong on short clips)"),
    ("en", "English"), ("ru", "Russian"), ("uk", "Ukrainian"), ("de", "German"), ("es", "Spanish"),
    ("fr", "French"), ("it", "Italian"), ("pt", "Portuguese"), ("tr", "Turkish"), ("zh", "Chinese"),
    ("ja", "Japanese"))
_OTHER_LANGUAGE = "Other: type a Whisper language code"

_PREFETCH_SCRIPT = (
    "import sys, time\n"
    "from tools.transcription_local import _load_local_whisper_model\n"
    "started = time.monotonic()\n"
    "_load_local_whisper_model(sys.argv[1])\n"
    "print(round(time.monotonic() - started))\n")


def recommended_local_model(ram_bytes: int | None, gpu: str) -> str:
    """The size to preselect: turbo on an NVIDIA GPU or with room for it, smaller otherwise."""
    if gpu == "nvidia":
        return "turbo"
    if ram_bytes is None:
        return "small"
    return next(name for floor, name in _RAM_RECOMMENDATIONS if ram_bytes >= floor)


def _size(mb: int) -> str:
    return f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{mb} MB"


def local_model_label(model: LocalWhisperModel, *, recommended: str = "", ram_bytes: int | None = None) -> str:
    """One picker row: name, size, download, memory, what it is for, and marks for this machine."""
    marks = [mark for mark, on in (
        ("★ recommended", model.name == recommended),
        ("may not fit in RAM", ram_bytes is not None and model.ram_mb * 1_000_000 > ram_bytes * _TIGHT_RAM_SHARE),
    ) if on]
    text = (f"{model.name:<18} {model.params:>5} · {_size(model.download_mb):>6} download · "
            f"~{_size(model.ram_mb)} RAM · {model.summary}")
    return text + (f"  [{', '.join(marks)}]" if marks else "")


def _menu(title: str, items: list[str], selected: int, description: str) -> int | None:
    """Arrow-key single choice; ``None`` when the user backs out."""
    from hermes_cli.curses_ui import curses_radiolist

    index = curses_radiolist(title, items, selected=selected, cancel_returns=-1, description=description)
    return None if index < 0 else index


def _current_local_model(stt: dict) -> str:
    return str((stt.get("local") or {}).get("model") or DEFAULT_LOCAL_MODEL)


def _pick_model(stt: dict, ram_bytes: int | None, gpu: str) -> str | None:
    recommended = recommended_local_model(ram_bytes, gpu)
    names = [model.name for model in LOCAL_WHISPER_MODELS]
    # A model the user already chose stays under the cursor; a fresh setup starts on the recommendation.
    current = _current_local_model(stt) if stt.get("provider") == "local" else recommended
    machine = (f"This machine: {ram_bytes / _GIB:.1f} GB RAM" if ram_bytes else "This machine: RAM unknown") + (
        ", NVIDIA GPU" if gpu == "nvidia" else ", no NVIDIA GPU (runs on the CPU)")
    index = _menu(
        "Whisper model:", [local_model_label(m, recommended=recommended, ram_bytes=ram_bytes)
                           for m in LOCAL_WHISPER_MODELS],
        names.index(current) if current in names else names.index(recommended),
        f"{machine}. Bigger models hear better and answer slower.\n"
        "Models marked English only cannot transcribe other languages.")
    return None if index is None else names[index]


def _current_language(stt: dict) -> str:
    local = stt.get("local") or {}
    return str(local.get("language") or stt.get("language") or "").strip().lower()


def _pick_language(stt: dict) -> str | None:
    codes = [code for code, _ in _LANGUAGES]
    current = _current_language(stt)
    labels = [f"{label} ({code})" if code else label for code, label in _LANGUAGES] + [_OTHER_LANGUAGE]
    selected = codes.index(current) if current in codes else len(codes)
    index = _menu("Language of your voice messages:", labels, selected,
                  "Naming the language makes Whisper much more accurate than auto-detect.")
    if index is None:
        return None
    if index < len(codes):
        return codes[index]
    code = _setup.prompt("Whisper language code (for example pl, kk, he)", current or None).strip().lower()
    if not (code.isalpha() and len(code) in (2, 3)):
        _setup.print_error(f"'{code}' is not a Whisper language code (two letters, or haw / yue).")
        return None
    return code


def _install_faster_whisper() -> bool:
    """Add the stt-whisper extra to Hermes's environment; later processes boot with it."""
    import pm
    from pm.extras import available, extra_supported

    if available("stt-whisper"):
        return True
    if not extra_supported("stt-whisper"):
        _setup.print_error("faster-whisper has no build for this platform.")
        _setup.print_info("Pick another Speech-to-Text provider in: hermes tools")
        return False
    _setup.print_info("Installing faster-whisper...")
    try:
        pm.sync_venv(["stt-whisper"], explicit=True)
    except (pm.InstallError, OSError, ValueError) as exc:
        _setup.print_error(f"faster-whisper install failed: {exc}")
        _setup.print_info("Retry with: hermes setup stt")
        return False
    _setup.print_success("faster-whisper installed.")
    return True


def _fresh_python() -> tuple[list[str], dict | None]:
    """Python of the environment PM just committed: a sync never switches the running process to it."""
    from pm import paths
    from pm.environments import activation_environment, project_python, selected_venv, venv_command

    root = paths.repo_root()
    if project_python(root).is_file():
        return venv_command(root, selected_venv(root)), activation_environment(root)
    return [sys.executable], None  # a developer venv: this interpreter is the environment


def _prefetch_model(model: str) -> None:
    """Download and load the model once, so the first voice message does not wait for it."""
    _setup.print_info(f"Downloading and loading '{model}' (one time; large models take a few minutes)...")
    command, env = _fresh_python()
    result = subprocess.run([*command, "-c", _PREFETCH_SCRIPT, model], env=env, stdout=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, text=True, check=False)
    if result.returncode != 0:
        _setup.print_warning("The model did not load now; it will download on the first voice message.")
        return
    _setup.print_success(f"Model '{model}' is ready (loaded in {result.stdout.strip() or '?'} s).")


def _offer_gateway_restart() -> None:
    """A running gateway keeps the environment it booted with until it restarts."""
    from gateway.status import get_running_pid

    if not get_running_pid():
        return
    if _setup.prompt_yes_no("Restart the gateway now so messengers use Whisper?", True):
        from hermes_cli.relaunch import build_relaunch_argv

        subprocess.run(build_relaunch_argv(["gateway", "restart"]), check=False)
    else:
        _setup.print_info("Restart it later with: hermes gateway restart")


def setup_stt(config: dict) -> None:
    """Standalone local Whisper setup (for 'hermes setup stt')."""
    from hermes_platform.host import facts

    _setup.print_info("Voice messages and voice mode are transcribed by Whisper on this machine:")
    _setup.print_info("free, no API key, and the audio never leaves it.")
    _setup.print_info("Cloud providers (Groq, OpenAI, ...) are in: hermes tools → Speech-to-Text")
    stt = config.setdefault("stt", {})
    model = _pick_model(stt, facts.ram_total_bytes(), facts.gpu_class())
    language = None if model is None else _pick_language(stt)
    if model is None or language is None:
        _setup.print_info("Speech-to-Text left unchanged.")
        return
    if not _install_faster_whisper():
        return
    stt.update(enabled=True, provider="local", language=language)
    # The per-provider hint would override the global one; the global applies to cloud providers too.
    stt.setdefault("local", {}).update(model=model, language="")
    _setup.save_config(config)
    _setup.print_success(f"Speech-to-Text: local Whisper '{model}', language: {language or 'auto-detect'}")
    _prefetch_model(model)
    _offer_gateway_restart()


import hermes_cli.setup as _setup
