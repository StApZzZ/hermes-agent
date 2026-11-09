"""skills/media/image-recognition/scripts/image_tools.py against fake CLI tools on PATH (no network, no models)."""

import base64
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import zlib

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "skills" / "media" / "image-recognition" / "scripts" / "image_tools.py"
pytestmark = pytest.mark.platforms("posix")  # the fake tools are shebang scripts


def _png(width=4, height=3):
    raw = b"".join(b"\x00" + b"\xdc\x00\x00" * width for _ in range(height))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@pytest.fixture
def env(tmp_path):
    """A PATH holding only the interpreter's dir plus the fake tools a test installs."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    image = tmp_path / "photo.png"
    image.write_bytes(_png())

    def install(name, body):
        tool = bin_dir / name
        tool.write_text(f"#!{sys.executable}\nimport json, sys\n{body}\n", encoding="utf-8")
        tool.chmod(0o755)

    def run(*args):
        child_env = {"PATH": os.pathsep.join([str(bin_dir), str(Path(sys.executable).parent)]),
                     "HOME": str(tmp_path), "LANG": "C.UTF-8"}
        proc = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                              encoding="utf-8", env=child_env, timeout=60, check=False)
        return proc.returncode, json.loads(proc.stdout)

    return install, run, image, tmp_path


def test_info_reports_format_and_digest_without_any_tool(env):
    _install, run, image, _ = env
    code, out = run("info", str(image))
    assert code == 0
    assert out["format"] == "PNG" and out["bytes"] == image.stat().st_size and len(out["sha256"]) == 64


def test_ocr_keeps_only_installed_languages_and_returns_the_text(env):
    install, run, image, tmp = env
    install("tesseract", f"""
if sys.argv[1] == "--list-langs":
    print("List of available languages (2):\\neng\\nrus")
    sys.exit(0)
open({str(tmp / 'argv.json')!r}, "w").write(json.dumps(sys.argv[1:]))
print("Итого: 1 250,00 ₽  \\n")""")
    code, out = run("ocr", str(image), "--lang", "rus+eng+deu", "--psm", "6")
    assert code == 0
    assert out["text"] == "Итого: 1 250,00 ₽" and out["langs"] == "rus+eng"
    argv = json.loads((tmp / "argv.json").read_text())
    assert argv[argv.index("-l") + 1] == "rus+eng" and argv[argv.index("--psm") + 1] == "6"


def test_codes_decodes_every_symbol_and_treats_no_symbol_as_an_empty_answer(env):
    install, run, image, _ = env
    xml = ('<barcodes xmlns="http://zbar.sourceforge.net/2008/barcode"><source href="x"><index num="0">'
           '<symbol type="QR-Code" quality="1"><data><![CDATA[https://example.com/pay?id=7]]></data></symbol>'
           '<symbol type="EAN-13" quality="2"><data><![CDATA[4601234567893]]></data></symbol>'
           '</index></source></barcodes>')
    install("zbarimg", f"print({xml!r})")
    code, out = run("codes", str(image))
    assert code == 0
    assert out["codes"] == [{"type": "QR-Code", "data": "https://example.com/pay?id=7", "quality": "1"},
                            {"type": "EAN-13", "data": "4601234567893", "quality": "2"}]
    install("zbarimg", "sys.exit(4)")
    assert run("codes", str(image)) == (0, {"path": str(image), "codes": [], "count": 0})


def test_meta_picks_camera_fields_and_links_gps(env):
    install, run, image, _ = env
    install("exiftool", 'print(json.dumps([{"Make": "Apple", "Model": "iPhone 15", "GPSLatitude": 55.7558,'
                        ' "GPSLongitude": 37.6173, "ThumbnailImage": "(binary)"}]))')
    code, out = run("meta", str(image))
    assert code == 0 and out["source"] == "exiftool"
    assert out["meta"]["Model"] == "iPhone 15" and "ThumbnailImage" not in out["meta"]
    assert out["gps"]["lat"] == 55.7558 and "mlat=55.7558" in out["gps"]["map"]


def test_missing_tool_exits_2_with_an_install_hint(env):
    _install, run, image, _ = env
    code, out = run("ocr", str(image))
    assert code == 2 and out["missing"] == "tesseract" and "tesseract-ocr" in out["install"]["apt"]


def test_scan_reports_missing_tools_without_failing(env):
    install, run, image, _ = env
    install("zbarimg", "sys.exit(4)")
    code, out = run("scan", str(image))
    assert code == 0
    assert out["info"]["format"] == "PNG" and out["codes"]["count"] == 0
    assert out["ocr"]["missing"] == "tesseract"


def test_describe_sends_the_image_to_claude_as_an_image_block_with_tools_off(env):
    install, run, image, tmp = env
    install("claude", f"""
message = json.loads(sys.stdin.readline())
open({str(tmp / 'claude.json')!r}, "w").write(json.dumps({{"argv": sys.argv[1:], "message": message}}))
print(json.dumps({{"type": "system"}}))
print(json.dumps({{"type": "result", "is_error": False, "result": "A red square."}}))""")
    code, out = run("describe", str(image), "--prompt", "What colour?")
    assert code == 0 and out == {"path": str(image), "engine": "claude", "text": "A red square."}
    seen = json.loads((tmp / "claude.json").read_text())
    blocks = seen["message"]["message"]["content"]
    assert base64.b64decode(blocks[0]["source"]["data"]) == image.read_bytes()
    assert blocks[0]["source"]["media_type"] == "image/png" and blocks[1]["text"] == "What colour?"
    assert seen["argv"][seen["argv"].index("--tools") + 1] == ""


def test_describe_falls_back_to_codex_when_claude_fails(env):
    install, run, image, tmp = env
    install("claude", 'print(json.dumps({"type": "result", "is_error": True, "result": "usage limit"}))\nsys.exit(1)')
    install("codex", f"""
open({str(tmp / 'codex.json')!r}, "w").write(json.dumps(sys.argv[1:]))
print(json.dumps({{"type": "item.completed", "item": {{"type": "agent_message", "text": "Красный квадрат."}}}}))
print(json.dumps({{"type": "turn.completed"}}))""")
    code, out = run("describe", str(image))
    assert code == 0 and out["engine"] == "codex" and out["text"] == "Красный квадрат."
    assert "--image=" + str(image) in json.loads((tmp / "codex.json").read_text())
