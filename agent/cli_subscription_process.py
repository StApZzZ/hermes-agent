"""One-shot, credential-owning CLI processes used by subscription providers."""

from __future__ import annotations

import json
from pathlib import Path
import signal
import subprocess
from typing import Any


API_ENV_KEYS = frozenset({
    "OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_GENAI_USE_VERTEXAI", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
})
GEMINI_POLICY = '''[[rule]]
toolName = "*"
decision = "deny"
priority = 999

[[rule]]
toolName = "*"
mcpName = "*"
decision = "deny"
priority = 999
'''


def cli_argv(engine: str, command: str, args: list[str], model: str, *, policy_path: str,
             system_path: str | None = None) -> list[str]:
    """No shell, prompt argument or permission-bypass flag crosses this boundary."""
    tails = {
        "codex": ["exec", "--json", "--ephemeral", "--sandbox", "read-only", "--skip-git-repo-check",
                  "-c", 'forced_login_method="chatgpt"', "-c", 'model_provider="openai"',
                  "-c", 'web_search="disabled"', "-c", "features.shell_tool=false"],
        "claude": ["--print", "--output-format", "json", "--no-session-persistence", "--restricted",
                   "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                   "--disallowedTools", "mcp__*", "--disable-slash-commands",
                   "--settings", '{"forceLoginMethod":"claudeai"}'],
        "gemini": ["--output-format", "json", "--policy", policy_path],
    }
    if engine not in tails:
        raise ValueError(f"Unsupported subscription CLI: {engine}")
    argv = [command, *args, *tails[engine]]
    if engine == "claude" and system_path:
        argv += ["--system-prompt-file", system_path]
    if model and model != "default":
        argv.append("--model=" + model)
    if engine == "codex":
        argv.append("-")
    elif engine == "gemini":
        # An explicit headless prompt prevents a pipe from starting the TUI.
        # The conversation itself is stdin, never an OS command-line argument.
        argv.extend(["--prompt", "Continue the Hermes request provided on stdin."])
    return argv


def subscription_env() -> dict[str, str]:
    from tools.environments.local import hermes_subprocess_env

    env = hermes_subprocess_env()
    for key in API_ENV_KEYS:
        env.pop(key, None)
    env["NO_BROWSER"] = "true"
    return env


def validate_gemini_auth(env: dict[str, str]) -> None:
    """Read settings, never OAuth credentials; a saved API/Vertex selection must not bill an API."""
    home = Path(env.get("HOME") or Path.home())
    path = home / ".gemini" / "settings.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        selected = data.get("security", {}).get("auth", {}).get("selectedType")
    except (ValueError, AttributeError) as exc:
        raise ValueError("Cannot verify Gemini authentication settings; select Sign in with Google in gemini.") from exc
    if selected not in (None, "oauth-personal"):
        raise ValueError("gemini-cli requires Sign in with Google, not API key or Vertex authentication.")


def timeout_seconds(timeout: Any) -> float:
    if isinstance(timeout, (int, float)):
        return max(0.0, float(timeout))
    read = getattr(timeout, "read", None)
    return max(0.0, float(read)) if isinstance(read, (int, float)) else 900.0


def terminate_process(proc: subprocess.Popen) -> None:
    """The CLIs are Node/Rust launchers with children of their own: end the whole tree."""
    from agent.deadline import kill_process_tree

    if proc.poll() is not None:
        return
    kill_process_tree(proc.pid, sig=signal.SIGTERM)
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        kill_process_tree(proc.pid)
        proc.wait(timeout=2)


def _object(text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise RuntimeError("The CLI returned malformed JSON; no HTTP fallback was attempted.") from exc
    if not isinstance(data, dict):
        raise RuntimeError("The CLI returned a non-object JSON response.")
    return data


class CLIReportedError(RuntimeError):
    """The CLI ran and reported a failed turn in its structured output."""


def _reported_error(row: dict, *keys: str) -> str:
    """The CLI's own structured error text (usage limits, model errors), never its stderr: login
    diagnostics with device codes or OAuth URLs go to stderr, the JSON result carries API errors."""
    error = row.get("error")
    candidates = [error.get("message") if isinstance(error, dict) else error, *(row.get(key) for key in keys)]
    text = next((c for c in candidates if isinstance(c, str) and c.strip()), "")
    return (": " + " ".join(text.split())[:300]) if text else ""


def decode_result(engine: str, stdout: str) -> tuple[str, dict | None]:
    if engine == "codex":
        rows = [_object(line) for line in stdout.splitlines() if line.strip()]
        failed = [row for row in rows if row.get("type") in {"error", "turn.failed"}]
        if failed:
            raise CLIReportedError("Codex CLI reported a failed turn" + _reported_error(failed[-1], "message")
                               + "; no HTTP fallback was attempted.")
        completed = [row for row in rows if row.get("type") == "turn.completed"]
        if not completed:
            raise RuntimeError("Codex CLI did not complete its turn.")
        text = "\n".join(str(row["item"].get("text") or "") for row in rows
                         if row.get("type") == "item.completed" and row.get("item", {}).get("type") == "agent_message")
        return text, completed[-1].get("usage")
    data = _object(stdout)
    if data.get("is_error") or data.get("error"):
        raise CLIReportedError(f"{engine} CLI reported an error" + _reported_error(data, "result")
                           + "; no HTTP fallback was attempted.")
    key = "result" if engine == "claude" else "response"
    if not isinstance(data.get(key), str):
        raise RuntimeError(f"{engine} CLI did not return its {key} text.")
    return data[key], data.get("usage")


def completion_usage(engine: str, usage: dict | None) -> Any:
    if not isinstance(usage, dict):
        return None
    from types import SimpleNamespace

    cached = int(usage.get("cached_input_tokens") or usage.get("cache_read_input_tokens") or 0)
    prompt = int(usage.get("input_tokens") or 0)
    if engine == "claude":
        prompt += cached + int(usage.get("cache_creation_input_tokens") or 0)
    output = int(usage.get("output_tokens") or 0)
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=output, total_tokens=prompt + output,
                           prompt_tokens_details=SimpleNamespace(cached_tokens=cached))
