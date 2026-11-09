"""Images inside a subscription CLI request.

Claude Code takes them as content blocks of a stream-json user message; Codex takes them as
``--image`` files. Gemini CLI can only read files through its native tools, which Hermes denies,
so it stays text-only. Each image is replaced in the transcript by a numbered marker that is
identical on every call, so a growing conversation keeps its cached prefix.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
from pathlib import Path

IMAGE_ENGINES = frozenset({"claude", "codex"})
_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
_IMAGE_BLOCK_TYPES = frozenset({"image_url", "input_image", "image"})


@dataclass(frozen=True)
class CLIImage:
    mime: str
    data: bytes


def _block_url(block: dict) -> str:
    if block.get("type") == "image":  # Anthropic-style source block
        source = block.get("source") or {}
        if source.get("type") == "base64":
            return f"data:{source.get('media_type', '')};base64,{source.get('data', '')}"
        return str(source.get("url") or "")
    ref = block.get("image_url")
    return str(ref.get("url") or "") if isinstance(ref, dict) else str(ref or "")


def _decode(block: dict) -> CLIImage:
    url = _block_url(block)
    header, sep, payload = url.partition(",")
    if not (url.startswith("data:") and sep and header.endswith(";base64")):
        raise ValueError("Subscription CLI providers take inline images only; remote image URLs are not fetched.")
    mime = header[len("data:"):-len(";base64")].lower()
    if mime not in _EXTENSIONS:
        raise ValueError(f"Subscription CLI providers take PNG, JPEG, GIF or WebP images, not {mime or 'unknown'}.")
    try:
        return CLIImage(mime, base64.b64decode(payload, validate=True))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("An attached image is not valid base64 data.") from exc


def detach_images(messages: list[dict]) -> tuple[list[dict], list[CLIImage]]:
    """``(messages with numbered image markers, images in order)``; text-only messages pass through."""
    images: list[CLIImage] = []
    out: list[dict] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list) or not any(
            isinstance(b, dict) and b.get("type") in _IMAGE_BLOCK_TYPES for b in content
        ):
            out.append(message)
            continue
        blocks = []
        for block in content:
            if isinstance(block, dict) and block.get("type") in _IMAGE_BLOCK_TYPES:
                images.append(_decode(block))
                block = {"type": "text", "text": _marker(len(images))}
            blocks.append(block)
        out.append({**message, "content": blocks})
    return out, images


def _marker(index: int) -> str:
    return f"[attached image #{index}]"


def claude_stream_input(conversation: str, images: list[CLIImage]) -> str:
    """One stream-json user message: the transcript text with each image right after its marker.
    Earlier images keep their place when the conversation grows, so the cached prefix holds."""
    content: list[dict] = []
    rest = conversation
    for index, image in enumerate(images, start=1):
        head, marker, rest = rest.partition(_marker(index))
        content.append({"type": "text", "text": head + marker})
        content.append({"type": "image", "source": {"type": "base64", "media_type": image.mime,
                                                    "data": base64.b64encode(image.data).decode("ascii")}})
    if rest:
        content.append({"type": "text", "text": rest})
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}}, ensure_ascii=False) + "\n"


def write_image_files(work: Path, images: list[CLIImage]) -> list[str]:
    """Images as files in the per-request directory, named by marker number."""
    paths = []
    for index, image in enumerate(images, start=1):
        path = work / f"image-{index}{_EXTENSIONS[image.mime]}"
        path.write_bytes(image.data)
        paths.append(str(path))
    return paths
