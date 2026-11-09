"""/api/vision/* — the dashboard's Images page, through the real app with a temp HERMES_HOME."""

import base64
import os
from pathlib import Path
import sys

import pytest
from starlette.testclient import TestClient

from hermes_cli import web_server

_PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@pytest.fixture
def client(monkeypatch, tmp_path):
    home = tmp_path / "hermes-home"
    home.mkdir()
    (home / "config.yaml").write_text("model:\n  provider: claude-cli\n  default: default\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    prev = {k: getattr(web_server.app.state, k, None) for k in ("auth_required", "bound_host")}
    web_server.app.state.auth_required = False
    web_server.app.state.bound_host = None
    test_client = TestClient(web_server.app)
    test_client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    yield test_client, home
    test_client.close()
    for key, value in prev.items():
        if value is None:
            if hasattr(web_server.app.state, key):
                delattr(web_server.app.state, key)
        else:
            setattr(web_server.app.state, key, value)


def _upload(test_client) -> str:
    response = test_client.post("/api/chat/image-upload", json={"data_url": f"data:image/png;base64,{_PNG}",
                                                                "filename": "probe.png"})
    assert response.status_code == 200, response.text
    return response.json()["path"]


def test_saving_a_mode_changes_how_the_next_image_is_routed(client):
    test_client, home = client
    before = test_client.get("/api/vision/settings").json()
    assert before["image_input_mode"] == "auto" and before["main"] == {"provider": "claude-cli", "model": "default"}

    saved = test_client.put("/api/vision/settings", json={"image_input_mode": "text"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["image_input_mode"] == "text" and saved.json()["route"] == "text"
    assert "image_input_mode: text" in (home / "config.yaml").read_text(encoding="utf-8")
    assert test_client.put("/api/vision/settings", json={"image_input_mode": "sideways"}).status_code == 400


def test_analyze_only_reads_images_staged_by_the_dashboard(client):
    test_client, home = client
    secret = home / "memories.md"
    secret.write_text("private", encoding="utf-8")
    response = test_client.post("/api/vision/analyze", json={"path": str(secret), "actions": ["ocr"]})
    assert response.status_code == 400
    traversal = str(Path(_upload(test_client)).parent / ".." / ".." / "memories.md")
    assert test_client.post("/api/vision/analyze", json={"path": traversal, "actions": ["ocr"]}).status_code == 400


@pytest.mark.platforms("posix")  # the fake recognisers are shebang scripts
def test_analyze_runs_the_recognisers_and_vision_analyze(client, monkeypatch, tmp_path):
    test_client, _home = client
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {
        "tesseract": 'if sys.argv[1] == "--list-langs":\n    print("langs\\neng")\nelse:\n    print("TOTAL 42")',
        "zbarimg": "sys.exit(4)",
    }.items():
        tool = bin_dir / name
        tool.write_text(f"#!{sys.executable}\nimport sys\n{body}\n", encoding="utf-8")
        tool.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]))

    seen = {}

    async def fake_vision(image_url, user_prompt, **_kw):
        seen.update(image_url=image_url, prompt=user_prompt)
        return '{"success": true, "analysis": "A single pixel."}'

    monkeypatch.setattr("tools.vision_tools.vision_analyze_tool", fake_vision)
    path = _upload(test_client)
    response = test_client.post("/api/vision/analyze", json={
        "path": path, "actions": ["describe", "ocr", "codes"], "prompt": "What is it?", "lang": "eng"})
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert results["describe"] == {"text": "A single pixel."}
    assert seen == {"image_url": path, "prompt": "What is it?"}
    assert results["ocr"]["text"] == "TOTAL 42" and results["ocr"]["langs"] == "eng"
    assert results["codes"]["count"] == 0
    assert base64.b64decode(_PNG) == Path(path).read_bytes()


def test_tools_reports_the_skill_doctor(client):
    test_client, _home = client
    tools = test_client.get("/api/vision/tools").json()
    assert set(tools["tools"]) == {"tesseract", "zbarimg", "exiftool", "claude", "codex"}
