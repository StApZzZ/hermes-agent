"""The systemd user timer behind ``hermes sync enable``: runs ``hermes sync run`` every N minutes.

Reuses the gateway's systemd plumbing (unit dir, ``systemctl --user``, WSL detection, launcher
command), so the unit starts the same Hermes the user runs, pinned to this installation's root.
"""

from __future__ import annotations

from pathlib import Path

UNIT = "hermes-sync"


def unit_texts(command: list[str], root: Path, interval_minutes: int) -> tuple[str, str]:
    """``(service, timer)`` unit file contents."""
    from hermes_cli.gateway import _systemd_command

    service = f"""[Unit]
Description=Hermes sync of settings, skills and memory with SSH hosts
After=network-online.target

[Service]
Type=oneshot
Environment={_systemd_command([f"HERMES_HOME={root}"])}
ExecStart={_systemd_command(command)}
Nice=10
"""
    timer = f"""[Unit]
Description=Run hermes sync every {interval_minutes} minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec={interval_minutes}min
RandomizedDelaySec=30
Unit={UNIT}.service

[Install]
WantedBy=timers.target
"""
    return service, timer


def _unit_paths() -> tuple[Path, Path]:
    from hermes_cli.gateway import user_systemd_unit_dir
    unit_dir = user_systemd_unit_dir()
    return unit_dir / f"{UNIT}.service", unit_dir / f"{UNIT}.timer"


def install(root: Path, interval_minutes: int) -> Path:
    """Write (or refresh) the units and start the timer; returns the timer path."""
    from hermes_cli._launchers import installation_command
    from hermes_cli.gateway import (
        PROJECT_ROOT, _preflight_user_systemd, _run_systemctl, get_python_path, supports_systemd_services)
    from utils import atomic_write_text

    if not supports_systemd_services():
        raise RuntimeError("systemd user services are not available here; run `hermes sync run` from cron instead")
    _preflight_user_systemd()
    command = installation_command(PROJECT_ROOT, ["sync", "run"], python=get_python_path(), home=root)
    service_text, timer_text = unit_texts(command, root, interval_minutes)
    service_path, timer_path = _unit_paths()
    service_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(service_path, service_text)
    atomic_write_text(timer_path, timer_text)
    _run_systemctl(["daemon-reload"], check=True)
    _run_systemctl(["enable", "--now", f"{UNIT}.timer"], check=True)
    _run_systemctl(["restart", f"{UNIT}.timer"], check=True)  # pick up a changed interval
    return timer_path


def remove() -> None:
    from hermes_cli.gateway import _run_systemctl
    service_path, timer_path = _unit_paths()
    if timer_path.exists():
        _run_systemctl(["disable", "--now", f"{UNIT}.timer"], check=False)
    for path in (timer_path, service_path):
        path.unlink(missing_ok=True)
    _run_systemctl(["daemon-reload"], check=False)


def state() -> str:
    from hermes_cli.gateway import _run_systemctl, supports_systemd_services
    if not supports_systemd_services():
        return "no systemd"
    if not _unit_paths()[1].exists():
        return "not installed"
    result = _run_systemctl(["is-active", f"{UNIT}.timer"], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", check=False)
    return (result.stdout or "").strip() or "unknown"
