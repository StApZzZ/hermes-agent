"""``hermes space`` subcommand parser."""

from __future__ import annotations

import argparse


def build_space_parser(subparsers) -> None:
    """Attach the ``space`` subcommand to ``subparsers``."""
    from hermes_cli.space_cmd import cmd_space
    from hermes_cli.spaces import SPACE_KINDS

    parser = subparsers.add_parser(
        "space", help="Switch between spaces (work, personal, projects) — groups of agents with their own memory",
        description="A space groups agents (profiles) for one area of your life — work, personal, a project. "
                    "Each agent keeps its own memory and sessions; the space adds shared notes, folder "
                    "binding (`hermes` inside a bound folder runs the space's agent) and `/space` switching "
                    "in chats.")
    sub = parser.add_subparsers(dest="space_action")

    sub.add_parser("list", aliases=["ls"], help="List spaces and their agents")
    sub.add_parser("current", help="Show the space this shell runs in")

    create = sub.add_parser("create", help="Create a space")
    create.add_argument("name", help="Space name (lowercase, e.g. work, personal, acme)")
    create.add_argument("--kind", choices=SPACE_KINDS, default=None,
                        help="work | personal | project | other (default: the name when it is a kind, else other)")
    create.add_argument("--title", default="", help="Display title, e.g. 'Работа'")
    create.add_argument("--agent", action="append", default=[], metavar="PROFILE",
                        help="Existing profile to put in the space (repeatable; the first is primary)")
    create.add_argument("--new-agent", nargs="?", const="", default=None, metavar="PROFILE",
                        help="Also create a new profile for the space (default name: the space name)")
    create.add_argument("--clone-from", default=None, metavar="PROFILE",
                        help="With --new-agent: copy config, keys, SOUL.md and skills from this profile "
                             "(memory starts empty unless --keep-memory)")
    create.add_argument("--keep-memory", action="store_true",
                        help="With --clone-from: keep the source's MEMORY.md/USER.md")
    create.add_argument("--path", action="append", default=[], metavar="DIR",
                        help="Bind a folder to the space (repeatable)")

    use = sub.add_parser("use", help="Make a space's agent the default for plain `hermes` commands")
    use.add_argument("name", help="Space name, or 'default' to leave spaces")
    use.add_argument("agent", nargs="?", default=None, help="Agent of the space (default: its primary)")

    show = sub.add_parser("show", help="Show one space")
    show.add_argument("name")

    edit = sub.add_parser("set", help="Change a space's kind, title or shared-memory access")
    edit.add_argument("name")
    edit.add_argument("--kind", choices=SPACE_KINDS, default=None)
    edit.add_argument("--title", default=None)
    edit.add_argument("--shared-memory", action=argparse.BooleanOptionalAction, default=None,
                      help="Whether the space's agents read the shared memory (default: yes; turn it off "
                           "for a bot other people talk to)")

    add = sub.add_parser("add", help="Put an agent into a space (moves it out of its previous space)")
    add.add_argument("name", help="Space name")
    add.add_argument("profile", help="Profile to add")
    add.add_argument("--primary", action="store_true", help="Make it the space's primary agent")

    remove = sub.add_parser("remove", help="Take an agent out of its space")
    remove.add_argument("profile")

    bind = sub.add_parser("bind", help="Bind a folder: `hermes` launched inside it runs the space's agent")
    bind.add_argument("name")
    bind.add_argument("path", nargs="?", default=".", help="Folder (default: current directory)")

    unbind = sub.add_parser("unbind", help="Unbind a folder from a space")
    unbind.add_argument("name")
    unbind.add_argument("path", nargs="?", default=".")

    note = sub.add_parser("note", help="Save a shared note every agent of the space reads at session start")
    note.add_argument("name")
    note.add_argument("text", nargs="+")

    notes = sub.add_parser("notes", help="List a space's shared notes")
    notes.add_argument("name")
    notes.add_argument("--remove", type=int, default=None, metavar="N", help="Remove note #N")

    delete = sub.add_parser("delete", help="Delete a space (its agents/profiles are kept)")
    delete.add_argument("name")
    delete.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    parser.set_defaults(func=cmd_space)
