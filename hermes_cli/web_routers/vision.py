"""``/api/vision/*`` — the dashboard's Images page: how pictures are recognised, and a test bench.

- ``settings``: ``agent.image_input_mode`` plus which backend describes images right now (the same
  ``resolve_vision_provider_client`` the agent uses), so a user on a subscription CLI sees that
  ``claude-cli`` does the work.
- ``tools``: the bundled ``image-recognition`` skill's ``doctor`` (tesseract, zbarimg, exiftool,
  claude, codex), run in the environment the agent's terminal gets.
- ``analyze``: runs ``vision_analyze`` and the skill's ``ocr``/``codes``/``meta`` on an image the page
  uploaded through ``/api/chat/image-upload``; only files in the profile's image staging dir.
- ``generation`` / ``generate``: which backend draws pictures (``image_gen.provider``, only backends
  available here) and one picture from it, returned inline as a data URL.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from hermes_cli.web_deps import late
from hermes_cli.web_routers._common import _CONFIG_MUTATION_LOCK, log as _log

router = APIRouter()

load_config = late("load_config", "hermes_cli.config")
save_config = late("save_config", "hermes_cli.config")
# Await-safe and without the skills-module lock: a minute-long recognition must not queue the
# rest of the dashboard behind it.
_config_profile_scope = late("_config_profile_scope", "hermes_cli.web_server_profiles")

IMAGE_MODES = ("auto", "native", "text")
_SKILL_ACTIONS = ("ocr", "codes", "meta")
_ACTIONS = ("describe", *_SKILL_ACTIONS)
_TOOL_TIMEOUT = 120


class VisionSettingsUpdate(BaseModel):
    image_input_mode: str


class GenerationProviderUpdate(BaseModel):
    provider: str


class GenerateRequest(BaseModel):
    prompt: str
    aspect_ratio: str = "landscape"


class VisionAnalyzeRequest(BaseModel):
    path: str
    actions: list[str] = Field(default_factory=lambda: ["describe"])
    prompt: str = ""
    lang: str = "rus+eng"


def _image_mode(config: dict) -> str:
    mode = str((config.get("agent") or {}).get("image_input_mode") or "auto").strip().lower()
    return mode if mode in IMAGE_MODES else "auto"


def _describer() -> tuple[Optional[dict], Optional[str]]:
    """``(backend, error)``: the provider/model ``vision_analyze`` would use now."""
    from agent.auxiliary_client import resolve_vision_provider_client

    try:
        provider, client, model = resolve_vision_provider_client()
    except Exception as exc:  # health: allow BLE001 -- a broken provider plugin must not take the page down; traceback logged
        _log.warning("Images page: vision backend resolution failed", exc_info=True)
        return None, str(exc)
    if client is None:
        return None, None
    return {"provider": provider, "model": model}, None


def _read_settings() -> dict:
    from agent.image_routing import decide_image_input_mode

    config = load_config()
    model_cfg = config.get("model") if isinstance(config.get("model"), dict) else {}
    provider = str(model_cfg.get("provider") or "")
    model = str(model_cfg.get("default") or model_cfg.get("model") or "")
    aux = ((config.get("auxiliary") or {}).get("vision") or {})
    backend, error = _describer()
    return {
        "image_input_mode": _image_mode(config),
        "modes": list(IMAGE_MODES),
        "main": {"provider": provider, "model": model},
        "aux": {"provider": str(aux.get("provider") or "auto"), "model": str(aux.get("model") or "")},
        "route": decide_image_input_mode(provider, model, config),
        "backend": backend,
        "backend_error": error,
    }


@router.get("/api/vision/settings")
async def get_vision_settings(profile: Optional[str] = None):
    def _run():
        with _config_profile_scope(profile):
            return _read_settings()

    return await asyncio.to_thread(_run)


@router.put("/api/vision/settings")
async def set_vision_settings(body: VisionSettingsUpdate, profile: Optional[str] = None):
    mode = body.image_input_mode.strip().lower()
    if mode not in IMAGE_MODES:
        raise HTTPException(status_code=400, detail=f"image_input_mode must be one of {', '.join(IMAGE_MODES)}")

    def _run():
        with _config_profile_scope(profile), _CONFIG_MUTATION_LOCK:
            config = load_config()
            agent = config.get("agent") if isinstance(config.get("agent"), dict) else {}
            config["agent"] = {**agent, "image_input_mode": mode}
            save_config(config)
            return _read_settings()

    return await asyncio.to_thread(_run)


def _skill_script() -> Path:
    from hermes_cli._startup_fast import project_root_str
    from hermes_constants import get_bundled_skills_dir

    bundled = get_bundled_skills_dir(Path(project_root_str()) / "skills")  # wrapper env, else checkout
    return bundled / "media" / "image-recognition" / "scripts" / "image_tools.py"


def _run_skill(*args: str) -> dict:
    """One helper command in the agent terminal's environment; its JSON, or ``{"error": ...}``."""
    from tools.environments.local import hermes_subprocess_env

    script = _skill_script()
    if not script.is_file():
        return {"error": "The image-recognition skill is not installed with this Hermes."}
    try:
        proc = subprocess.run(
            [sys.executable, "-I", str(script), *args], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=_TOOL_TIMEOUT, env=hermes_subprocess_env(), stdin=subprocess.DEVNULL,
            check=False)
    except subprocess.TimeoutExpired:
        return {"error": f"Timed out after {_TOOL_TIMEOUT} s."}
    try:
        return json.loads(proc.stdout)
    except ValueError:
        _log.warning("image-recognition %s printed no JSON (exit %s)", args[:1], proc.returncode)
        return {"error": f"The helper exited with code {proc.returncode}."}


@router.get("/api/vision/tools")
async def get_vision_tools(profile: Optional[str] = None):
    def _run():
        with _config_profile_scope(profile):
            return _run_skill("doctor")

    return await asyncio.to_thread(_run)


def _staged_image(path: str, profile: Optional[str]) -> Path:
    """The upload, only if it is a file inside the profile's image staging dir."""
    from hermes_cli.staged_uploads import resolve_upload_home
    from hermes_cli.web_routers.files import _chat_image_dir

    root = _chat_image_dir(resolve_upload_home(profile)).resolve()
    try:
        target = Path(path).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid image path") from exc
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(status_code=400, detail="Upload the image on this page first")
    return target


def _describe(path: Path, prompt: str) -> dict:
    from tools.vision_tools import vision_analyze_tool

    question = prompt.strip() or ("Describe this image precisely: the scene, objects and people, and "
                                  "transcribe any visible text verbatim. Answer in the language of the question.")
    result = json.loads(asyncio.run(vision_analyze_tool(image_url=str(path), user_prompt=question)))
    if result.get("success"):
        return {"text": result.get("analysis", "")}
    return {"error": result.get("error") or result.get("analysis") or "vision_analyze failed"}


@router.post("/api/vision/analyze")
async def analyze_image(body: VisionAnalyzeRequest, profile: Optional[str] = None):
    actions = [a for a in dict.fromkeys(body.actions) if a in _ACTIONS]
    if not actions:
        raise HTTPException(status_code=400, detail=f"actions must name some of {', '.join(_ACTIONS)}")
    target = await asyncio.to_thread(_staged_image, body.path, profile)

    def _one(action: str) -> dict:
        with _config_profile_scope(profile):
            if action == "describe":
                return _describe(target, body.prompt)
            extra = ("--lang", body.lang) if action == "ocr" else ()
            return _run_skill(action, str(target), *extra)

    results = await asyncio.gather(*(asyncio.to_thread(_one, action) for action in actions))
    return {"path": str(target), "results": dict(zip(actions, results))}


def _generation_state() -> dict:
    from agent.image_gen_choice import available_choices, selected_provider

    return {"provider": selected_provider(),
            "choices": [{"name": c.name, "label": c.label} for c in available_choices()]}


@router.get("/api/vision/generation")
async def get_generation(profile: Optional[str] = None):
    def _run():
        with _config_profile_scope(profile):
            return _generation_state()

    return await asyncio.to_thread(_run)


@router.put("/api/vision/generation")
async def set_generation(body: GenerationProviderUpdate, profile: Optional[str] = None):
    def _run():
        with _config_profile_scope(profile), _CONFIG_MUTATION_LOCK:
            state = _generation_state()
            if body.provider not in {c["name"] for c in state["choices"]}:
                raise HTTPException(status_code=400, detail=f"{body.provider} is not available here")
            config = load_config()
            section = config.get("image_gen") if isinstance(config.get("image_gen"), dict) else {}
            config["image_gen"] = {**section, "provider": body.provider}
            save_config(config)
            return _generation_state()

    return await asyncio.to_thread(_run)


def _inline_image(ref: str) -> str:
    """A cached picture as a data URL (the page cannot read the cache dir); remote URLs pass through."""
    import base64
    import mimetypes

    if ref.startswith(("http://", "https://", "data:")):
        return ref
    path = Path(ref)
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


@router.post("/api/vision/generate")
async def generate_image(body: GenerateRequest, profile: Optional[str] = None):
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Describe the picture first")

    def _run():
        from agent.image_gen_registry import get_active_provider

        with _config_profile_scope(profile):
            provider = get_active_provider()
            if provider is None:
                return {"success": False, "error": "No picture backend is selected. Pick one above."}
            result = provider.generate(prompt, body.aspect_ratio)
        if not result.get("success"):
            return {"success": False, "provider": result.get("provider"), "error": result.get("error")}
        return {"success": True, "provider": result.get("provider"), "model": result.get("model"),
                "path": result.get("image"), "image": _inline_image(str(result.get("image")))}

    return await asyncio.to_thread(_run)
