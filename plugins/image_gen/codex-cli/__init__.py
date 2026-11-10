"""Image generation through the signed-in Codex CLI — the ChatGPT subscription, no API key.

``codex exec`` draws with its built-in image tool and saves the picture under
``$CODEX_HOME/generated_images/<thread_id>/``; the thread id comes from the JSON event stream. The
picture is copied into Hermes's generated-image cache and Codex's copy is removed. Like the
``codex-cli`` model provider, credentials never leave Codex CLI: the process gets the subscription
environment (API-key variables removed), a read-only sandbox, no shell tool and no web search, so the
only thing it can do is draw. Source images for edits are passed as ``--image`` files.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Optional

from agent.image_gen_provider import DEFAULT_ASPECT_RATIO, resolve_aspect_ratio, save_b64_image, success_response
from plugins.image_gen._common import (
    StaticImageGenProvider, collect_source_images, error_factory, prompt_required_error)

logger = logging.getLogger(__name__)

PROVIDER = "codex-cli"
MODEL = "codex-cli"
_TIMEOUT_SECONDS = 300
_MAX_REFERENCE_IMAGES = 4
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
_ASPECTS = {"landscape": "landscape (3:2)", "square": "square (1:1)", "portrait": "portrait (2:3)"}
_INSTRUCTION = (
    "Use your image generation tool to create exactly one image{edit}. Aspect: {aspect}.\n"
    "Image request: {prompt}\n\n"
    "Do not run commands, read files or search the web. After the image is generated, reply with the single word DONE."
)
_SANDBOX_ARGS = (
    "exec", "--json", "--ephemeral", "--sandbox", "read-only", "--skip-git-repo-check",
    "-c", 'forced_login_method="chatgpt"', "-c", 'web_search="disabled"',
    "-c", "features.shell_tool=false", "-c", "features.image_generation=true",
)


def _codex_command() -> Optional[str]:
    """The ``codex-cli`` model provider's command when it resolves, else ``codex`` on PATH."""
    try:
        from hermes_cli.auth import resolve_external_process_provider_credentials

        command = resolve_external_process_provider_credentials(PROVIDER).get("command")
        if command:
            return command
    except Exception:  # health: allow BLE001 -- provider registry absent or CLI missing: fall back to PATH
        logger.debug("codex-cli provider did not resolve; looking for codex on PATH", exc_info=True)
    return shutil.which("codex")


def build_argv(command: str, image_paths: list[str]) -> list[str]:
    """No shell and no prompt on the command line: the instruction goes on stdin (``-``)."""
    return [command, *_SANDBOX_ARGS, *("--image=" + path for path in image_paths), "-"]


def build_instruction(prompt: str, aspect: str, edit: bool) -> str:
    edit_note = " based on the attached image(s), keeping what the request does not change" if edit else ""
    return _INSTRUCTION.format(edit=edit_note, aspect=_ASPECTS.get(aspect, _ASPECTS["landscape"]), prompt=prompt)


def _materialize_source(source: str, work: Path, index: int) -> str:
    """A local file path Codex can read for one source image (local path, data: URL or http(s) URL)."""
    if source.startswith("data:"):
        header, _, payload = source.partition(",")
        extension = header.split("/")[-1].split(";")[0] or "png"
        path = work / f"source-{index}.{extension}"
        path.write_bytes(base64.b64decode(payload))
        return str(path)
    if source.startswith(("http://", "https://")):
        from agent.image_gen_provider import save_url_image

        return str(save_url_image(source, prefix="codex_cli_source"))
    path = Path(os.path.expanduser(source))
    if not path.is_file():
        raise ValueError(f"source image not found: {source}")
    return str(path)


def _thread_id(stdout: str) -> Optional[str]:
    for line in stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("type") == "thread.started":
            return str(row.get("thread_id") or "") or None
    return None


def _reported_error(stdout: str) -> Optional[str]:
    """The CLI's own structured error (usage limit, login), never stderr (it may hold device codes)."""
    for line in reversed(stdout.splitlines()):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("type") in ("error", "turn.failed"):
            error = row.get("error")
            text = error.get("message") if isinstance(error, dict) else error or row.get("message")
            return " ".join(str(text or "Codex reported a failed turn").split())[:300]
    return None


def _codex_home(env: dict[str, str]) -> Path:
    home = env.get("CODEX_HOME", "").strip()
    return Path(home) if home else Path(env.get("HOME") or Path.home()) / ".codex"


def _run_codex(argv: list[str], instruction: str, env: dict[str, str], cwd: str) -> tuple[int, str]:
    """Run one owned ``codex exec``; the whole process tree ends on timeout or error."""
    from agent.cli_subscription_process import terminate_process
    from hermes_cli._subprocess_compat import windows_hide_flags
    from hermes_cli.process_identity import release_process_group_once_empty, spawn_owned

    proc = spawn_owned(argv, purpose="codex-cli-image", stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, text=True, encoding="utf-8", cwd=cwd, env=env,
                       creationflags=windows_hide_flags())
    try:
        stdout, _ = proc.communicate(instruction, timeout=_TIMEOUT_SECONDS)
        return proc.returncode, stdout
    finally:
        terminate_process(proc)
        release_process_group_once_empty(proc.pid, proc=proc)


class CodexCLIImageGenProvider(StaticImageGenProvider):
    """Pictures from the ChatGPT subscription through ``codex exec`` (Codex picks model and size)."""

    provider_id = PROVIDER
    label = "Codex CLI (ChatGPT subscription)"
    models = {MODEL: {"display": "Codex CLI image tool", "speed": "~30-60s",
                      "strengths": "ChatGPT subscription, no API key; text and image inputs"}}
    default_model_id = MODEL
    price = "subscription"

    def is_available(self) -> bool:
        return bool(_codex_command())

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": self.label, "badge": "subscription",
            "tag": "Draws through your signed-in Codex CLI; credentials stay inside Codex CLI",
            "env_vars": [],
            "post_setup_hint": "Sign in once with `hermes auth add codex-cli` (runs `codex login`).",
        }

    def capabilities(self) -> dict[str, Any]:
        return {"modalities": ["text", "image"], "max_reference_images": _MAX_REFERENCE_IMAGES}

    def generate(self, prompt: str, aspect_ratio: str = DEFAULT_ASPECT_RATIO, *,
                 image_url: Optional[str] = None, reference_image_urls: Optional[list[str]] = None,
                 **kwargs: Any) -> dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)
        if not prompt:
            return prompt_required_error(PROVIDER, aspect)
        fail = error_factory(PROVIDER, aspect, model=MODEL, prompt=prompt)
        command = _codex_command()
        if not command:
            return fail("Codex CLI is not installed: npm install -g @openai/codex, then codex login.",
                        "missing_dependency")
        from agent.cli_subscription_process import subscription_env

        env = subscription_env()
        sources = collect_source_images(image_url, reference_image_urls, _MAX_REFERENCE_IMAGES)
        with tempfile.TemporaryDirectory(prefix="hermes-codex-image-") as work:
            try:
                paths = [_materialize_source(s, Path(work), i) for i, s in enumerate(sources)]
            except (OSError, ValueError) as exc:
                return fail(f"Invalid source image for Codex: {exc}", "invalid_image_input")
            try:
                code, stdout = _run_codex(build_argv(command, paths), build_instruction(prompt, aspect, bool(paths)),
                                          env, work)
            except subprocess.TimeoutExpired:
                return fail(f"Codex CLI did not finish the image in {_TIMEOUT_SECONDS} s.", "timeout")
            except OSError as exc:
                return fail(f"Could not start Codex CLI: {exc}", "missing_dependency")
        return self._collect(stdout, code, env, fail, prompt=prompt, aspect=aspect, edits=len(sources))

    def _collect(self, stdout: str, code: int, env: dict[str, str], fail, *, prompt: str, aspect: str,
                 edits: int) -> dict[str, Any]:
        reported = _reported_error(stdout)
        if reported:
            return fail(f"Codex CLI: {reported}", "api_error")
        thread = _thread_id(stdout)
        folder = _codex_home(env) / "generated_images" / thread if thread else None
        images = sorted(p for p in folder.iterdir() if p.suffix.lower() in _IMAGE_SUFFIXES) if folder and folder.is_dir() else []
        if not images:
            detail = f"exit code {code}" if code else "no image was produced"
            return fail(f"Codex CLI returned no image ({detail}); check `codex login status`.", "empty_response")
        try:
            saved = save_b64_image(base64.b64encode(images[-1].read_bytes()).decode("ascii"), prefix="codex_cli",
                                   extension=images[-1].suffix.lstrip(".").lower())
        except OSError as exc:
            return fail(f"Could not save image to cache: {exc}", "io_error")
        finally:
            shutil.rmtree(folder, ignore_errors=True)  # the copy in Hermes's cache is the one that is kept
        return success_response(image=str(saved), model=MODEL, prompt=prompt, aspect_ratio=aspect, provider=PROVIDER,
                                modality="image" if edits else "text", extra={"input_image_count": edits})


def register(ctx) -> None:
    """Plugin entry point — register the Codex CLI image provider."""
    ctx.register_image_gen_provider(CodexCLIImageGenProvider())
