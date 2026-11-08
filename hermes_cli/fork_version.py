"""The fork's own release identity: annotated ``fork-YYYY.MM.DD`` tags (``.N`` for a second release
on one day), described in ``docs/CHANGELOG-RU.md`` and cut by ``scripts/fork_release.py``.

They live apart from upstream's ``vX.Y.Z`` releases (``update_channel.STABLE_TAG_RE``) and its older
CalVer ``vYYYY.M.D`` tags, so neither the stable update channel nor ``version_info`` ever reads a
fork release as an upstream one; upstream's version and release date stay what they are.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

TAG_PREFIX = "fork-"
VERSION_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}(?:\.[2-9]|\.[1-9]\d+)?$")


def tag_for(version: str) -> str:
    return TAG_PREFIX + version


@lru_cache(maxsize=4)
def fork_version(repo_dir: Path) -> str | None:
    """``2026.11.08`` at a release, ``2026.11.08+3`` three commits past it, None before the first."""
    from hermes_cli.version_info import _run_git

    described = _run_git(repo_dir, "describe", "--tags", "--long", "--match", f"{TAG_PREFIX}[0-9]*", "HEAD")
    if not described:
        return None
    tag, count, _sha = described.rsplit("-", 2)
    version = tag.removeprefix(TAG_PREFIX)
    if not VERSION_RE.fullmatch(version):
        return None
    return version if count == "0" else f"{version}+{count}"
