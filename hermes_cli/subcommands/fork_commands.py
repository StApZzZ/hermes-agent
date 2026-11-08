"""The fork's own top-level subcommands, attached from one place so ``main.py`` (over its size cap)
does not grow with each of them."""

from __future__ import annotations


def build_fork_parsers(subparsers) -> None:
    from hermes_cli.subcommands.remote import build_remote_parser
    from hermes_cli.subcommands.repos import build_repos_parser
    from hermes_cli.subcommands.space import build_space_parser
    from hermes_cli.subcommands.sync import build_sync_parser

    build_remote_parser(subparsers)
    build_sync_parser(subparsers)
    build_space_parser(subparsers)
    build_repos_parser(subparsers)
