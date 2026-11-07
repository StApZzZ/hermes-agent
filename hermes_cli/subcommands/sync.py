"""``hermes sync`` subcommand parser."""

from __future__ import annotations

import argparse


def build_sync_parser(subparsers) -> None:
    """Attach the ``sync`` subcommand to ``subparsers``."""
    from hermes_cli.home_sync import cmd_sync

    parser = subparsers.add_parser(
        "sync", help="Keep settings, skills and memory in step with Hermes on SSH hosts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Two-way sync of config.yaml, SOUL.md, skills, memory (MEMORY.md, USER.md) and spaces, "
                    "for every profile, with Hermes on an SSH host.\n"
                    "Secrets (.env, auth.json), sessions and logs stay on each machine, and so do "
                    "machine settings in config.yaml (terminal, gateway and messaging, paths).\n\n"
                    "  hermes sync run hermes-dev        sync now (--dry-run: only show)\n"
                    "  hermes sync enable hermes-dev     sync every 10 min (--every N)\n"
                    "  hermes sync status                hosts, last sync, timer\n\n"
                    "Edited on both sides: config keys and memory entries merge; for other files the "
                    "newer copy wins and the other is kept under sync/<host>/conflicts/.")
    sub = parser.add_subparsers(dest="sync_action")

    run = sub.add_parser("run", help="Sync now with HOST (default: the hosts in sync.hosts)")
    run.add_argument("hosts", nargs="*", metavar="HOST", help="SSH config alias")
    run.add_argument("--dry-run", action="store_true", help="Show what would change, change nothing")

    enable = sub.add_parser("enable", help="Sync with HOST now and then on a systemd timer")
    enable.add_argument("host", metavar="HOST", help="SSH config alias")
    enable.add_argument("--every", type=int, default=None, metavar="MINUTES",
                        help="Timer period (default: sync.interval_minutes, 10)")

    disable = sub.add_parser("disable", help="Stop syncing with HOST on the timer")
    disable.add_argument("host", metavar="HOST")

    sub.add_parser("status", help="Show synced hosts, last sync and the timer")
    sub.add_parser("agent", help=argparse.SUPPRESS)  # the remote end, run over SSH

    parser.set_defaults(func=cmd_sync)
