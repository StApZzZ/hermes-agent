"""Spaces: named groups of agents (work, personal, projects) that you switch between.

A space sits ABOVE profiles and never merges their state: every agent in a space is still its own
profile island (config, MEMORY.md/USER.md, sessions, skills). What a space adds:

- membership in the member's own ``profile.yaml`` (``space`` / ``space_primary``), so a profile
  rename carries it and a delete drops it with no registry to repair;
- space attributes in ``<root>/spaces.yaml`` (title, kind, bound folders);
- shared space notes in ``<root>/spaces/<name>/SPACE.md`` that every agent of the space reads at
  session start (frozen into the system prompt like MEMORY.md, never re-read mid-session);
- the shared memory ``<root>/spaces/SHARED.md`` that every agent reads the same way, unless its
  space opts out (``shared_memory: false``, e.g. a work bot that colleagues talk to);
- per-chat lane selections in ``<root>/spaces/chat_selections.json`` that a multiplexed gateway
  consults to run one sender's lane under a space's agent (``/space`` in a chat).

``<root>`` is the installation root (``get_default_hermes_root()``), shared by every profile.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SPACE_KINDS = ("work", "personal", "project", "other")
SPACE_NOTES_CHAR_LIMIT = 3000
SHARED_MEMORY_CHAR_LIMIT = 3000
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class Space:
    name: str
    kind: str = "other"
    title: str = ""
    paths: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()  # primary first
    shared_memory: bool = True  # members read SHARED.md

    @property
    def primary(self) -> Optional[str]:
        return self.agents[0] if self.agents else None

    @property
    def label(self) -> str:
        return f"{self.title} ({self.name})" if self.title and self.title != self.name else self.name


# Paths

def _root() -> Path:
    from hermes_constants import get_default_hermes_root
    return get_default_hermes_root()


def registry_path() -> Path:
    return _root() / "spaces.yaml"


def space_dir(name: str) -> Path:
    return _root() / "spaces" / normalize_space_name(name)


def space_notes_path(name: str) -> Path:
    return space_dir(name) / "SPACE.md"


def shared_memory_path() -> Path:
    return _root() / "spaces" / "SHARED.md"


def selections_path() -> Path:
    return _root() / "spaces" / "chat_selections.json"


def normalize_space_name(name: str) -> str:
    canon = (name or "").strip().lower()
    if not _NAME_RE.match(canon):
        raise ValueError(
            f"Invalid space name {name!r}: use lowercase letters, digits, '-' or '_' (max 64 chars).")
    return canon


# YAML helpers

def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    import hermes_yaml
    try:
        data = hermes_yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except (OSError, ValueError, hermes_yaml.YAMLError) as exc:
        logger.warning("Ignoring unreadable %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _write_yaml(path: Path, data: dict) -> None:
    from utils import atomic_yaml_write
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_yaml_write(path, data, sort_keys=False)


def _read_registry() -> dict[str, dict]:
    raw = _load_yaml(registry_path()).get("spaces")
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict) and _NAME_RE.match(str(k))}


def _write_registry(registry: dict[str, dict]) -> None:
    _write_yaml(registry_path(), {"spaces": registry})


# Membership (profile.yaml of each member)

def _profile_dir(profile: str) -> Path:
    from hermes_cli.profiles import get_profile_dir, normalize_profile_name, profile_exists
    canon = normalize_profile_name(profile)
    if not profile_exists(canon):
        raise FileNotFoundError(f"Profile '{canon}' does not exist. Create it: hermes profile create {canon}")
    return get_profile_dir(canon)


def _membership(profile_dir: Path) -> tuple[str, bool]:
    data = _load_yaml(profile_dir / "profile.yaml")
    space = str(data.get("space") or "").strip().lower()
    return (space if _NAME_RE.match(space) else ""), bool(data.get("space_primary"))


def _write_membership(profile_dir: Path, space: Optional[str], primary: bool = False) -> None:
    path = profile_dir / "profile.yaml"
    data = _load_yaml(path)
    data.pop("space_primary", None)
    if space:
        data["space"] = space
        if primary:
            data["space_primary"] = True
    else:
        data.pop("space", None)
    _write_yaml(path, data)


def _members() -> dict[str, list[str]]:
    """space -> member profile names, primary first then alphabetical."""
    from hermes_cli.profiles import get_profile_dir, list_profile_names
    ranked: dict[str, list[tuple[bool, str]]] = {}
    for name in list_profile_names():
        space, primary = _membership(get_profile_dir(name))
        if space:
            ranked.setdefault(space, []).append((not primary, name))
    return {space: [name for _, name in sorted(rows)] for space, rows in ranked.items()}


def _build(name: str, attrs: dict, agents: list[str]) -> Space:
    kind = str(attrs.get("kind") or "other")
    paths = attrs.get("paths") if isinstance(attrs.get("paths"), list) else []
    return Space(name=name, kind=kind if kind in SPACE_KINDS else "other",
                 title=str(attrs.get("title") or "").strip(),
                 paths=tuple(str(p) for p in paths if p), agents=tuple(agents),
                 shared_memory=attrs.get("shared_memory") is not False)


# Queries

def list_spaces() -> list[Space]:
    """Declared spaces in registry order, then spaces only referenced by a member profile."""
    registry, members = _read_registry(), _members()
    names = list(registry) + sorted(s for s in members if s not in registry)
    return [_build(n, registry.get(n, {}), members.get(n, [])) for n in names]


def get_space(name: str) -> Space:
    canon = normalize_space_name(name)
    for space in list_spaces():
        if space.name == canon:
            return space
    known = ", ".join(s.name for s in list_spaces()) or "none yet"
    raise KeyError(f"Unknown space '{canon}' (known: {known}).")


def space_of_profile(profile: str) -> Optional[Space]:
    try:
        space, _primary = _membership(_profile_dir(profile))
        return get_space(space) if space else None
    except (FileNotFoundError, KeyError, ValueError):
        return None


def space_for_path(path: Path) -> Optional[Space]:
    """The space whose bound folder is the deepest ancestor of (or equal to) *path*."""
    if not registry_path().is_file():
        return None
    target = Path(path).resolve()
    best: tuple[int, str] | None = None
    for name, attrs in _read_registry().items():
        for raw in attrs.get("paths") or []:
            bound = Path(str(raw)).expanduser().resolve()
            if (bound == target or bound in target.parents) and (best is None or len(bound.parts) > best[0]):
                best = (len(bound.parts), name)
    return get_space(best[1]) if best else None


_FOLDER_BOUND_COMMANDS = (None, "chat")


def profile_for_cwd(argv: list[str]) -> Optional[str]:
    """Primary agent of the space bound to the current folder, for an agent-session launch only
    (bare ``hermes`` / ``hermes chat``). Management commands keep following ``-p`` and the sticky
    profile, so ``hermes gateway restart`` inside a project folder never re-targets another profile."""
    if not registry_path().is_file():
        return None
    try:
        from hermes_cli._parser import command_argv
        command = command_argv(argv)
        if (command[0] if command else None) not in _FOLDER_BOUND_COMMANDS:
            return None
        space = space_for_path(Path.cwd())
        return space.primary if space else None
    except Exception:  # health: allow BLE001 -- a broken spaces.yaml must never block startup
        logger.debug("Folder-bound space lookup failed", exc_info=True)
        return None


# Mutations

def create_space(name: str, *, kind: str = "other", title: str = "") -> Space:
    canon = normalize_space_name(name)
    if kind not in SPACE_KINDS:
        raise ValueError(f"Unknown kind '{kind}' (one of: {', '.join(SPACE_KINDS)}).")
    registry = _read_registry()
    if canon in registry:
        raise FileExistsError(f"Space '{canon}' already exists.")
    registry[canon] = {"kind": kind, **({"title": title.strip()} if title.strip() else {}), "paths": []}
    _write_registry(registry)
    return get_space(canon)


def update_space(name: str, *, kind: Optional[str] = None, title: Optional[str] = None,
                 shared_memory: Optional[bool] = None) -> Space:
    canon = get_space(name).name
    if kind is not None and kind not in SPACE_KINDS:
        raise ValueError(f"Unknown kind '{kind}' (one of: {', '.join(SPACE_KINDS)}).")
    registry = _read_registry()
    attrs = registry.setdefault(canon, {"paths": []})
    if kind is not None:
        attrs["kind"] = kind
    if title is not None:
        if title.strip():
            attrs["title"] = title.strip()
        else:
            attrs.pop("title", None)
    if shared_memory is not None:
        if shared_memory:
            attrs.pop("shared_memory", None)
        else:
            attrs["shared_memory"] = False
    _write_registry(registry)
    return get_space(canon)


def delete_space(name: str) -> Space:
    """Forget a space: registry entry, memberships, its notes and chat selections. Profiles stay."""
    import shutil
    space = get_space(name)
    for agent in space.agents:
        _write_membership(_profile_dir(agent), None)
    registry = _read_registry()
    registry.pop(space.name, None)
    _write_registry(registry)
    shutil.rmtree(space_dir(space.name), ignore_errors=True)
    _drop_selections(lambda entry: entry.get("space") == space.name)
    return space


def add_agent(name: str, profile: str, *, primary: bool = False) -> Space:
    """Put *profile* into space *name*, moving it out of any other space (one space per agent)."""
    space = get_space(name)
    profile_dir = _profile_dir(profile)
    if primary:
        from hermes_cli.profiles import get_profile_dir
        for other in space.agents:
            _write_membership(get_profile_dir(other), space.name)
    _write_membership(profile_dir, space.name, primary=primary)
    return get_space(space.name)


def remove_agent(profile: str) -> Optional[Space]:
    """Take *profile* out of its space; returns the space it left (None when it had none)."""
    space = space_of_profile(profile)
    _write_membership(_profile_dir(profile), None)
    return space


def bind_path(name: str, path: Path) -> Space:
    """Bind a folder to a space; ``hermes`` launched inside it runs the space's primary agent."""
    space = get_space(name)
    resolved = str(Path(path).expanduser().resolve())
    registry = _read_registry()
    for attrs in registry.values():
        attrs["paths"] = [p for p in attrs.get("paths") or [] if str(Path(str(p)).expanduser().resolve()) != resolved]
    registry.setdefault(space.name, {"paths": []}).setdefault("paths", []).append(resolved)
    _write_registry(registry)
    return get_space(space.name)


def unbind_path(name: str, path: Path) -> Space:
    space = get_space(name)
    resolved = str(Path(path).expanduser().resolve())
    registry = _read_registry()
    attrs = registry.get(space.name, {})
    kept = [p for p in attrs.get("paths") or [] if str(Path(str(p)).expanduser().resolve()) != resolved]
    if len(kept) == len(attrs.get("paths") or []):
        raise KeyError(f"{resolved} is not bound to space '{space.name}'.")
    attrs["paths"] = kept
    _write_registry(registry)
    return get_space(space.name)


# Shared space notes (SPACE.md) and shared memory (SHARED.md); stores live in memory_scopes

def read_notes(name: str) -> list[str]:
    from hermes_cli.memory_scopes import read_entries, space_store
    return read_entries(space_store(name))


def add_note(name: str, text: str) -> list[str]:
    """Append a shared note (strict threat scan, bounded store); returns the space's notes."""
    from hermes_cli.memory_scopes import add_entries, read_entries, space_store
    store = space_store(name)
    add_entries(store, [text])
    return read_entries(store)


def remove_note(name: str, index: int) -> str:
    """Remove the 1-based note *index*; returns the removed text."""
    from hermes_cli.memory_scopes import read_entries, remove_entries, space_store
    store = space_store(name)
    notes = read_entries(store)
    if not 1 <= index <= len(notes):
        raise IndexError(f"Space '{normalize_space_name(name)}' has {len(notes)} note(s); no #{index}.")
    remove_entries(store, [notes[index - 1]])
    return notes[index - 1]


def _prompt_safe(store) -> list[str]:
    """Entries of *store* minus any that fail the strict scan (a hand edit bypasses the write check)."""
    from hermes_cli.memory_scopes import read_entries
    from tools.memory_tool_store import _scan_memory_content
    entries = read_entries(store)
    safe = [e for e in entries if not _scan_memory_content(e)]
    if len(safe) < len(entries):
        logger.warning("Dropped %d entr(y/ies) of %s from the system prompt: threat pattern",
                       len(entries) - len(safe), store.label)
    return safe


def _shared_memory_block() -> Optional[str]:
    from hermes_cli.memory_scopes import shared_store, usage
    from tools.memory_tool_store import ENTRY_DELIMITER
    entries = _prompt_safe(shared_store())
    if not entries:
        return None
    sep = "═" * 46
    return (f"{sep}\nSHARED MEMORY (known to all your agents) "
            f"[{usage(entries):,}/{SHARED_MEMORY_CHAR_LIMIT:,} chars]\n{sep}\n"
            "Facts that hold in every space. Save one with `hermes memory add shared \"<fact>\"` "
            "(applies from the next session).\n" + ENTRY_DELIMITER.join(entries))


def _space_block(space: Space, profile: str) -> str:
    from hermes_cli.memory_scopes import space_store
    others = [a for a in space.agents if a != profile]
    lines = [
        f"SPACE: {space.label} [{space.kind}]",
        f"You are the '{profile}' agent of this space"
        + (f"; other agents here: {', '.join(others)}." if others else "."),
        "Keep this space's information inside it. Save a fact every agent of the space should know with "
        f"`hermes space note {space.name} \"<fact>\"` (applies from the next session).",
    ]
    notes = _prompt_safe(space_store(space.name))
    if notes:
        lines += ["Shared space notes:", *(f"- {n}" for n in notes)]
    return "\n".join(lines)


def system_prompt_block(profile: str) -> Optional[str]:
    """Shared memory (unless the space opts out) plus the space block for *profile*, or None.
    Read once when the prompt is built (session start), so it never invalidates the cached prefix."""
    space = space_of_profile(profile)
    parts = [_shared_memory_block() if space is None or space.shared_memory else None,
             _space_block(space, profile) if space is not None else None]
    return "\n\n".join(p for p in parts if p) or None


# Chat lane selections (multiplexed gateway, ``/space`` in a chat)

_SELECTIONS_LOCK = threading.Lock()
_SELECTIONS_CACHE: dict[str, object] = {"sig": None, "data": {}}


def lane_key(platform: str, bot_profile: Optional[str], chat_id, user_id) -> str:
    """One sender in one chat as received by one bot: a switch never re-homes anyone else."""
    return json.dumps([str(platform), bot_profile or "default", str(chat_id or ""), str(user_id or "")])


def _signature(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def _selections() -> dict[str, dict]:
    path = selections_path()
    sig = _signature(path)
    with _SELECTIONS_LOCK:
        if sig != _SELECTIONS_CACHE["sig"]:
            from utils import read_json_or_empty
            data = read_json_or_empty(path) if sig else {}
            _SELECTIONS_CACHE.update(sig=sig, data={k: v for k, v in data.items() if isinstance(v, dict)})
        return dict(_SELECTIONS_CACHE["data"])


def _store_selections(data: dict[str, dict]) -> None:
    from utils import atomic_json_write
    path = selections_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(path, data)


def selected_profile(key: str) -> Optional[str]:
    entry = _selections().get(key)
    profile = str((entry or {}).get("profile") or "").strip()
    return profile or None


def selection(key: str) -> Optional[dict]:
    return _selections().get(key)


def select_lane(key: str, space: str, profile: str) -> None:
    with _SELECTIONS_LOCK:
        from utils import read_json_or_empty
        data = read_json_or_empty(selections_path())
        data[key] = {"space": space, "profile": profile}
        _store_selections(data)


def clear_lane(key: str) -> bool:
    with _SELECTIONS_LOCK:
        from utils import read_json_or_empty
        data = read_json_or_empty(selections_path())
        if data.pop(key, None) is None:
            return False
        _store_selections(data)
        return True


def _drop_selections(predicate) -> None:
    with _SELECTIONS_LOCK:
        from utils import read_json_or_empty
        data = read_json_or_empty(selections_path())
        kept = {k: v for k, v in data.items() if not (isinstance(v, dict) and predicate(v))}
        if len(kept) != len(data):
            _store_selections(kept)


def profiles_selected_via(bot_profile: Optional[str]) -> set[str]:
    """Profiles some chat lane reaches through *bot_profile*'s bot (``None`` = the default bot)."""
    bot = bot_profile or "default"
    found = set()
    for key, entry in _selections().items():
        try:
            if json.loads(key)[1] == bot and entry.get("profile"):
                found.add(str(entry["profile"]))
        except (ValueError, IndexError, TypeError):
            continue
    return found
