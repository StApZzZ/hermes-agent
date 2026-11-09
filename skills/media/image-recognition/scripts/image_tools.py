#!/usr/bin/env python3
"""Recognise what is in an image with command-line tools; every command prints one JSON object.

  info     format, pixel size, bytes, sha256
  ocr      text via tesseract (optionally preprocessed with Pillow)
  codes    QR codes and barcodes via zbarimg
  meta     EXIF: camera, capture time, GPS (exiftool, else Pillow)
  describe a description from a signed-in subscription CLI (claude or codex)
  scan     info + codes + ocr + meta in one call
  doctor   which tools are installed, with install hints

Exit status: 0 success, 1 error, 2 a required tool is missing (the JSON says how to install it).
Stdlib only; Pillow is used when importable.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

TOOLS = {
    "tesseract": {"apt": "tesseract-ocr tesseract-ocr-rus", "brew": "tesseract tesseract-lang",
                  "winget": "UB-Mannheim.TesseractOCR"},
    "zbarimg": {"apt": "zbar-tools", "brew": "zbar", "winget": None},
    "exiftool": {"apt": "libimage-exiftool-perl", "brew": "exiftool", "winget": "OliverBetz.ExifTool"},
    "claude": {"npm": "@anthropic-ai/claude-code", "login": "claude auth login"},
    "codex": {"npm": "@openai/codex", "login": "codex login --device-auth"},
}
_MAGIC = ((b"\x89PNG\r\n\x1a\n", "PNG"), (b"\xff\xd8\xff", "JPEG"), (b"GIF8", "GIF"), (b"BM", "BMP"),
          (b"II*\x00", "TIFF"), (b"MM\x00*", "TIFF"))
_MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}
_DESCRIBE_PROMPT = ("Describe this image precisely: the scene, objects, people and their actions, "
                    "and transcribe any visible text verbatim. Answer in the user's language: {lang}.")
_CLI_TIMEOUT = 300


class MissingTool(Exception):
    def __init__(self, tool: str):
        super().__init__(tool)
        self.tool = tool


def _which(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise MissingTool(tool)
    return path


def _run(argv: list[str], *, stdin: str | None = None, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(argv, input=stdin, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, check=False,
                          stdin=None if stdin is not None else subprocess.DEVNULL)


def _pillow():
    try:
        from PIL import Image
    except ImportError:
        return None
    return Image


def _sniff_format(head: bytes) -> str | None:
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    if head[4:8] == b"ftyp":
        return "HEIF"
    return next((name for sig, name in _MAGIC if head.startswith(sig)), None)


def cmd_info(path: Path, _args) -> dict:
    data = path.read_bytes()
    info = {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "format": _sniff_format(data[:16])}
    image_mod = _pillow()
    if image_mod is not None:
        try:
            with image_mod.open(path) as im:
                info.update(format=im.format or info["format"], width=im.width, height=im.height, mode=im.mode,
                            frames=getattr(im, "n_frames", 1))
        except (OSError, ValueError, image_mod.DecompressionBombError) as exc:  # unreadable or unsupported here
            info["pillow_error"] = str(exc)
    return info


def _installed_langs(tesseract: str) -> set[str]:
    out = _run([tesseract, "--list-langs"]).stdout
    return {line.strip() for line in out.splitlines()[1:] if line.strip()}


def _ocr_langs(tesseract: str, wanted: str) -> str:
    installed = _installed_langs(tesseract)
    keep = [lang for lang in wanted.split("+") if lang in installed] if installed else wanted.split("+")
    return "+".join(keep) or "eng"


def _preprocess(path: Path, work: Path) -> Path | None:
    """Greyscale, upscale small images and stretch contrast: tesseract reads 300-dpi-like text best."""
    image_mod = _pillow()
    if image_mod is None:
        return None
    from PIL import ImageFilter, ImageOps

    with image_mod.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("L")
        if im.width < 1600:
            factor = max(2, round(1600 / max(im.width, 1)))
            im = im.resize((im.width * factor, im.height * factor), image_mod.LANCZOS)
        im = ImageOps.autocontrast(im).filter(ImageFilter.SHARPEN)
        out = work / "prepared.png"
        im.save(out)
    return out


def cmd_ocr(path: Path, args) -> dict:
    tesseract = _which("tesseract")
    langs = _ocr_langs(tesseract, args.lang)
    with tempfile.TemporaryDirectory(prefix="img-ocr-") as tmp:
        source = path
        prepared = _preprocess(path, Path(tmp)) if args.prep else None
        if prepared is not None:
            source = prepared
        proc = _run([tesseract, str(source), "stdout", "-l", langs, "--psm", str(args.psm)])
    if proc.returncode:
        raise RuntimeError("tesseract failed: " + proc.stderr.strip()[-300:])
    text = "\n".join(line.rstrip() for line in proc.stdout.splitlines()).strip()
    return {"path": str(path), "langs": langs, "preprocessed": bool(args.prep and source != path),
            "text": text, "chars": len(text)}


def _zbar_symbols(xml_text: str) -> list[dict]:
    symbols = []
    for element in ET.fromstring(xml_text).iter():
        if element.tag.rsplit("}", 1)[-1] != "symbol":
            continue
        data = next((child.text or "" for child in element if child.tag.rsplit("}", 1)[-1] == "data"), "")
        symbols.append({"type": element.get("type"), "data": data, "quality": element.get("quality")})
    return symbols


def cmd_codes(path: Path, _args) -> dict:
    proc = _run([_which("zbarimg"), "--quiet", "--xml", str(path)])
    # zbarimg exits 4 when the image holds no symbol: that is an answer, not a failure.
    if proc.returncode not in (0, 4):
        raise RuntimeError("zbarimg failed: " + proc.stderr.strip()[-300:])
    symbols = _zbar_symbols(proc.stdout) if proc.stdout.strip() else []
    return {"path": str(path), "codes": symbols, "count": len(symbols)}


_META_KEYS = ("Make", "Model", "LensModel", "DateTimeOriginal", "CreateDate", "OffsetTimeOriginal", "Software",
              "ImageWidth", "ImageHeight", "Orientation", "GPSLatitude", "GPSLongitude", "GPSAltitude",
              "Artist", "Copyright", "ImageDescription", "UserComment")


def _gps_summary(meta: dict) -> dict | None:
    lat, lon = meta.get("GPSLatitude"), meta.get("GPSLongitude")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    return {"lat": lat, "lon": lon, "map": f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=17/{lat}/{lon}"}


def _pillow_exif(path: Path) -> dict:
    image_mod = _pillow()
    if image_mod is None:
        raise MissingTool("exiftool")
    from PIL.ExifTags import GPSTAGS, TAGS

    with image_mod.open(path) as im:
        exif = im.getexif()
        meta = {TAGS.get(k, str(k)): v for k, v in exif.items()}
        meta.update({TAGS.get(k, str(k)): v for k, v in exif.get_ifd(0x8769).items()})
        gps = {GPSTAGS.get(k, str(k)): v for k, v in exif.get_ifd(0x8825).items()}

    def degrees(dms, ref):
        value = float(dms[0]) + float(dms[1]) / 60 + float(dms[2]) / 3600
        return -value if ref in ("S", "W") else value

    if "GPSLatitude" in gps and "GPSLongitude" in gps:
        meta["GPSLatitude"] = degrees(gps["GPSLatitude"], gps.get("GPSLatitudeRef"))
        meta["GPSLongitude"] = degrees(gps["GPSLongitude"], gps.get("GPSLongitudeRef"))
    return {k: (v if isinstance(v, (int, float, str)) else str(v)) for k, v in meta.items()}


def cmd_meta(path: Path, _args) -> dict:
    if shutil.which("exiftool"):
        proc = _run([shutil.which("exiftool"), "-json", "-n", str(path)])
        if proc.returncode:
            raise RuntimeError("exiftool failed: " + proc.stderr.strip()[-300:])
        meta, source = json.loads(proc.stdout)[0], "exiftool"
    else:
        meta, source = _pillow_exif(path), "pillow"
    picked = {k: meta[k] for k in _META_KEYS if meta.get(k) not in (None, "")}
    return {"path": str(path), "source": source, "meta": picked, "gps": _gps_summary(meta),
            "has_exif": bool(picked)}


def _image_mime(path: Path) -> str:
    fmt = _sniff_format(path.read_bytes()[:16])
    if fmt not in _MIME:
        raise RuntimeError(f"describe takes PNG, JPEG, GIF or WebP; convert {fmt or 'this file'} to PNG first.")
    return _MIME[fmt]


def _describe_claude(path: Path, prompt: str) -> str:
    message = {"type": "user", "message": {"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": _image_mime(path),
                                     "data": base64.b64encode(path.read_bytes()).decode("ascii")}},
        {"type": "text", "text": prompt}]}}
    # No tools, no MCP, subscription login only: the CLI only looks at the picture.
    argv = [_which("claude"), "--print", "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose", "--no-session-persistence", "--tools", "", "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}', "--disable-slash-commands",
            "--settings", '{"forceLoginMethod":"claudeai"}']
    proc = _run(argv, stdin=json.dumps(message) + "\n", timeout=_CLI_TIMEOUT)
    results = [row for row in map(_json_or_none, proc.stdout.splitlines()) if row and row.get("type") == "result"]
    if not results or results[-1].get("is_error"):
        reason = results[-1].get("result") if results else f"exit code {proc.returncode}"
        raise RuntimeError(f"claude could not describe the image: {reason}. Check `claude auth status`.")
    return str(results[-1].get("result") or "").strip()


def _describe_codex(path: Path, prompt: str) -> str:
    _image_mime(path)
    argv = [_which("codex"), "exec", "--json", "--ephemeral", "--sandbox", "read-only", "--skip-git-repo-check",
            "-c", 'forced_login_method="chatgpt"', "-c", 'web_search="disabled"', "-c", "features.shell_tool=false",
            "--image=" + str(path), "-"]
    proc = _run(argv, stdin=prompt, timeout=_CLI_TIMEOUT)
    rows = [row for row in map(_json_or_none, proc.stdout.splitlines()) if row]
    texts = [row["item"].get("text", "") for row in rows
             if row.get("type") == "item.completed" and (row.get("item") or {}).get("type") == "agent_message"]
    if proc.returncode or not texts:
        failed = [row for row in rows if row.get("type") in ("error", "turn.failed")]
        reason = json.dumps(failed[-1])[:300] if failed else f"exit code {proc.returncode}"
        raise RuntimeError(f"codex could not describe the image: {reason}. Check `codex login status`.")
    return "\n".join(texts).strip()


def _json_or_none(line: str):
    try:
        row = json.loads(line)
    except ValueError:
        return None
    return row if isinstance(row, dict) else None


_DESCRIBERS = {"claude": _describe_claude, "codex": _describe_codex}


def cmd_describe(path: Path, args) -> dict:
    engines = [args.engine] if args.engine != "auto" else [e for e in _DESCRIBERS if shutil.which(e)]
    if not engines:
        raise MissingTool("claude")
    prompt = args.prompt or _DESCRIBE_PROMPT.format(lang=args.lang_name)
    errors = []
    for engine in engines:
        try:
            return {"path": str(path), "engine": engine, "text": _DESCRIBERS[engine](path, prompt)}
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            errors.append(str(exc))
    raise RuntimeError(" | ".join(errors))


def cmd_scan(path: Path, args) -> dict:
    report: dict = {"path": str(path)}
    for name, fn in (("info", cmd_info), ("codes", cmd_codes), ("ocr", cmd_ocr), ("meta", cmd_meta)):
        try:
            result = fn(path, args)
            result.pop("path", None)
            report[name] = result
        except MissingTool as exc:
            report[name] = {"missing": exc.tool, "install": TOOLS[exc.tool]}
        except (RuntimeError, OSError, ValueError) as exc:
            report[name] = {"error": str(exc)}
    return report


def cmd_doctor(_path, _args) -> dict:
    tools = {}
    for tool, hints in TOOLS.items():
        found = shutil.which(tool)
        tools[tool] = {"installed": bool(found), "path": found, **({} if found else {"install": hints})}
    if tools["tesseract"]["installed"]:
        tools["tesseract"]["langs"] = sorted(_installed_langs(tools["tesseract"]["path"]))
    return {"tools": tools, "pillow": _pillow() is not None}


COMMANDS = {"info": cmd_info, "ocr": cmd_ocr, "codes": cmd_codes, "meta": cmd_meta, "describe": cmd_describe,
            "scan": cmd_scan, "doctor": cmd_doctor}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("image", nargs="?", help="image file (not needed for doctor)")
    parser.add_argument("--lang", default="rus+eng", help="tesseract languages, e.g. rus+eng (missing ones are dropped)")
    parser.add_argument("--psm", type=int, default=3, help="tesseract page segmentation mode (6 = one text block)")
    parser.add_argument("--prep", action="store_true", help="greyscale, upscale and sharpen before OCR")
    parser.add_argument("--engine", choices=("auto", *_DESCRIBERS), default="auto")
    parser.add_argument("--prompt", help="question to ask about the image (describe)")
    parser.add_argument("--lang-name", default="Russian", help="answer language for describe")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = None
    if args.command != "doctor":
        if not args.image:
            print(json.dumps({"error": f"{args.command} needs an image path"}))
            return 1
        path = Path(os.path.expanduser(args.image))
        if not path.is_file():
            print(json.dumps({"error": f"no such file: {path}"}))
            return 1
    try:
        result = COMMANDS[args.command](path, args)
    except MissingTool as exc:
        print(json.dumps({"missing": exc.tool, "install": TOOLS[exc.tool]}, ensure_ascii=False))
        return 2
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
