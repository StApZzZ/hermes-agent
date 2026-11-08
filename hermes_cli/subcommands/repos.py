"""``hermes repos`` subcommand parser."""

from __future__ import annotations

import argparse


def build_repos_parser(subparsers) -> None:
    """Attach the ``repos`` subcommand to ``subparsers``."""
    parser = subparsers.add_parser(
        "repos", help="Set Hermes up with the MCP servers, skills and CLI tools your repositories ship",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Find what your own repositories offer Hermes and set it up: MCP servers they ship, "
                    "their project skills (.hermes/, .agents/, .claude/skills), skill packs, and console "
                    "tools (as a generated skill). Nothing in a repository is run.\n\n"
                    "  hermes repos scan              repositories next to the current one\n"
                    "  hermes repos scan ~/mcp-repos  repositories under a folder\n"
                    "  hermes repos apply             set up what scan shows (--only NAME,...)\n\n"
                    "Paths are this machine's: none of it travels with `hermes sync`.")
    sub = parser.add_subparsers(dest="repos_action")
    for action, text in (("scan", "Show what would be set up (default)"),
                         ("apply", "Set it up; a re-run updates what this command added")):
        p = sub.add_parser(action, help=text)
        p.add_argument("roots", nargs="*", help="Folders holding repositories (default: the parent of this repo)")
        if action == "apply":
            p.add_argument("--only", help="Comma-separated repository folder names")

    def _run(args):
        from hermes_cli.repo_tools import cmd_repos
        if not getattr(args, "repos_action", None):
            args.repos_action, args.roots = "scan", []
        return cmd_repos(args)

    parser.set_defaults(func=_run)
