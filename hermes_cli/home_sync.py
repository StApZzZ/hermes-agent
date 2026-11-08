"""``hermes sync``: keep settings, skills and memory in step between this machine and SSH hosts.

Facade: CLI actions and the SSH peer. The scope lives in ``home_sync_scope``, the merge rules in
``home_sync_merge``, one run in ``home_sync_engine``, per-side reads/writes and the remote agent in
``home_sync_store``, the timer in ``home_sync_timer``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from hermes_cli.home_sync_store import REPLY_MARKER, StaleError, SyncError

# The peer runs the same command on the host. Non-interactive SSH shells may lack ~/.local/bin on
# PATH, where the installer puts the launcher.
_REMOTE_AGENT = ('if command -v hermes >/dev/null 2>&1; then exec hermes sync agent; fi; '
                 'exec "$HOME/.local/bin/hermes" sync agent')
_SSH_TIMEOUT_SECONDS = 300


def _root() -> Path:
    from hermes_constants import get_default_hermes_root
    return get_default_hermes_root()


def _settings() -> dict:
    from hermes_cli.config import load_config
    return load_config().get("sync") or {}


def parse_reply(stdout: str, stderr: str, returncode: int, host: str) -> dict:
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(REPLY_MARKER):
            reply = json.loads(line[len(REPLY_MARKER):])
            if reply.get("ok"):
                return reply
            raise (StaleError if reply.get("stale") else SyncError)(f"{host}: {reply.get('error')}")
    tail = ((stderr or "").strip().splitlines() or [""])[-1]
    raise SyncError(f"{host}: no reply from `hermes sync agent` (ssh exit {returncode})"
                    + (f": {tail}" if tail else "")
                    + ". The host needs SSH access without a prompt and a Hermes with `hermes sync`"
                      " (run `hermes update` there).")


class SshPeer:
    """The remote side, one ``ssh HOST hermes sync agent`` per request."""

    def __init__(self, host: str):
        from hermes_cli.subcommands.remote import _checked_host
        self.host = _checked_host(host)

    def call(self, request: dict) -> dict:
        from hermes_cli.subcommands.remote import _ssh_argv
        proc = subprocess.run(_ssh_argv(self.host, _REMOTE_AGENT, batch=True), input=json.dumps(request),
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=_SSH_TIMEOUT_SECONDS, check=False)
        return parse_reply(proc.stdout, proc.stderr, proc.returncode, self.host)


def sync_host(host: str, *, dry_run: bool = False, peer=None):
    """One sync with ``host``; serialized per host so a timer run never overlaps a manual one."""
    from hermes_cli.auth import _file_lock
    from hermes_cli.home_sync_engine import sync_with

    root, settings = _root(), _settings()
    peer = peer or SshPeer(host)
    with _file_lock(root / "sync" / host / ".lock", threading.local(), 2.0,
                    f"another `hermes sync` with {host} is running"):
        return sync_with(peer, host, root, dry_run=dry_run, profiles=tuple(settings.get("profiles") or ()),
                         extra_local_keys=tuple(settings.get("local_keys") or ()))


def _print_report(report) -> None:
    verb = "would " if report.dry_run else ""
    rows = (("→ " + verb + "send", report.pushed), ("← " + verb + "get", report.pulled),
            ("⇄ " + verb + "merge", report.merged), ("✗ " + verb + "delete here", report.deleted_local),
            ("✗ " + verb + f"delete on {report.host}", report.deleted_remote))
    if not report.changed and not report.skipped:
        print(f"{report.host}: in sync")
    for label, paths in rows:
        for rel in paths:
            print(f"{report.host}: {label} {rel}")
    for line in report.conflicts:
        print(f"{report.host}: conflict — {line}")
    if report.conflict_dir:
        print(f"{report.host}: the other copies are in {report.conflict_dir}")
    for line in report.skipped:
        print(f"{report.host}: skipped — {line}")


def _sync_run(args) -> int:
    hosts = list(args.hosts or _settings().get("hosts") or [])
    if not hosts:
        print("No hosts to sync. Run `hermes sync run HOST`, or `hermes sync enable HOST` to sync on a timer.")
        return 2
    failed = 0
    for host in hosts:
        try:
            _print_report(sync_host(host, dry_run=args.dry_run))
        except (SyncError, TimeoutError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            print(f"{host}: sync failed — {exc}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


def _update_settings(mutate) -> dict:
    from hermes_cli.config import atomic_config_write, get_config_path, read_user_config_raw
    path = get_config_path()
    raw = read_user_config_raw(path)
    section = dict(raw.get("sync") or {})
    mutate(section)
    atomic_config_write(path, {**raw, "sync": section})
    return section


def _sync_enable(args) -> int:
    from hermes_cli import home_sync_timer
    from hermes_cli.subcommands.remote import _checked_host

    host = _checked_host(args.host)

    def _add(section: dict) -> None:
        section["hosts"] = [*[h for h in section.get("hosts") or [] if h != host], host]
        if args.every:
            section["interval_minutes"] = args.every

    section = _update_settings(_add)
    interval = int(section.get("interval_minutes") or _settings().get("interval_minutes") or 10)
    first = _sync_run(argparse.Namespace(hosts=[host], dry_run=False))
    timer = home_sync_timer.install(_root(), interval)
    print(f"Syncing with {', '.join(section['hosts'])} every {interval} min ({timer}).")
    return first


def _sync_disable(args) -> int:
    from hermes_cli import home_sync_timer

    section = _update_settings(lambda s: s.update(hosts=[h for h in s.get("hosts") or [] if h != args.host]))
    if section["hosts"]:
        print(f"Stopped syncing with {args.host}; still syncing with {', '.join(section['hosts'])}.")
    else:
        home_sync_timer.remove()
        print(f"Stopped syncing with {args.host}; the timer is removed.")
    return 0


def _sync_status(_args) -> int:
    from hermes_cli import home_sync_timer
    from hermes_cli.home_sync_engine import load_state

    root, settings = _root(), _settings()
    hosts = list(settings.get("hosts") or [])
    known = sorted({p.name for p in (root / "sync").glob("*") if p.is_dir()} | set(hosts))
    print(f"Timer: {home_sync_timer.state()} (every {settings.get('interval_minutes', 10)} min)")
    print(f"Profiles: default{''.join(f', {p}' for p in settings.get('profiles') or [])} (sync.profiles)")
    for host in known:
        synced_at = load_state(root, host).get("synced_at")
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(synced_at)) if synced_at else "never"
        print(f"  {host}: {'on timer' if host in hosts else 'manual'}, last sync {when}")
    if not known:
        print("  No hosts yet: `hermes sync enable HOST`.")
    return 0


def _sync_agent(_args) -> int:
    from hermes_cli.home_sync_store import serve_agent
    return serve_agent(_root())


_ACTIONS = {None: _sync_status, "status": _sync_status, "run": _sync_run, "enable": _sync_enable,
            "disable": _sync_disable, "agent": _sync_agent}


def cmd_sync(args) -> None:
    try:
        code = _ACTIONS[getattr(args, "sync_action", None)](args)
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"hermes sync: {exc}", file=sys.stderr)
        code = 2
    if code:
        sys.exit(code)
