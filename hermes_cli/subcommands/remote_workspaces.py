"""Data-only workspace bookmarks for the standalone remote launcher.

Kept separate from Hermes' runtime config: Windows needs no agent install,
YAML parser, credentials or PM environment to read these bookmarks.
"""

import json
import os
from pathlib import Path


def config_path() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home() / ".config")
    return base / "hermes-remote" / "workspaces.json"


def load_workspaces(path: Path | None) -> dict:
    source = path or config_path()
    if path is None and not source.exists():
        return {}
    try:
        data = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read workspace config {source}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Workspace config must be a JSON object keyed by workspace name")
    for name, entry in data.items():
        _validate_entry(name, entry)
    return data


def _validate_entry(name, entry):
    from hermes_cli.subcommands.remote import _checked_host, _checked_workspace, _TMUX_SESSION

    if not isinstance(entry, dict) or set(entry) - {"host", "workspace", "agent", "session", "tmux"}:
        raise ValueError(f"Invalid fields in workspace {name!r}; allowed: host, workspace, agent, session, tmux")
    _checked_host(entry.get("host"))
    _checked_workspace(entry.get("workspace"))
    if entry.get("agent", "hermes") not in {"hermes", "claude"}:
        raise ValueError(f"Workspace {name!r}: agent must be hermes or claude")
    if "tmux" in entry and not isinstance(entry["tmux"], bool):
        raise ValueError(f"Workspace {name!r}: tmux must be boolean")
    if "session" in entry and (not isinstance(entry["session"], str)
                                or not _TMUX_SESSION.fullmatch(entry["session"])):
        raise ValueError(f"Workspace {name!r}: invalid session label")


def resolve_target(host: str, workspace: str | None, profiles: dict) -> dict:
    target = dict(profiles.get(host, {"host": host}))
    if workspace is not None:
        target["workspace"] = workspace
    if "workspace" not in target:
        raise ValueError("Supply --workspace /absolute/path or use a saved workspace name (remote list)")
    return target
