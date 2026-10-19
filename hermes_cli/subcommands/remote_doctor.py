"""Read-only Linux probe, executed on the SSH host, never locally."""

import shlex


def doctor_script(workspace: str) -> str:
    return 'export PATH="$HOME/.local/bin:$PATH"\nworkspace=' + shlex.quote(workspace) + '\n' + r'''
status=0
fail() { printf 'FAIL: %s\n' "$1"; status=1; }
warn() { printf 'WARN: %s\n' "$1"; }
printf 'SSH: OK\nHost: '; hostname
printf 'Workspace: %s\n' "$workspace"
if [ -d "$workspace" ] && [ -r "$workspace" ] && [ -x "$workspace" ]; then
  printf 'Workspace: OK\n'
  [ -w "$workspace" ] || fail 'Workspace is read-only; ask its owner for write access.'
else
  fail 'Workspace missing or inaccessible; create/clone it on the server and check permissions.'
fi
for tool in git claude hermes tmux; do
  if command -v "$tool" >/dev/null 2>&1; then
    printf '%s: OK (%s)\n' "$tool" "$(command -v "$tool")"
  else
    case "$tool" in
      git|tmux) fail "$tool missing; install it with your Linux package manager." ;;
      claude) fail 'Claude Code missing; install its native Linux release and sign in with claude.' ;;
      hermes) fail 'Hermes missing; run the official Linux installer and add its launcher to PATH.' ;;
    esac
  fi
done
if command -v hermes >/dev/null 2>&1; then
  if hermes acp --check; then printf 'ACP runtime: OK\n'
  else fail 'Hermes ACP unavailable; repair/install its ACP dependencies using Hermes PM.'; fi
fi
if command -v python3 >/dev/null 2>&1; then python3 --version
else warn 'No system python3; Hermes must supply its managed Python runtime.'; fi
if command -v node >/dev/null 2>&1; then node --version
else printf 'Node: absent (not needed by native Claude Code; required by npm installs)\n'; fi
printf '\nMemory and swap (host view):\n'
if [ -r /proc/meminfo ]; then
  awk '/^(MemTotal|MemAvailable|SwapTotal|SwapFree):/ { print }' /proc/meminfo
  available=$(awk '/^MemAvailable:/ { print $2 }' /proc/meminfo)
  swap=$(awk '/^SwapTotal:/ { print $2 }' /proc/meminfo)
  if [ -n "$available" ] && [ "$available" -lt 1048576 ]; then
    fail 'Less than 1 GiB RAM available; stop unused jobs or enlarge the server/container memory limit.'
  fi
  if [ "${swap:-0}" -eq 0 ]; then warn 'No swap; consider swap on the Linux server if memory pressure occurs.'; fi
else fail 'Cannot read /proc/meminfo; Linux is required for this workflow.'; fi
printf '\nDisk:\n'
disk="$workspace"
[ -d "$disk" ] || disk="$HOME"
if df -Pk "$disk"; then
  available=$(df -Pk "$disk" | awk 'END { print $4 }')
  if [ -n "$available" ] && [ "$available" -lt 2097152 ]; then
    fail 'Less than 2 GiB disk free; clear remote caches or enlarge the project volume.'
  fi
else fail 'Cannot inspect workspace filesystem with df.'; fi
printf '\nInodes:\n'
df -Pi "$disk" || warn 'Inode statistics unavailable.'
printf '\nProcess limits:\n'
ulimit -a
printf '\nCgroup limits (bytes; max/-1 means unlimited):\n'
for file in /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.swap.max /sys/fs/cgroup/pids.max /sys/fs/cgroup/pids.current /sys/fs/cgroup/memory/memory.limit_in_bytes; do
  if [ -r "$file" ]; then
    value=$(cat "$file")
    printf '%s: %s\n' "$file" "$value"
    case "$file:$value" in
      */memory.max:max) ;;
      */memory.max:*)
        if [ "$value" -lt 1073741824 ] 2>/dev/null; then
          fail 'Cgroup memory limit is below 1 GiB; increase the container/service memory limit.'
        fi ;;
    esac
  fi
done
exit "$status"
'''
