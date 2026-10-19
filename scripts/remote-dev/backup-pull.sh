#!/bin/bash
# Pull a snapshot of a Hermes remote-development server into <repo>/server-backups/<alias>/.
#
#   backup-pull.sh [--force] [ssh-alias]       (default alias: hermes-dev)
#
# One <UTC stamp>.tar.gz (+ .sha256) per day: a run is skipped when today's snapshot exists
# unless --force; the newest $KEEP (default 14) are kept. The directory is gitignored and
# holds secrets (~/.hermes/.env), so it is created mode 700. Restore: see
# docs/REMOTE-DEVELOPMENT-RU.md § Резервные копии.
set -euo pipefail

force=0
if [ "${1:-}" = --force ]; then force=1; shift; fi
alias=${1:-hermes-dev}
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)/server-backups
dest=$root/$alias
keep=${KEEP:-14}

umask 077
mkdir -p "$dest"
exec > >(tee -a "$dest/backup.log") 2>&1
log() { echo "$(date -u +%FT%TZ) $*"; }

today=$(date -u +%Y%m%d)
if [ "$force" = 0 ] && compgen -G "$dest/$today-*.tar.gz" >/dev/null; then
    log "skip: snapshot for $today already exists"
    exit 0
fi

stamp=$(date -u +%Y%m%d-%H%M%S)
partial=$dest/.$stamp.partial
trap 'rm -f "$partial"' EXIT
log "start: $alias"
ssh -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15 "$alias" 'bash -s' \
    < "$here/backup-snapshot.sh" > "$partial"
gzip -t "$partial"
tar -tzf "$partial" | grep -x '\.hermes/config\.yaml' >/dev/null \
    || { log "error: archive has no .hermes/config.yaml"; exit 1; }
mv "$partial" "$dest/$stamp.tar.gz"
(cd "$dest" && sha256sum "$stamp.tar.gz" > "$stamp.tar.gz.sha256")
log "ok: $stamp.tar.gz $(du -h "$dest/$stamp.tar.gz" | cut -f1)"

mapfile -t old < <(ls -1t "$dest"/*.tar.gz | tail -n +$((keep + 1)))
for archive in "${old[@]}"; do rm -f "$archive" "$archive.sha256"; log "pruned: $(basename "$archive")"; done
