"""One side of ``hermes sync``: read and change in-scope files under an installation root.

The same code runs locally and as the remote peer (``hermes sync agent``, JSON over SSH stdio), so
both sides enforce the scope allowlist and the optimistic check that a file still has the hash the
plan was made from. Writes reuse each file's own writer: ``config.yaml`` through the config write
seam (comments and formatting survive), memory stores under the memory tool's lock.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any, Callable

from hermes_cli.home_sync_scope import KIND_ENTRIES, KIND_FILE, KIND_YAML, file_hash, kind_of, scan

PROTOCOL = 1
REPLY_MARKER = "HERMES-SYNC-REPLY "  # login shells may print banners on stdout; the reply is marked

# Op actions and the scope kind each one may touch.
_ACTION_KINDS = {"write": KIND_FILE, "yaml": KIND_YAML, "entries": KIND_ENTRIES}


class SyncError(Exception):
    """A sync step that cannot proceed; the message is shown to the user as is."""


class StaleError(SyncError):
    """A file changed between planning and applying; nothing was written, the next run retries."""


def _checked(root: Path, rel: str) -> Path:
    if kind_of(rel) is None:
        raise SyncError(f"path outside the sync scope: {rel!r}")
    return root / rel


def current_hash(root: Path, rel: str) -> str | None:
    path = _checked(root, rel)
    return file_hash(path) if path.is_file() and not path.is_symlink() else None


def read_texts(root: Path, rels: list[str]) -> dict[str, str | None]:
    """Base64 of each file (None when absent)."""
    out: dict[str, str | None] = {}
    for rel in rels:
        path = _checked(root, rel)
        out[rel] = base64.b64encode(path.read_bytes()).decode("ascii") if path.is_file() else None
    return out


def _mkparent(path: Path) -> None:
    from hermes_constants import mkdir_under_hermes_home
    mkdir_under_hermes_home(path.parent)


def _write_file(path: Path, data: str) -> None:
    from utils import atomic_write_bytes
    _mkparent(path)
    atomic_write_bytes(path, base64.b64decode(data), tmp_prefix=".sync_")


def _write_yaml(path: Path, data: dict) -> None:
    _mkparent(path)
    if path.name == "config.yaml":
        from hermes_cli.config import atomic_config_replace
        atomic_config_replace(path, data)
    else:
        from utils import atomic_yaml_write
        atomic_yaml_write(path, data, sort_keys=False)


def _write_entries(path: Path, data: list[str]) -> None:
    from tools.memory_tool_store import MemoryStore
    _mkparent(path)
    with MemoryStore._file_lock(path):
        MemoryStore._write_file(path, [str(e) for e in data])


def _delete(path: Path, _data: Any = None) -> None:
    path.unlink(missing_ok=True)


_WRITERS: dict[str, Callable[[Path, Any], None]] = {
    "write": _write_file, "yaml": _write_yaml, "entries": _write_entries, "delete": _delete}


def _op_order(op: dict) -> tuple[int, str]:
    # A profile's config.yaml gives it identity, which its memory dir needs before it is created.
    return (0 if op["path"].endswith("config.yaml") else 1, op["path"])


def _validate(root: Path, ops: list[dict]) -> None:
    stale = []
    for op in ops:
        action, rel = op.get("action"), op.get("path")
        if action not in _WRITERS:
            raise SyncError(f"unknown sync action {action!r}")
        kind = kind_of(rel)
        if kind is None or (action != "delete" and _ACTION_KINDS[action] != kind):
            raise SyncError(f"{action} is not allowed on {rel!r}")
        if current_hash(root, rel) != op.get("expect"):
            stale.append(rel)
    if stale:
        raise StaleError("changed while syncing, will retry next run: " + ", ".join(sorted(stale)))


def _ensure_profiles(root: Path, ops: list[dict]) -> None:
    """A profile that exists on the other machine only is created the regular way first; one that
    was deleted here is never brought back by a sync."""
    from hermes_constants import named_profile_is_deleted

    names = {op["path"].split("/")[1] for op in ops
             if op["action"] != "delete" and op["path"].startswith("profiles/")}
    for name in sorted(names):
        home = root / "profiles" / name
        if named_profile_is_deleted(home):
            raise SyncError(f"profile '{name}' was deleted on this machine; delete it on the other one "
                            f"too, or recreate it here (hermes profile create {name})")
        if not home.exists():
            from hermes_cli.profiles import create_profile
            create_profile(name, no_alias=True)


def apply_ops(root: Path, ops: list[dict]) -> dict[str, str | None]:
    """Apply every op, or none of them if any target moved since it was read. Returns new hashes."""
    _validate(root, ops)
    _ensure_profiles(root, ops)
    for op in sorted(ops, key=_op_order):
        _WRITERS[op["action"]](root / op["path"], op.get("data"))
    return {op["path"]: current_hash(root, op["path"]) for op in ops}


def handle_request(root: Path, request: dict) -> dict:
    op = request.get("op")
    if op == "hello":
        return {"protocol": PROTOCOL}
    if op == "scan":
        return {"files": scan(root)}
    if op == "read":
        return {"files": read_texts(root, list(request.get("paths") or []))}
    if op == "apply":
        return {"hashes": apply_ops(root, list(request.get("ops") or []))}
    raise SyncError(f"unknown request {op!r}")


def serve_agent(root: Path, stdin=None, stdout=None) -> int:
    """``hermes sync agent``: one JSON request on stdin, one marked JSON reply on stdout."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    try:
        reply = {"ok": True, **handle_request(root, json.loads(stdin.read() or "{}"))}
    except StaleError as exc:
        reply = {"ok": False, "stale": True, "error": str(exc)}
    except (SyncError, OSError, ValueError) as exc:
        reply = {"ok": False, "error": str(exc)}
    stdout.write(REPLY_MARKER + json.dumps(reply, default=str) + "\n")
    stdout.flush()
    return 0 if reply["ok"] else 1
