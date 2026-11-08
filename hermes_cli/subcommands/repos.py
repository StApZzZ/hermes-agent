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
                    "  hermes repos scan                         projects next to the current one\n"
                    "  hermes repos scan ~/mcp-repos ~/work      projects under these folders\n"
                    "  hermes repos scan ~/repos/maps-parser     one project\n"
                    "  hermes repos scan --only my-vpn,maps-parser --kind mcp,skills\n"
                    "  hermes repos apply [same arguments]       set up what scan shows\n\n"
                    "Paths are this machine's: none of it travels with `hermes sync`.")
    sub = parser.add_subparsers(dest="repos_action")
    for action, text in (("scan", "Show what would be set up (default)"),
                         ("apply", "Set it up; a re-run updates what this command added")):
        p = sub.add_parser(action, help=text)
        p.add_argument("roots", nargs="*", metavar="PATH",
                       help="A project, or a folder of projects (default: the folder holding this project)")
        p.add_argument("--only", metavar="NAME,...", help="Only these project folder names")
        p.add_argument("--kind", metavar="KIND,...", help="Only these kinds: mcp, skills, tools (default: all)")

    def _run(args):
        from hermes_cli.repo_tools import cmd_repos
        if not getattr(args, "repos_action", None):
            args.repos_action, args.roots, args.only, args.kind = "scan", [], None, None
        return cmd_repos(args)

    parser.set_defaults(func=_run)
