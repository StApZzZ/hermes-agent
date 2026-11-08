"""Three-way merge rules for ``hermes sync``. Pure functions: no I/O, no clocks.

Each side's state is compared with the state both sides had after the last successful sync (the
base). A side that did not change since then takes the other side's version; a side that changed
alone wins; when both changed, YAML documents merge key by key, memory stores entry by entry, and
other files fall back to "the newer copy wins" (the caller keeps the other copy).
"""

from __future__ import annotations

from typing import Any, Iterable


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()

# Settings that describe the machine rather than the person: paths, terminal backend, local
# services, messaging adapters (one bot token must be polled by ONE gateway), schema version.
# Each side keeps its own values for these; everything else in config.yaml is shared.
MACHINE_LOCAL_CONFIG_KEYS = (
    "_config_version", "terminal", "gateway", "platforms", "webapp", "dashboard", "desktop", "kanban",
    "worktrees", "lsp", "mcp_servers", "sync",
    "telegram", "discord", "slack", "whatsapp", "mattermost", "matrix",
    "browser.executable_path", "browser.user_data_dir",
    "skills.external_dirs", "skills.create_dir", "skills.trusted_project_dirs",
    "wake_word.model_dir", "moa.trace_dir", "vault.*.binary_path", "secrets.*.binary_path",
    "update.installs",  # this machine's checkouts, keyed by install id, with their paths
)
# A space's bound folders are paths on one machine.
MACHINE_LOCAL_SPACES_KEYS = ("spaces.*.paths",)


def parse_patterns(patterns: Iterable[str]) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(p.split(".")) for p in patterns if p)


def _matches(pattern: tuple[str, ...], path: tuple[str, ...]) -> bool:
    return len(pattern) == len(path) and all(p in ("*", k) for p, k in zip(pattern, path))


def _is_prefix(pattern: tuple[str, ...], path: tuple[str, ...]) -> bool:
    return len(pattern) > len(path) and all(p in ("*", k) for p, k in zip(pattern, path))


def split_local(doc: dict, patterns, prefix: tuple[str, ...] = ()) -> tuple[dict, dict]:
    """``(shared, machine_local)`` parts of a mapping; ``graft`` puts them back together."""
    shared: dict = {}
    local: dict = {}
    for key, value in doc.items():
        path = prefix + (str(key),)
        if any(_matches(p, path) for p in patterns):
            local[key] = value
        elif isinstance(value, dict) and value and any(_is_prefix(p, path) for p in patterns):
            sub_shared, sub_local = split_local(value, patterns, path)
            if sub_shared:
                shared[key] = sub_shared
            if sub_local:
                local[key] = sub_local
        else:
            shared[key] = value
    return shared, local


def graft(shared: dict, local: dict) -> dict:
    out = dict(shared)
    for key, value in local.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = graft(out[key], value)
        else:
            out[key] = value
    return out


def merge3(base: Any, ours: Any, theirs: Any, *, ours_wins: bool,
           path: tuple[str, ...] = (), conflicts: list | None = None) -> Any:
    """Three-way merge of YAML values; MISSING marks an absent key. Nested mappings merge per key,
    so two machines editing different keys of one section never conflict. A real conflict (both
    changed one scalar differently) resolves to ``ours`` when ``ours_wins`` and is recorded."""
    if ours == theirs:
        return ours
    if ours == base:
        return theirs
    if theirs == base:
        return ours
    if isinstance(ours, dict) and isinstance(theirs, dict):
        base_map = base if isinstance(base, dict) else {}
        merged = {}
        for key in [*ours, *(k for k in theirs if k not in ours)]:
            value = merge3(base_map.get(key, MISSING), ours.get(key, MISSING), theirs.get(key, MISSING),
                           ours_wins=ours_wins, path=path + (str(key),), conflicts=conflicts)
            if value is not MISSING:
                merged[key] = value
        return merged
    if conflicts is not None:
        conflicts.append(".".join(path) or "<document>")
    return ours if ours_wins else theirs


def merge_entries(base: list[str] | None, ours: list[str], theirs: list[str]) -> list[str]:
    """Union of both sides minus what either side removed since the base. An edited entry is a
    removal plus an addition, so both edits survive and the original is dropped."""
    base_set = set(base or ())
    removed = (base_set - set(ours)) | (base_set - set(theirs))
    merged: list[str] = []
    for entry in [*ours, *theirs]:
        if entry not in removed and entry not in merged:
            merged.append(entry)
    return merged


# Actions for one path. "push" = local -> remote, "pull" = remote -> local.
SAME, PUSH, PULL, MERGE, DELETE_LOCAL, DELETE_REMOTE, DROP = (
    "same", "push", "pull", "merge", "delete-local", "delete-remote", "drop")


def plan_path(base_local: str | None, base_remote: str | None,
              local: str | None, remote: str | None) -> str:
    """Decide one path from its hashes (None = absent) and the hashes each side had at the base
    (both None = never synced). An edit beats a deletion on the other side."""
    if local is None and remote is None:
        return DROP
    if local == remote:
        return SAME
    if base_local is None and base_remote is None:
        if remote is None:
            return PUSH
        return PULL if local is None else MERGE
    local_changed, remote_changed = local != base_local, remote != base_remote
    if not local_changed and not remote_changed:
        return SAME
    if not local_changed:
        return DELETE_LOCAL if remote is None else PULL
    if not remote_changed:
        return DELETE_REMOTE if local is None else PUSH
    if local is None:
        return PULL
    if remote is None:
        return PUSH
    return MERGE
