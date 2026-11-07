#!/usr/bin/env python3
"""Check scripts/deploy/ide: every extension ID in the lists exists in its marketplace.

    python3 scripts/deploy/check_ide_lists.py

Exit 1: an unknown ID or a malformed line; 2: the marketplace is unreachable.
"""
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*\.[A-Za-z0-9][A-Za-z0-9.-]*(@\d+(\.\d+)*)?$")
IDE = pathlib.Path(__file__).resolve().parent / "ide"


def ids(path: pathlib.Path) -> list[str]:
    out = []
    for n, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if not ID_RE.match(line):
            sys.exit(f"{path}:{n}: not an extension ID: {line!r}")
        out.append(line.split("@", 1)[0])
    return out


def vscode_marketplace(names: list[str]) -> list[str]:
    body = {"filters": [{"criteria": [{"filterType": 7, "value": n} for n in names]}], "flags": 0}
    req = urllib.request.Request(
        "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json;api-version=7.2-preview.1",
                 "User-Agent": "hermes-deploy-check"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            found = {f"{e['publisher']['publisherName']}.{e['extensionName']}".lower()
                     for e in json.loads(r.read())["results"][0]["extensions"]}
    except (OSError, urllib.error.URLError) as e:
        print(f"marketplace unreachable: {e}")
        sys.exit(2)
    return [n for n in names if n.lower() not in found]


def main() -> int:
    json.loads((IDE / "machine-settings.json").read_text(encoding="utf-8-sig"))
    bad = 0
    for rel in ("vscode/local.txt", "vscode/remote.txt"):
        names = ids(IDE / rel)
        missing = vscode_marketplace(names)
        print(f"{rel}: {len(names)} IDs, unknown: {', '.join(missing) or '-'}")
        bad += len(missing)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
