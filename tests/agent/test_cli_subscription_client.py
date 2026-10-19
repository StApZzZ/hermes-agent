"""Real child-process contracts for CLI completions; no accounts or network."""

import json
from pathlib import Path
import sys

import pytest

from agent.cli_subscription_client import SubscriptionCLIClient, request_parts, request_prompt


@pytest.fixture
def client_factory(tmp_path, monkeypatch):
    from agent.cli_subscription_process import API_ENV_KEYS

    capture = tmp_path / "capture.json"
    env = {"PATH":str(Path(sys.executable).parent), "HOME":str(tmp_path), "CLI_FIXTURE_CAPTURE":str(capture)}
    env.update({key:"must-not-reach-cli" for key in API_ENV_KEYS})
    # Keep native OS essentials for child Python on Windows.
    import os
    for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
        if key in os.environ:
            env[key] = os.environ[key]
    monkeypatch.setattr("tools.environments.local.hermes_subprocess_env", lambda:dict(env))
    clients = []

    def make(engine, mode="text"):
        client = SubscriptionCLIClient(engine=engine, command=sys.executable,
            args=[str(Path(__file__).parent / "fixtures/subscription_cli.py"), engine, mode], acp_cwd=str(tmp_path))
        clients.append(client)
        return client, capture

    yield make
    for client in clients:
        client.close()


@pytest.mark.parametrize("engine", ["codex", "gemini", "claude"])
def test_real_cli_completion_preserves_unicode_and_removes_api_auth(client_factory, engine):
    client, capture = client_factory(engine)
    result = client.chat.completions.create(model="default", messages=[{"role":"user","content":"Привет & $(touch nope)"}])
    assert result.choices[0].message.content == "Привет from " + engine
    payload = json.loads(capture.read_text())
    assert "Привет & $(touch nope)" in payload["prompt"]
    assert all(payload["env"][key] is None for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"))
    assert payload["env"]["NO_BROWSER"] == "true"
    assert not any("Привет" in arg for arg in payload["argv"])


@pytest.mark.parametrize("engine", ["codex", "gemini", "claude"])
def test_hermes_tool_calls_roundtrip_through_shared_bridge(client_factory, engine):
    client, _ = client_factory(engine, "tool")
    tools = [{"type":"function","function":{"name":"terminal","parameters":{"type":"object"}}}]
    result = client.chat.completions.create(messages=[{"role":"user","content":"pwd"}], tools=tools, stream=True)
    delta = result[0].choices[0].delta
    assert delta.tool_calls[0].id == "fixture_call"
    assert delta.tool_calls[0].function.name == "terminal"
    assert json.loads(delta.tool_calls[0].function.arguments) == {"command":"pwd"}
    assert result[0].choices[0].finish_reason == "tool_calls"


@pytest.mark.parametrize("engine", ["codex", "gemini", "claude"])
@pytest.mark.parametrize("mode", ["exit", "malformed", "failed"])
def test_cli_failures_propagate_without_credentials_in_errors(client_factory, engine, mode):
    client, _ = client_factory(engine, mode)
    with pytest.raises(RuntimeError) as caught:
        client.chat.completions.create(messages=[{"role":"user","content":"test"}])
    assert "secret-device-code" not in str(caught.value)
    if mode == "failed":
        # The CLI's structured error (e.g. a subscription limit) is what the user needs to see.
        assert "usage limit" in str(caught.value)
    assert not client._processes


def test_claude_receives_the_stable_prefix_as_its_system_prompt_file(client_factory):
    client, capture = client_factory("claude")
    client.chat.completions.create(messages=[{"role":"system","content":"Hermes system Ω"}, {"role":"user","content":"task"}])
    payload = json.loads(capture.read_text())
    assert payload["system"].startswith("Hermes system Ω")
    assert "Hermes system Ω" not in payload["prompt"] and '"task"' in payload["prompt"]
    assert not any("Hermes system" in arg for arg in payload["argv"])


@pytest.mark.parametrize("engine", ["codex", "gemini"])
def test_other_clis_receive_the_stable_prefix_first_on_stdin(client_factory, engine):
    client, capture = client_factory(engine)
    client.chat.completions.create(messages=[{"role":"system","content":"Hermes system Ω"}, {"role":"user","content":"task"}])
    payload = json.loads(capture.read_text())
    assert payload["system"] is None
    assert payload["prompt"].startswith("Hermes system Ω") and '"task"' in payload["prompt"]


def test_timeout_reaps_child_and_close_refuses_new_calls(client_factory):
    client, _ = client_factory("codex", "timeout")
    with pytest.raises(TimeoutError):
        client.chat.completions.create(messages=[{"role":"user","content":"test"}], timeout=0.2)
    assert not client._processes
    client.close()
    with pytest.raises(RuntimeError, match="closed"):
        client.chat.completions.create(messages=[])


def test_concurrent_calls_own_their_processes(client_factory):
    client, _ = client_factory("claude")
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(client.chat.completions.create, messages=[{"role":"user","content":str(i)}]) for i in range(2)]
        assert all(job.result().choices[0].message.content == "Привет from claude" for job in jobs)
    assert not client._processes


def test_transcript_prefix_and_tool_results_are_not_rewritten():
    messages=[{"role":"system","content":"stable instructions"}, {"role":"user","content":"task"},
              {"role":"assistant","content":None,"tool_calls":[{"id":"call_1","function":{"name":"terminal","arguments":"{}"}}]}]
    before=request_prompt(messages, None, None, None)
    result={"role":"tool","tool_call_id":"call_1","content":"result"}
    assert request_prompt([*messages,result], None, None, None).startswith(before)
    assert '"tool_calls"' in before and '"call_1"' in before
    system_before, conversation_before = request_parts(messages, None, None, None)
    system_after, conversation_after = request_parts([*messages,result], None, None, None)
    assert system_after == system_before and conversation_after.startswith(conversation_before)


def test_multimodal_input_is_rejected_explicitly():
    with pytest.raises(ValueError, match="text only"):
        request_prompt([{"role":"user","content":[{"type":"image_url","image_url":{"url":"data:private"}}]}],None,None,None)
