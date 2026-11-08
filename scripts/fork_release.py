#!/usr/bin/env python3
"""Cut a fork release: ``fork-YYYY.MM.DD`` (``.N`` for another one that day).

    python scripts/fork_release.py            # check, show what would happen
    python scripts/fork_release.py --apply    # rename the changelog's unreleased section, commit, tag
    python scripts/fork_release.py --apply --push

Runs on a clean ``main`` equal to ``origin/main``. The unreleased section of docs/CHANGELOG-RU.md must
say what changed; it becomes the release's section and the annotated tag's message.
"""

from __future__ import annotations

import argparse
import datetime
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hermes_cli.fork_version import tag_for

CHANGELOG = ROOT / "docs" / "CHANGELOG-RU.md"
UNRELEASED = "## Не выпущено"


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", stdin=subprocess.DEVNULL, timeout=300, check=False)
    if check and result.returncode:
        raise SystemExit(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()


def next_version(today: datetime.date, existing: set[str]) -> str:
    base = today.strftime("%Y.%m.%d")
    if tag_for(base) not in existing:
        return base
    n = 2
    while tag_for(f"{base}.{n}") in existing:
        n += 1
    return f"{base}.{n}"


def split_unreleased(text: str) -> tuple[str, str, str]:
    """(before, unreleased body, after) around the unreleased section."""
    head, sep, rest = text.partition(UNRELEASED + "\n")
    if not sep:
        raise SystemExit(f"{CHANGELOG.name}: no '{UNRELEASED}' section")
    body, nxt, tail = rest.partition("\n## ")
    return head, body.strip(), (nxt + tail) if nxt else ""


def released_text(text: str, version: str) -> tuple[str, str]:
    """The changelog with the unreleased section turned into ``version``, and that section's body."""
    head, body, after = split_unreleased(text)
    if not body:
        raise SystemExit(f"{CHANGELOG.name}: the '{UNRELEASED}' section is empty: write what changed first")
    after = after.lstrip("\n")
    return f"{head}{UNRELEASED}\n\n## {version}\n\n{body}\n\n{after}".rstrip() + "\n", body


def preflight() -> None:
    if git("branch", "--show-current") != "main":
        raise SystemExit("Release from the main branch.")
    if git("status", "--porcelain", "-uno"):
        raise SystemExit("The working tree has uncommitted changes.")
    git("fetch", "--quiet", "--tags", "origin")
    if git("rev-parse", "HEAD") != git("rev-parse", "origin/main"):
        raise SystemExit("main differs from origin/main: pull or push first.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Commit the changelog and create the tag")
    parser.add_argument("--push", action="store_true", help="With --apply: push main and the tag to origin")
    args = parser.parse_args(argv)

    preflight()
    existing = set(git("tag", "--list", tag_for("*")).splitlines())
    version = next_version(datetime.date.today(), existing)
    new_text, notes = released_text(CHANGELOG.read_text(encoding="utf-8-sig"), version)
    print(f"Release {tag_for(version)}:\n\n{notes}\n")
    if not args.apply:
        print("Dry run. Cut it with --apply (and --push to publish).")
        return 0
    CHANGELOG.write_text(new_text, encoding="utf-8")
    git("commit", "--quiet", "-m", f"chore(release): {tag_for(version)}", "--", str(CHANGELOG.relative_to(ROOT)))
    git("tag", "-a", tag_for(version), "-m", f"{tag_for(version)}\n\n{notes}")
    if args.push:
        git("push", "--quiet", "origin", "main", tag_for(version))
        print(f"Pushed main and {tag_for(version)}. On a host: `hermes update`, then `hermes --version`.")
    else:
        print(f"Tagged {tag_for(version)}. Publish: git push origin main {tag_for(version)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
