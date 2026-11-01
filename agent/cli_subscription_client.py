"""Hermes remains the agent; official subscription CLIs supply text completions.

The shared ACP tool bridge carries Hermes tool schemas/calls. Native CLI tools
are disabled or read-only; credentials stay inside each CLI. This client has no
HTTP transport, token reader, OAuth refresh implementation or provider fallback.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
from typing import Any

from hermes_cli.process_identity import release_process_group_once_empty, spawn_owned
from agent.acp_openai_bridge import (
    completion_to_stream_chunks,
    extract_tool_calls_from_text,
    render_tool_bridge_sections,
)
from agent.cli_subscription_process import (
    GEMINI_POLICY, CLIReportedError, cli_argv, completion_usage, decode_result, subscription_env,
    terminate_process, timeout_seconds, validate_gemini_auth,
)


def request_parts(messages: list[dict], tools: list[dict] | None, tool_choice: Any, response_format: Any) -> tuple[str, str]:
    """``(system, conversation)``. The system part is identical on every call of a session, so a
    CLI that takes it as its system prompt caches it; the conversation only ever grows by appending."""
    system = [str(m["content"]) for m in messages if m.get("role") == "system" and isinstance(m.get("content"), str)]
    system += ["You supply the next model response for Hermes Agent. Hermes owns the conversation and executes all tools.",
               "Do not execute native CLI tools or delegate. Request Hermes tools through the tool-call contract below.",
               *render_tool_bridge_sections(tools, tool_choice)]
    if response_format:
        system.append("Required response format: " + json.dumps(response_format, ensure_ascii=False, sort_keys=True))
    conversation = ["Conversation messages (ordered JSON records; continue the last request):"]
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(
            not isinstance(block, dict) or block.get("type") not in {"text", "input_text"} for block in content
        ):
            raise ValueError("Subscription CLI providers currently accept text only; image/audio blocks cannot be discarded.")
        if message.get("role") == "system" and isinstance(content, str):
            continue
        # Preserve tool-call IDs, names, results and roles, not just rendered content.
        conversation.append(json.dumps(message, ensure_ascii=False, sort_keys=True))
    return "\n\n".join(system), "\n\n".join(conversation)


def request_prompt(messages: list[dict], tools: list[dict] | None, tool_choice: Any, response_format: Any) -> str:
    return "\n\n".join(request_parts(messages, tools, tool_choice, response_format))


class SubscriptionCLIClient:
    HERMES_SKIP_TRANSPORT_WRAP = True
    HERMES_SKIP_ASYNC_WRAP = True

    def __init__(self, *, engine: str, command: str | None = None, args: list[str] | None = None,
                 api_key: str | None = None, base_url: str | None = None, acp_cwd: str | None = None, **_: Any):
        from agent.runtime_cwd import resolve_agent_cwd

        if engine not in {"codex", "claude", "gemini"}:
            raise ValueError("Unknown subscription CLI engine")
        self.engine, self.command, self.args = engine, command or engine, list(args or [])
        self.api_key, self.base_url = api_key or engine + "-cli", base_url or "cli://" + engine
        self.cwd = str(Path(acp_cwd or resolve_agent_cwd()).resolve())
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.is_closed = False
        self._lock = threading.Lock()
        self._processes: set[subprocess.Popen] = set()

    def close(self) -> None:
        with self._lock:
            self.is_closed = True
            processes = tuple(self._processes)
        for proc in processes:
            terminate_process(proc)

    def _run(self, system: str, conversation: str, model: str, timeout: Any) -> tuple[str, dict | None]:
        from hermes_cli._subprocess_compat import windows_hide_flags

        env = subscription_env()
        if self.engine == "gemini":
            validate_gemini_auth(env)
        # Per-request files cannot race with another profile/task's configuration.
        with tempfile.TemporaryDirectory(prefix="hermes-cli-") as work:
            policy = Path(work) / "deny-native-tools.toml"
            policy.write_text(GEMINI_POLICY, encoding="utf-8")
            system_path = None
            if self.engine == "claude":
                # Only Claude's cache breakpoint needs a separate system prompt; the others cache
                # the stable stdin prefix on their own. A file keeps the text out of the process table.
                system_path = Path(work) / "system-prompt.txt"
                system_path.write_text(system, encoding="utf-8")
                prompt = conversation
            else:
                prompt = system + "\n\n" + conversation
            argv = cli_argv(self.engine, self.command, self.args, model, policy_path=str(policy),
                            system_path=str(system_path) if system_path else None)
            with self._lock:
                if self.is_closed:
                    raise RuntimeError("Subscription CLI client is closed")
                # Owned: the CLI and its children die with Hermes even on SIGKILL.
                proc = spawn_owned(argv, purpose=f"{self.engine}-cli", stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding="utf-8", cwd=self.cwd, env=env,
                                   creationflags=windows_hide_flags())
                self._processes.add(proc)
            try:
                try:
                    stdout, _stderr = proc.communicate(prompt, timeout=timeout_seconds(timeout))
                except subprocess.TimeoutExpired as exc:
                    raise TimeoutError(f"{self.engine} CLI completion timed out") from exc
                if proc.returncode:
                    # A failed turn (usage limit, model error) still prints its structured error.
                    # stderr is never surfaced: CLI diagnostics may contain device codes or OAuth
                    # URLs, and authentication happens in the user's native terminal.
                    try:
                        decode_result(self.engine, stdout)
                    except CLIReportedError:
                        raise
                    except RuntimeError:
                        pass
                    raise RuntimeError(f"{self.engine} CLI exited with code {proc.returncode}; check its native login/status.")
                return decode_result(self.engine, stdout)
            finally:
                terminate_process(proc)
                release_process_group_once_empty(proc.pid, proc=proc)
                for pipe in (proc.stdin, proc.stdout, proc.stderr):
                    if pipe is not None:
                        pipe.close()
                with self._lock:
                    self._processes.discard(proc)

    def _create(self, *, model: str = "default", messages: list[dict] | None = None, tools: list[dict] | None = None,
                tool_choice: Any = None, response_format: Any = None, stream: bool = False, timeout: Any = None, **_: Any):
        system, conversation = request_parts(messages or [], tools, tool_choice, response_format)
        text, usage = self._run(system, conversation, model, timeout)
        calls, content = extract_tool_calls_from_text(text)
        if not calls and not content:
            raise RuntimeError(f"{self.engine} CLI returned an empty completion")
        message = SimpleNamespace(content=content, tool_calls=calls, reasoning=None, reasoning_content=None, reasoning_details=None)
        result = SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls" if calls else "stop")],
                                 model=model, usage=completion_usage(self.engine, usage))
        return completion_to_stream_chunks(result) if stream else result
