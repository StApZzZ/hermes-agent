#!/bin/bash
# Server half of backup-pull.sh: runs as the Hermes user and writes ONE tar.gz to stdout.
# Diagnostics go to stderr. SQLite databases are copied through the backup API so a live
# session or gateway never yields a torn state.db; everything rebuildable is left out.
set -euo pipefail
cd "$HOME"

ROOTS=(.hermes .claude .claude.json .codex .gemini .config/git .gitconfig .ssh/authorized_keys
       .bashrc .profile repos work)
EXCLUDES=(
    # Rebuilt by Hermes / the CLIs on demand.
    .hermes/tools .hermes/cache .hermes/installs .hermes/image_cache .hermes/audio_cache
    .hermes/sandboxes .hermes/runtime .hermes/models_dev_cache.json
    .codex/packages .codex/cache .codex/tmp .codex/log .codex/models_cache.json
    .codex/app-server-control .codex/app-server-daemon
    .claude/cache .claude/downloads .gemini/tmp
    # Subscription logins stay inside each CLI; restoring is one native login, not a token copy.
    .codex/auth.json .claude/.credentials.json .gemini/oauth_creds.json
    '.gemini/*credentials*.json' '.gemini/mcp-oauth-tokens*'
    # Dependency and build trees inside projects.
    node_modules .venv venv __pycache__ .mypy_cache .pytest_cache .ruff_cache .next .turbo
    # Live SQLite files: replaced by consistent copies below.
    '*.db' '*.db-wal' '*.db-shm' '*.sqlite' '*.sqlite-wal' '*.sqlite-shm' '*.sqlite3'
)

work=$(mktemp -d "${TMPDIR:-/tmp}/hermes-backup.XXXXXX")
trap 'rm -rf "$work"' EXIT
stage=$work/stage
mkdir -p "$stage"

present=()
for root in "${ROOTS[@]}"; do [ -e "$root" ] && present+=("$root"); done
exclude_args=()
for pattern in "${EXCLUDES[@]}"; do exclude_args+=("--exclude=$pattern"); done

# Large rebuildable trees never hold state worth snapshotting.
prune=()
for dir in .hermes/tools .hermes/cache .hermes/installs .codex/packages; do prune+=(-path "./$dir" -o); done
for name in node_modules .venv venv .git; do prune+=(-name "$name" -o); done
find "${present[@]/#/./}" \( "${prune[@]}" -false \) -prune -o -type f \
    \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) -print 2>/dev/null \
  | python3 -c '
import pathlib, sqlite3, sys
stage = pathlib.Path(sys.argv[1])
for line in sys.stdin:
    src = pathlib.Path(line.rstrip("\n"))
    dst = stage / src
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30) as live, sqlite3.connect(dst) as copy:
            live.backup(copy)
    except sqlite3.Error as exc:
        print(f"backup-snapshot: skipped {src}: {exc}", file=sys.stderr)
' "$stage"

{
    echo "host: $(hostname)"
    echo "taken_utc: $(date -u +%FT%TZ)"
    for git in repos/*/.git; do
        repo=${git%/.git}
        echo "repo: $repo branch=$(git -C "$repo" branch --show-current) head=$(git -C "$repo" rev-parse --short HEAD)" \
             "dirty=$(git -C "$repo" status --porcelain | wc -l)" \
             "unpushed=$(git -C "$repo" rev-list --count '@{upstream}..HEAD' 2>/dev/null || echo '?')"
    done
} > "$stage/BACKUP-MANIFEST.txt"
mkdir -p "$stage/server-etc"
cp /etc/nftables.conf /etc/ssh/sshd_config.d/*.conf "$stage/server-etc/" 2>/dev/null || true
crontab -l > "$stage/server-etc/crontab.$(id -un)" 2>/dev/null || true

# Exit 1 = a file changed while being read (live logs); the SQLite state is in the stage copy.
tar -cf "$work/backup.tar" --warning=no-file-changed --ignore-failed-read "${exclude_args[@]}" "${present[@]}" \
    || [ $? -eq 1 ]
tar -rf "$work/backup.tar" -C "$stage" .
gzip -6 -c "$work/backup.tar"
