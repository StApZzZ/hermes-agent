"""One ``hermes sync`` run against one peer: scan both sides, plan, build ops, apply, record the base.

The base (``<root>/sync/<host>/state.json``) holds, per path, the hash each side had after the last
successful run and, for YAML documents and memory stores, the merged content, so the next run can
tell "changed here" from "changed there". Hashes are recorded from what was read or written, never
from a rescan, so an edit that lands during a run is never mistaken for already-synced.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from hermes_cli.home_sync_merge import (
    DELETE_LOCAL, DELETE_REMOTE, DROP, MACHINE_LOCAL_CONFIG_KEYS, MACHINE_LOCAL_SPACES_KEYS, MERGE,
    MISSING, PULL, PUSH, SAME, graft, merge3, merge_entries, parse_patterns, plan_path, split_local)
from hermes_cli.home_sync_scope import KIND_ENTRIES, KIND_FILE, KIND_YAML, kind_of, scan
from hermes_cli.home_sync_store import SyncError, apply_ops

STATE_VERSION = 1


class Peer(Protocol):
    def call(self, request: dict) -> dict: ...


@dataclass
class Report:
    host: str
    dry_run: bool = False
    pushed: list[str] = field(default_factory=list)
    pulled: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    deleted_local: list[str] = field(default_factory=list)
    deleted_remote: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    conflict_dir: str = ""

    @property
    def changed(self) -> bool:
        return any((self.pushed, self.pulled, self.merged, self.deleted_local, self.deleted_remote))


@dataclass
class _Path:
    """Everything known about one path while building its ops."""
    rel: str
    action: str
    local: dict | None
    remote: dict | None
    base: dict
    local_bytes: bytes | None = None
    remote_bytes: bytes | None = None

    @property
    def local_newer(self) -> bool:
        return (self.local or {}).get("mtime", 0) >= (self.remote or {}).get("mtime", 0)


@dataclass
class _Ops:
    local: list[dict] = field(default_factory=list)
    remote: list[dict] = field(default_factory=list)
    docs: dict[str, Any] = field(default_factory=dict)  # merged base content per path
    conflict_copies: dict[str, bytes] = field(default_factory=dict)
    skipped: set[str] = field(default_factory=set)  # left out of this run; their base stays as it was


def state_path(root: Path, host: str) -> Path:
    return root / "sync" / host / "state.json"


def load_state(root: Path, host: str) -> dict:
    try:
        data = json.loads(state_path(root, host).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"version": STATE_VERSION, "paths": {}}
    return data if data.get("version") == STATE_VERSION else {"version": STATE_VERSION, "paths": {}}


def _save_state(root: Path, host: str, paths: dict, synced_at: float) -> None:
    from utils import atomic_write_text
    target = state_path(root, host)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": STATE_VERSION, "synced_at": synced_at, "paths": paths}
    atomic_write_text(target, json.dumps(payload, ensure_ascii=False, indent=1, default=str), tmp_prefix=".state_")


def _hash(side: dict, rel: str) -> str | None:
    return (side.get(rel) or {}).get("hash")


def _patterns_for(rel: str, extra_local_keys) -> tuple:
    if rel.endswith("config.yaml"):
        return parse_patterns([*MACHINE_LOCAL_CONFIG_KEYS, *extra_local_keys])
    if rel == "spaces.yaml":
        return parse_patterns(MACHINE_LOCAL_SPACES_KEYS)
    return ()


def _load_yaml(rel: str, data: bytes | None) -> dict | None:
    if data is None:
        return None
    import hermes_yaml
    try:
        doc = hermes_yaml.safe_load(data.decode("utf-8-sig")) or {}
    except (ValueError, hermes_yaml.YAMLError) as exc:
        raise SyncError(f"{rel}: unreadable YAML ({exc}); fix it, then sync again") from exc
    if not isinstance(doc, dict):
        raise SyncError(f"{rel}: not a YAML mapping; fix it, then sync again")
    return doc


def _entries(data: bytes | None) -> list[str] | None:
    if data is None:
        return None
    from tools.memory_tool_store import MemoryStore
    return MemoryStore._parse_entries(data.decode("utf-8-sig"))


def _op(action: str, p: _Path, side: str, data: Any = None) -> dict:
    expect = (p.local if side == "local" else p.remote) or {}
    return {"action": action, "path": p.rel, "expect": expect.get("hash"), "data": data}


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _file_ops(p: _Path, ops: _Ops, report: Report, _extra) -> None:
    if p.action == PUSH:
        ops.remote.append(_op("write", p, "remote", _b64(p.local_bytes)))
        report.pushed.append(p.rel)
    elif p.action == PULL:
        ops.local.append(_op("write", p, "local", _b64(p.remote_bytes)))
        report.pulled.append(p.rel)
    elif p.local_newer:
        ops.remote.append(_op("write", p, "remote", _b64(p.local_bytes)))
        ops.conflict_copies[f"remote/{p.rel}"] = p.remote_bytes
        report.pushed.append(p.rel)
        report.conflicts.append(f"{p.rel}: edited on both sides, kept this machine's (newer) copy")
    else:
        ops.local.append(_op("write", p, "local", _b64(p.remote_bytes)))
        ops.conflict_copies[f"local/{p.rel}"] = p.local_bytes
        report.pulled.append(p.rel)
        report.conflicts.append(f"{p.rel}: edited on both sides, kept {report.host}'s (newer) copy")


def _emit(p: _Path, ops: _Ops, report: Report, action: str, old_local, new_local, old_remote, new_remote) -> None:
    if new_local != old_local:
        ops.local.append(_op(action, p, "local", new_local))
    if new_remote != old_remote:
        ops.remote.append(_op(action, p, "remote", new_remote))
    wrote_local, wrote_remote = new_local != old_local, new_remote != old_remote
    if wrote_local and wrote_remote:
        report.merged.append(p.rel)
    elif wrote_local:
        report.pulled.append(p.rel)
    elif wrote_remote:
        report.pushed.append(p.rel)


def _yaml_ops(p: _Path, ops: _Ops, report: Report, extra_local_keys) -> None:
    patterns = _patterns_for(p.rel, extra_local_keys)
    local_doc, remote_doc = _load_yaml(p.rel, p.local_bytes), _load_yaml(p.rel, p.remote_bytes)
    local_shared, local_own = split_local(local_doc or {}, patterns)
    remote_shared, remote_own = split_local(remote_doc or {}, patterns)
    if local_doc is None or remote_doc is None:
        merged = remote_shared if local_doc is None else local_shared  # new on one side: copy it
    else:
        conflicts: list[str] = []
        merged = merge3(p.base.get("doc", MISSING), local_shared, remote_shared, ours_wins=p.local_newer, conflicts=conflicts)
        winner = "this machine" if p.local_newer else report.host
        report.conflicts += [f"{p.rel}: {key} set on both sides, kept {winner}'s value" for key in conflicts]
    ops.docs[p.rel] = merged
    _emit(p, ops, report, "yaml", local_doc, graft(merged, local_own), remote_doc, graft(merged, remote_own))


def _entries_ops(p: _Path, ops: _Ops, report: Report, _extra) -> None:
    local, remote = _entries(p.local_bytes), _entries(p.remote_bytes)
    if local is None or remote is None:
        merged = remote if local is None else local
    else:
        merged = merge_entries(p.base.get("doc"), local, remote)
    ops.docs[p.rel] = merged
    _emit(p, ops, report, "entries", local, merged, remote, merged)


_BUILDERS = {KIND_FILE: _file_ops, KIND_YAML: _yaml_ops, KIND_ENTRIES: _entries_ops}


def _needs_content(rel: str, action: str) -> tuple[bool, bool]:
    """(local, remote) contents an action needs."""
    if action in (DELETE_LOCAL, DELETE_REMOTE):
        return False, False
    if kind_of(rel) != KIND_FILE or action == MERGE:
        return True, True
    return action == PUSH, action == PULL


def _plan(root: Path, local: dict, remote: dict, base: dict) -> list[_Path]:
    planned = []
    for rel in sorted(set(local) | set(remote) | set(base)):
        if kind_of(rel) is None:
            continue
        entry = base.get(rel, {})
        action = plan_path(entry.get("l"), entry.get("r"), _hash(local, rel), _hash(remote, rel))
        if action not in (SAME, DROP):
            planned.append(_Path(rel, action, local.get(rel), remote.get(rel), entry))
    return planned


def _load_contents(root: Path, peer: Peer, planned: list[_Path]) -> None:
    wanted = []
    for p in planned:
        need_local, need_remote = _needs_content(p.rel, p.action)
        if need_local and p.local:
            p.local_bytes = (root / p.rel).read_bytes()
        if need_remote and p.remote:
            wanted.append(p.rel)
    if wanted:
        files = peer.call({"op": "read", "paths": wanted})["files"]
        for p in planned:
            if files.get(p.rel) is not None:
                p.remote_bytes = base64.b64decode(files[p.rel])


def _build(planned: list[_Path], report: Report, extra_local_keys) -> _Ops:
    ops = _Ops()
    for p in planned:
        if p.action == DELETE_LOCAL:
            ops.local.append(_op("delete", p, "local"))
            report.deleted_local.append(p.rel)
        elif p.action == DELETE_REMOTE:
            ops.remote.append(_op("delete", p, "remote"))
            report.deleted_remote.append(p.rel)
        else:
            try:
                _BUILDERS[kind_of(p.rel)](p, ops, report, extra_local_keys)
            except SyncError as exc:
                ops.skipped.add(p.rel)
                report.skipped.append(str(exc))
    return ops


def _base_doc(root: Path, rel: str, extra_local_keys) -> Any:
    data = (root / rel).read_bytes()
    if kind_of(rel) == KIND_YAML:
        return split_local(_load_yaml(rel, data) or {}, _patterns_for(rel, extra_local_keys))[0]
    return _entries(data)


def _new_base(root: Path, local: dict, remote: dict, base: dict, ops: _Ops,
              hashes: tuple[dict, dict], extra_local_keys) -> dict:
    local_hashes, remote_hashes = hashes
    out = {}
    for rel in sorted(set(local) | set(remote) | set(local_hashes) | set(remote_hashes)):
        if rel in ops.skipped:
            if rel in base:
                out[rel] = base[rel]
            continue
        lh = local_hashes[rel] if rel in local_hashes else _hash(local, rel)
        rh = remote_hashes[rel] if rel in remote_hashes else _hash(remote, rel)
        if lh is None or rh is None or kind_of(rel) is None:
            continue
        entry: dict = {"l": lh, "r": rh}
        if rel in ops.docs:
            entry["doc"] = ops.docs[rel]
        elif "doc" in base.get(rel, {}) and base[rel].get("l") == lh:
            entry["doc"] = base[rel]["doc"]
        elif kind_of(rel) in (KIND_YAML, KIND_ENTRIES):
            entry["doc"] = _base_doc(root, rel, extra_local_keys)
        out[rel] = entry
    return out


def _keep_conflict_copies(root: Path, host: str, copies: dict[str, bytes], stamp: str) -> str:
    if not copies:
        return ""
    target = root / "sync" / host / "conflicts" / stamp
    for rel, data in copies.items():
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        (target / rel).write_bytes(data)
    return str(target)


def sync_with(peer: Peer, host: str, root: Path, *, dry_run: bool = False,
              extra_local_keys=(), now: float | None = None) -> Report:
    """Run one bidirectional sync between ``root`` and ``peer``; returns what changed."""
    report = Report(host=host, dry_run=dry_run)
    state = load_state(root, host)
    local, remote = scan(root), peer.call({"op": "scan"})["files"]
    planned = _plan(root, local, remote, state["paths"])
    _load_contents(root, peer, planned)
    ops = _build(planned, report, extra_local_keys)
    if dry_run:
        return report
    now = time.time() if now is None else now
    report.conflict_dir = _keep_conflict_copies(
        root, host, ops.conflict_copies, time.strftime("%Y%m%d-%H%M%S", time.localtime(now)))
    remote_hashes = peer.call({"op": "apply", "ops": ops.remote})["hashes"] if ops.remote else {}
    local_hashes = apply_ops(root, ops.local) if ops.local else {}
    paths = _new_base(root, local, remote, state["paths"], ops, (local_hashes, remote_hashes), extra_local_keys)
    _save_state(root, host, paths, now)
    return report
