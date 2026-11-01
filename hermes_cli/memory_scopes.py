"""Memory scopes: one address for every curated memory store, to list, edit, copy and move entries
between agents, spaces and the shared memory.

    <profile>          both of a profile's stores (MEMORY.md, then USER.md)
    <profile>:memory   the agent's own notes (MEMORY.md)
    <profile>:user     what the agent knows about you (USER.md)
    space:<name>       a space's notes (SPACE.md), read by every agent of the space
    shared             the shared memory (SHARED.md), read by every agent

Every store is the same ``§``-delimited file under the memory tool's own lock, so a write here
never races a live agent's ``memory`` call; agents see the change from their next session (the
prompt snapshot is frozen at session start). Entries enter system prompts, so every write runs the
memory tool's strict threat scan and respects the store's char limit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from tools.memory_tool_store import ENTRY_DELIMITER, MemoryStore, _scan_memory_content

SHARED = "shared"
_PROFILE_STORES = {"memory": ("MEMORY.md", "memory_char_limit", 2200),
                   "user": ("USER.md", "user_char_limit", 1375)}


@dataclass(frozen=True)
class Store:
    label: str   # the address that selects exactly this store, e.g. "work:user"
    kind: str    # "memory" | "user" | "notes"
    path: Path
    limit: int


# Addressing

def shared_store() -> Store:
    from hermes_cli import spaces
    return Store(SHARED, "notes", spaces.shared_memory_path(), spaces.SHARED_MEMORY_CHAR_LIMIT)


def space_store(name: str) -> Store:
    from hermes_cli import spaces
    space = spaces.get_space(name)
    return Store(f"space:{space.name}", "notes", spaces.space_notes_path(space.name), spaces.SPACE_NOTES_CHAR_LIMIT)


def profile_stores(profile: str, kind: Optional[str] = None) -> list[Store]:
    from hermes_cli.profiles import normalize_profile_name
    from hermes_cli.spaces import _load_yaml, _profile_dir
    canon = normalize_profile_name(profile)
    home = _profile_dir(canon)
    section = _load_yaml(home / "config.yaml").get("memory")
    limits = section if isinstance(section, dict) else {}
    stores = []
    for name in ([kind] if kind else list(_PROFILE_STORES)):
        filename, limit_key, default = _PROFILE_STORES[name]
        try:
            limit = int(limits.get(limit_key, default))
        except (TypeError, ValueError):
            limit = default
        stores.append(Store(f"{canon}:{name}", name, home / "memories" / filename, limit))
    return stores


def resolve(spec: str) -> list[Store]:
    """Stores named by *spec* (see the module docstring); raises ValueError/KeyError/FileNotFoundError."""
    spec = (spec or "").strip()
    if spec == SHARED:
        return [shared_store()]
    head, sep, tail = spec.partition(":")
    if sep and head == "space":
        return [space_store(tail)]
    if sep and tail not in _PROFILE_STORES:
        raise ValueError(f"Unknown store '{spec}': use <profile>, <profile>:memory, <profile>:user, "
                         "space:<name> or shared.")
    return profile_stores(head, tail or None)


def resolve_one(spec: str) -> Store:
    stores = resolve(spec)
    if len(stores) > 1:
        raise ValueError(f"'{spec}' names two stores; pick one: {' or '.join(s.label for s in stores)}.")
    return stores[0]


def all_stores() -> list[Store]:
    """Every store on this installation: shared, each space's notes, each profile's two stores."""
    from hermes_cli import spaces
    from hermes_cli.profiles import list_profile_names
    stores = [shared_store(), *(space_store(s.name) for s in spaces.list_spaces())]
    for name in list_profile_names():
        stores += profile_stores(name)
    return stores


# Reading and writing

def read_entries(store: Store) -> list[str]:
    return list(dict.fromkeys(MemoryStore._read_file(store.path)))


def usage(entries: list[str]) -> int:
    return len(ENTRY_DELIMITER.join(entries))


def _update(store: Store, change: Callable[[list[str]], list[str]]) -> list[str]:
    """Lock, re-read, apply *change*, enforce the limit and persist; returns the new entries."""
    store.path.parent.mkdir(parents=True, exist_ok=True)
    with MemoryStore._file_lock(store.path):
        raw, read_ok = MemoryStore._read_raw_checked(store.path)
        if not read_ok:
            raise OSError(f"{store.path} exists but could not be read; nothing was changed.")
        before = list(dict.fromkeys(MemoryStore._parse_entries(raw)))
        after = change(before)
        if usage(after) > store.limit and usage(after) > usage(before):
            raise ValueError(f"{store.label} would hold {usage(after):,} chars (limit {store.limit:,}). "
                             f"Remove or shorten entries first: hermes memory list {store.label}")
        MemoryStore._write_file(store.path, after)
        return after


def check_entry(text: str) -> str:
    entry = (text or "").strip()
    if not entry:
        raise ValueError("Entry is empty.")
    threat = _scan_memory_content(entry)
    if threat:
        raise ValueError(f"Refusing entry: {threat}")
    return entry


def add_entries(store: Store, texts: list[str]) -> list[str]:
    """Append *texts* that the store does not hold yet; returns the ones actually added."""
    new = list(dict.fromkeys(check_entry(t) for t in texts))
    added: list[str] = []

    def _append(entries: list[str]) -> list[str]:
        added.extend(t for t in new if t not in entries)
        return entries + added
    _update(store, _append)
    return added


def remove_entries(store: Store, texts: list[str]) -> int:
    doomed = set(texts)
    removed = 0

    def _drop(entries: list[str]) -> list[str]:
        nonlocal removed
        kept = [e for e in entries if e not in doomed]
        removed = len(entries) - len(kept)
        return kept
    _update(store, _drop)
    return removed


# Selecting entries

_RANGE_RE = re.compile(r"^(\d+)(?:-(\d+))?$")


def parse_indices(spec: str, count: int) -> list[int]:
    """1-based ``"1,3-5"`` -> 0-based indices, in the order given, without repeats."""
    picked: list[int] = []
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        m = _RANGE_RE.match(part)
        if not m:
            raise ValueError(f"Bad entry number '{part}': use numbers and ranges like 1,3-5.")
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        if not 1 <= lo <= hi <= count:
            raise IndexError(f"Entry {part} is out of range (the store has {count} entries).")
        picked += [i - 1 for i in range(lo, hi + 1)]
    return list(dict.fromkeys(picked))


def select(entries: list[str], *, numbers: Optional[str] = None, match: Optional[list[str]] = None) -> list[str]:
    """All entries, the numbered ones, or the ones containing any *match* text (case-insensitive)."""
    if numbers:
        return [entries[i] for i in parse_indices(numbers, len(entries))]
    if match:
        needles = [m.lower() for m in match]
        return [e for e in entries if any(n in e.lower() for n in needles)]
    return list(entries)


# Copy / move

@dataclass
class Transfer:
    source: Store
    dest: Store
    entries: list[str]
    added: list[str]


def _dest_for(source: Store, dests: list[Store]) -> Store:
    """A whole profile as destination keeps each entry's kind; notes land in the agent's MEMORY.md."""
    if len(dests) == 1:
        return dests[0]
    return next(d for d in dests if d.kind == (source.kind if source.kind in _PROFILE_STORES else "memory"))


def plan_transfer(src: str, dst: str, *, numbers: Optional[str] = None,
                  match: Optional[list[str]] = None) -> list[Transfer]:
    sources, dests = resolve(src), resolve(dst)
    if numbers and len(sources) > 1:
        raise ValueError(f"Entry numbers need one store; pick {' or '.join(s.label for s in sources)}.")
    plans = []
    for source in sources:
        dest = _dest_for(source, dests)
        if dest.path == source.path:
            raise ValueError(f"{source.label} is both the source and the destination.")
        picked = select(read_entries(source), numbers=numbers, match=match)
        if picked:
            for entry in picked:
                check_entry(entry)
            plans.append(Transfer(source, dest, picked, []))
    _check_room(plans)
    return plans


def _check_room(plans: list[Transfer]) -> None:
    """Refuse up front when a destination would overflow, so a transfer never half-applies."""
    incoming: dict[Path, tuple[Store, list[str]]] = {}
    for plan in plans:
        incoming.setdefault(plan.dest.path, (plan.dest, []))[1].extend(plan.entries)
    for dest, entries in incoming.values():
        current = read_entries(dest)
        after = current + [e for e in dict.fromkeys(entries) if e not in current]
        if usage(after) > dest.limit:
            raise ValueError(f"{dest.label} would hold {usage(after):,} chars (limit {dest.limit:,}); "
                             f"it has {usage(current):,}. Pick fewer entries (--entries / --match) "
                             f"or free space: hermes memory list {dest.label}")


def apply_transfer(plans: list[Transfer], *, move: bool) -> list[Transfer]:
    """Write every destination first; only then (``move``) drop the entries from their sources."""
    for plan in plans:
        plan.added = add_entries(plan.dest, plan.entries)
    if move:
        for plan in plans:
            remove_entries(plan.source, plan.entries)
    return plans
