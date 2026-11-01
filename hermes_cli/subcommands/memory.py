"""``hermes memory`` subcommand parser."""

from __future__ import annotations

import argparse

from typing import Callable

from hermes_cli.subcommands._shared import add_yes_flag


def build_memory_parser(subparsers, *, cmd_memory: Callable) -> None:
    """Attach the ``memory`` subcommand to ``subparsers``."""
    memory_parser = subparsers.add_parser(
        "memory", help="Manage memory: shared memory, copy/move between agents, external providers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Curated memory across agents and spaces, and external memory provider plugins.\n\n"
            "Stores: <profile> (MEMORY.md + USER.md), <profile>:memory, <profile>:user,\n"
            "space:<name> (the space's notes) and shared (read by every agent).\n"
            "  hermes memory list                      every store with its size\n"
            "  hermes memory add shared \"<fact>\"       a fact all your agents know\n"
            "  hermes memory copy work personal --match Alex\n"
            "  hermes memory move work:user shared --entries 1,3-5\n\n"
            "Bundled providers: retaindb, byterover.\n"
            "Catalog providers (e.g. honcho, hindsight, supermemory,\n"
            "mem0, openviking, holographic): hermes plugins install <name>.\n\n"
            "Only one external provider can be active at a time.\n"
            "Built-in memory (MEMORY.md/USER.md) is always active.")
    memory_sub = memory_parser.add_subparsers(dest="memory_command")
    _setup_parser = memory_sub.add_parser(
        "setup", help="Interactive provider selection and configuration")
    _setup_parser.add_argument(
        "provider", nargs="?", default=None,
        help="Provider to configure directly (e.g. honcho), skipping the picker")
    memory_sub.add_parser("status", help="Show current memory provider config")
    memory_sub.add_parser("off", help="Disable external provider (built-in only)")
    _reset_parser = memory_sub.add_parser(
        "reset", help="Erase all built-in memory (MEMORY.md and USER.md)")
    add_yes_flag(_reset_parser)
    _reset_parser.add_argument(
        "--target", choices=["all", "memory", "user"], default="all",
        help="Which store to reset: 'all' (default), 'memory', or 'user'")
    _add_store_commands(memory_sub)
    memory_parser.set_defaults(func=cmd_memory)


def _add_store_commands(memory_sub) -> None:
    store_help = "<profile>[:memory|:user], space:<name> or shared"
    list_parser = memory_sub.add_parser(
        "list", aliases=["ls"], help="List memory stores, or the numbered entries of one")
    list_parser.add_argument("store", nargs="?", default=None, help=store_help)
    add_parser = memory_sub.add_parser("add", help="Add an entry to a store (a bare profile means its MEMORY.md)")
    add_parser.add_argument("store", help=store_help)
    add_parser.add_argument("text", nargs="+")
    remove_parser = memory_sub.add_parser("remove", help="Remove numbered entries from a store")
    remove_parser.add_argument("store", help=store_help)
    remove_parser.add_argument("numbers", help="Entry numbers from `hermes memory list <store>`, e.g. 1,3-5")
    for verb, what in (("copy", "Copy"), ("move", "Move")):
        transfer = memory_sub.add_parser(
            verb, help=f"{what} all or some entries to another agent, a space or the shared memory")
        transfer.add_argument("source", help=store_help)
        transfer.add_argument("dest", help=store_help + " (a whole profile keeps MEMORY/USER apart)")
        pick = transfer.add_mutually_exclusive_group()
        pick.add_argument("--entries", default=None, metavar="N",
                          help="Only these entry numbers (needs a single source store), e.g. 1,3-5")
        pick.add_argument("--match", action="append", default=[], metavar="TEXT",
                          help="Only entries containing TEXT (case-insensitive, repeatable)")
        transfer.add_argument("--dry-run", action="store_true", help="Show what would change, change nothing")
