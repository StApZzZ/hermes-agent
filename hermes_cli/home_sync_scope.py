"""What ``hermes sync`` carries between two Hermes installations, and how each path merges.

The scope is an allowlist: settings, persona, built-in memory, skills and spaces. Secrets
(``.env``, ``auth.json``), sessions, databases, logs and caches are outside it by construction,
and both ends evaluate the same predicate, so a peer can never be asked to read or write a path
outside it. Bundled skills that still match their shipped hash are skipped: each installation
seeds its own from its own Hermes version, so only user-made or user-edited skills travel.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

KIND_YAML = "yaml"  # merged key by key; machine-local keys stay on their machine
KIND_ENTRIES = "entries"  # §-delimited memory stores, merged entry by entry
KIND_FILE = "file"  # copied whole; on a two-sided edit the newer copy wins

_NAME = r"[a-z0-9][a-z0-9_-]{0,63}"
_HOME = rf"(?:profiles/{_NAME}/)?"
_RULES = (
    (re.compile(rf"{_HOME}(?:config|profile)\.yaml|spaces\.yaml"), KIND_YAML),
    (re.compile(rf"{_HOME}memories/(?:MEMORY|USER)\.md|spaces/SHARED\.md|spaces/{_NAME}/SPACE\.md"),
     KIND_ENTRIES),
    (re.compile(rf"{_HOME}SOUL\.md"), KIND_FILE),
)
_SKILL_PATH = re.compile(rf"{_HOME}skills/(.+)")
_PROFILE_DIR = re.compile(_NAME)

# Per-machine skill bookkeeping describes THIS installation: usage counters, curator run state,
# the bundled-seed manifest, and the hub's install records, audit log and scan cache (a hub skill
# that arrives by sync is an ordinary skill on the other side). Hub taps, the archive and curator
# suppressions are the user's decisions and travel.
_SKILLS_BOOKKEEPING = frozenset({".usage.json", ".curator_state", ".bundled_manifest", ".no-bundled-skills"})
_HUB_BOOKKEEPING = frozenset({"lock.json", "audit.log", "scan-cache"})
_SKIP_DIRS = frozenset({"__pycache__", ".git", "node_modules", ".venv", "venv"})
_HOME_FILES = ("config.yaml", "profile.yaml", "SOUL.md", "memories/MEMORY.md", "memories/USER.md")


def _skill_rest_ok(rest: str) -> bool:
    parts = rest.split("/")
    if parts[0] in _SKILLS_BOOKKEEPING or any(p in _SKIP_DIRS for p in parts):
        return False
    if parts[0] == ".hub" and len(parts) > 1 and parts[1] in _HUB_BOOKKEEPING:
        return False
    name = parts[-1]
    return not (name.endswith((".lock", ".pyc")) or name.startswith(".tmp"))


def kind_of(rel: str) -> str | None:
    """The merge kind of a root-relative POSIX path, or None when it is outside the scope."""
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
        return None
    if any(p in ("", ".", "..") for p in rel.split("/")) or any(ord(c) < 32 for c in rel):
        return None
    for pattern, kind in _RULES:
        if pattern.fullmatch(rel):
            return kind
    match = _SKILL_PATH.fullmatch(rel)
    return KIND_FILE if match and _skill_rest_ok(match.group(1)) else None


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bundled_manifest(skills: Path) -> dict[str, str]:
    try:
        text = (skills / ".bundled_manifest").read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    pairs = (line.strip().partition(":") for line in text.splitlines())
    return {name.strip(): digest.strip() for name, _, digest in pairs if name.strip() and digest.strip()}


def pristine_bundled_dirs(skills: Path) -> set[Path]:
    """Skill dirs that are byte-identical to what this installation's Hermes seeded."""
    manifest = _bundled_manifest(skills)
    if not manifest:
        return set()
    from tools.skill_usage import read_skill_name
    from tools.skills_sync import dir_hash

    pristine = set()
    for skill_md in skills.rglob("SKILL.md"):
        skill_dir = skill_md.parent
        origin = manifest.get(read_skill_name(skill_md, skill_dir.name))
        if origin and origin in (dir_hash(skill_dir), dir_hash(skill_dir, include_runtime_cache=True)):
            pristine.add(skill_dir)
    return pristine


def _homes(root: Path) -> list[tuple[str, Path]]:
    from hermes_constants import named_profile_has_identity

    homes = [("", root)]
    profiles = root / "profiles"
    if profiles.is_dir():
        for entry in sorted(profiles.iterdir()):
            if (_PROFILE_DIR.fullmatch(entry.name) and entry.is_dir() and not entry.is_symlink()
                    and named_profile_has_identity(entry)):
                homes.append((f"profiles/{entry.name}/", entry))
    return homes


def _record(out: dict, root: Path, rel: str) -> None:
    path = root / rel
    if kind_of(rel) and path.is_file() and not path.is_symlink():
        out[rel] = {"hash": file_hash(path), "mtime": path.stat().st_mtime}


def _scan_skills(out: dict, root: Path, prefix: str, skills: Path) -> None:
    if not skills.is_dir() or skills.is_symlink():
        return
    skip = pristine_bundled_dirs(skills)
    for dirpath, dirnames, filenames in os.walk(skills):
        current = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and current / d not in skip
                       and not (current / d).is_symlink()]
        for name in filenames:
            rel_in_skills = (current / name).relative_to(skills).as_posix()
            _record(out, root, f"{prefix}skills/{rel_in_skills}")


def scan(root: Path) -> dict[str, dict]:
    """``{rel_path: {"hash", "mtime"}}`` for every in-scope file under the installation root."""
    out: dict[str, dict] = {}
    for prefix, home in _homes(root):
        for name in _HOME_FILES:
            _record(out, root, prefix + name)
        _scan_skills(out, root, prefix, home / "skills")
    _record(out, root, "spaces.yaml")
    _record(out, root, "spaces/SHARED.md")
    spaces = root / "spaces"
    if spaces.is_dir():
        for notes in sorted(spaces.glob("*/SPACE.md")):
            _record(out, root, notes.relative_to(root).as_posix())
    return out
