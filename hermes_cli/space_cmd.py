"""``hermes space`` — create, inspect and switch spaces (``hermes_cli/spaces.py``)."""

from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path
from typing import NoReturn

from hermes_cli import spaces as sp


def _die(msg: str) -> NoReturn:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def _agents_label(space: sp.Space) -> str:
    if not space.agents:
        return "—"
    return ", ".join([f"{space.agents[0]}*", *space.agents[1:]])


def _current_profile() -> str:
    from hermes_cli.profiles import get_active_profile_name
    return get_active_profile_name()


def _space_list(_args) -> None:
    spaces = sp.list_spaces()
    if not spaces:
        print("No spaces yet. Create one:  hermes space create work --kind work --new-agent")
        return
    current = sp.space_of_profile(_current_profile())
    print(f"\n {'Space':<24} {'Kind':<10} {'Agents (* primary)':<30} Folders")
    print(f" {'─' * 23}  {'─' * 9}  {'─' * 29}  {'─' * 20}")
    for space in spaces:
        marker = "◆" if current and current.name == space.name else " "
        folders = ", ".join(space.paths) or "—"
        print(f"{marker}{space.label:<24} {space.kind:<10} {_agents_label(space):<30} {folders}")
    print("\nSwitch:  hermes space use <name>     In a chat:  /space <name>\n")


def _space_current(_args) -> None:
    profile = _current_profile()
    space = sp.space_of_profile(profile)
    bound = sp.space_for_path(Path.cwd())
    if space is None:
        print(f"Agent '{profile}' is not in a space.")
    else:
        print(f"Space: {space.label} [{space.kind}] — agent '{profile}'")
    if bound is not None:
        print(f"This folder is bound to space '{bound.name}': `hermes` here runs '{bound.primary or '(no agent yet)'}'.")


def _print_space(space: sp.Space) -> None:
    print(f"\nSpace:   {space.label}")
    print(f"Kind:    {space.kind}")
    print(f"Agents:  {_agents_label(space)}")
    print(f"Folders: {', '.join(space.paths) or '—'}")
    notes = sp.read_notes(space.name)
    print(f"Notes:   {len(notes)} shared note(s) — hermes space notes {space.name}\n")


def _create_agent(space_name: str, args) -> str:
    """Create the space's own profile through the regular ``profile create`` flow."""
    from hermes_cli.profile_cmd import _profile_create
    name = args.new_agent or space_name
    _profile_create(Namespace(
        profile_name=name, clone=False, clone_all=False, clone_from=args.clone_from, no_alias=False,
        no_skills=False, clone_channels=False, sync_imports=False,
        description=f"Agent of the {space_name} space"))
    if args.clone_from and not args.keep_memory:
        # A clone copies the source's curated memory; a new space starts with its own.
        from hermes_cli.profiles import get_profile_dir
        for fname in ("MEMORY.md", "USER.md"):
            (get_profile_dir(name) / "memories" / fname).unlink(missing_ok=True)
        print(f"Memory of '{name}' starts empty (cloned config only; --keep-memory keeps it).")
    return name


def _space_create(args) -> None:
    if args.clone_from and args.new_agent is None:
        _die("--clone-from needs --new-agent.")
    name = sp.normalize_space_name(args.name)
    kind = args.kind or (name if name in sp.SPACE_KINDS else "other")
    space = sp.create_space(name, kind=kind, title=args.title)
    agents = list(args.agent)
    if args.new_agent is not None:
        agents.insert(0, _create_agent(space.name, args))
    for i, agent in enumerate(agents):
        sp.add_agent(space.name, agent, primary=i == 0)
    for path in args.path:
        sp.bind_path(space.name, Path(path))
    print(f"✓ Space '{space.name}' created.")
    _print_space(sp.get_space(space.name))
    if not agents:
        print(f"Add an agent:  hermes space add {space.name} <profile>   (or create one: --new-agent)")


def _space_use(args) -> None:
    from hermes_cli.profiles import set_active_profile
    if args.name == "default":
        set_active_profile("default")
        print("Switched to the default profile (no space).")
        return
    space = sp.get_space(args.name)
    agent = args.agent or space.primary
    if agent is None:
        _die(f"Space '{space.name}' has no agents. Add one: hermes space add {space.name} <profile>")
    if agent not in space.agents:
        _die(f"'{agent}' is not an agent of space '{space.name}' ({_agents_label(space)}).")
    set_active_profile(agent)
    print(f"Switched to space {space.label} — `hermes` now runs agent '{agent}'.")
    print("Folders bound to another space still pick that space's agent.")


def _space_show(args) -> None:
    _print_space(sp.get_space(args.name))


def _space_set(args) -> None:
    _print_space(sp.update_space(args.name, kind=args.kind, title=args.title))


def _space_add(args) -> None:
    previous = sp.space_of_profile(args.profile)
    space = sp.add_agent(args.name, args.profile, primary=args.primary)
    moved = f" (moved from '{previous.name}')" if previous and previous.name != space.name else ""
    print(f"✓ '{args.profile}' is in space '{space.name}'{moved}. Agents: {_agents_label(space)}")


def _space_remove(args) -> None:
    left = sp.remove_agent(args.profile)
    print(f"✓ '{args.profile}' left space '{left.name}'." if left else f"'{args.profile}' was not in a space.")


def _space_bind(args) -> None:
    space = sp.bind_path(args.name, Path(args.path))
    print(f"✓ Bound. `hermes` inside {space.paths[-1]} now runs '{space.primary or '(no agent yet)'}'.")


def _space_unbind(args) -> None:
    sp.unbind_path(args.name, Path(args.path))
    print(f"✓ Unbound {Path(args.path).expanduser().resolve()} from '{args.name}'.")


def _space_note(args) -> None:
    notes = sp.add_note(args.name, " ".join(args.text))
    print(f"✓ Saved. Space '{args.name}' has {len(notes)} shared note(s); agents see it from their next session.")


def _space_notes(args) -> None:
    if args.remove is not None:
        print(f"✓ Removed: {sp.remove_note(args.name, args.remove)}")
        return
    notes = sp.read_notes(sp.get_space(args.name).name)
    if not notes:
        print(f"No shared notes. Add one: hermes space note {args.name} \"...\"")
    for i, note in enumerate(notes, 1):
        print(f"{i:>3}. {note}")


def _space_delete(args) -> None:
    space = sp.get_space(args.name)
    if not args.yes:
        answer = input(f"Delete space '{space.name}' and its shared notes? Agents stay as plain profiles. [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return
    sp.delete_space(space.name)
    print(f"✓ Space '{space.name}' deleted; agents kept: {_agents_label(space)}")


_ACTIONS = {
    None: _space_list, "list": _space_list, "ls": _space_list, "current": _space_current,
    "create": _space_create, "use": _space_use, "show": _space_show, "set": _space_set,
    "add": _space_add, "remove": _space_remove, "bind": _space_bind, "unbind": _space_unbind,
    "note": _space_note, "notes": _space_notes, "delete": _space_delete,
}


def cmd_space(args) -> None:
    try:
        _ACTIONS[getattr(args, "space_action", None)](args)
    except (ValueError, KeyError, IndexError, FileExistsError, FileNotFoundError) as exc:
        _die(exc.args[0] if exc.args else str(exc))
