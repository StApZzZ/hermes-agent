#!/usr/bin/env python3
"""Merge JSON patches into a VS Code settings.json (stdlib only; runs before Hermes exists).

    vscode_settings.py SETTINGS PATCH [PATCH ...]      PATCH: a JSON file, or - for stdin

Objects merge key by key; lists gain the missing items (strings compared case-insensitively,
as VS Code compares extension IDs); anything else is replaced. settings.json is JSONC: comments
and trailing commas are accepted, and the first original is kept next to the hermes-deploy cache
before the file is rewritten as plain JSON.
"""
import json
import os
import pathlib
import sys


def strip_jsonc(text: str) -> str:
    out, i, n, in_str, esc = [], 0, len(text), False, False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            in_str = esc or c != '"'
            esc = not esc and c == "\\"
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        else:
            out.append(c)
        i += 1
    t, res, in_str, esc = "".join(out), [], False, False
    for j, c in enumerate(t):
        if in_str:
            in_str = esc or c != '"'
            esc = not esc and c == "\\"
        elif c == '"':
            in_str = True
        elif c == "," and t[j + 1:].lstrip()[:1] in ("}", "]"):
            continue
        res.append(c)
    return "".join(res)


def merge(cur, patch):
    if isinstance(cur, dict) and isinstance(patch, dict):
        return {**cur, **{k: merge(cur.get(k), v) if k in cur else v for k, v in patch.items()}}
    if isinstance(cur, list) and isinstance(patch, list):
        seen = {x.lower() if isinstance(x, str) else json.dumps(x) for x in cur}
        return cur + [x for x in patch if (x.lower() if isinstance(x, str) else json.dumps(x)) not in seen]
    return patch


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    path = pathlib.Path(sys.argv[1]).expanduser()
    original = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    settings = json.loads(strip_jsonc(original) or "{}")
    for source in sys.argv[2:]:
        text = sys.stdin.read() if source == "-" else pathlib.Path(source).read_text(encoding="utf-8-sig")
        settings = merge(settings, json.loads(text))
    if original:
        cache = pathlib.Path(os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache") / "hermes-deploy"
        backup = cache / (path.parent.name + "-" + path.name + ".bak")
        if not backup.exists():   # the first copy keeps the user's comments; reruns never overwrite it
            cache.mkdir(parents=True, exist_ok=True)
            backup.write_text(original, encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{path}: {len(settings)} keys")
    return 0


if __name__ == "__main__":
    sys.exit(main())
