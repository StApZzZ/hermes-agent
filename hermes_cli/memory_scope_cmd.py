"""``hermes memory list|add|remove|copy|move`` — curated memory across agents, spaces and the shared
memory (``hermes_cli/memory_scopes.py``)."""

from __future__ import annotations

import sys
from typing import NoReturn

from hermes_cli import memory_scopes as ms

_NEXT_SESSION = "Agents see the change from their next session."


def _die(msg: str) -> NoReturn:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def _fill(store: ms.Store, entries: list[str]) -> str:
    return f"{ms.usage(entries):,}/{store.limit:,} chars"


def _overview() -> None:
    print(f"\n {'Store':<28} {'Entries':>7}  Size")
    print(f" {'─' * 28} {'─' * 7}  {'─' * 18}")
    for store in ms.all_stores():
        entries = ms.read_entries(store)
        print(f" {store.label:<28} {len(entries):>7}  {_fill(store, entries)}")
    print("\nEntries:  hermes memory list <store>     Copy/move:  hermes memory copy|move <from> <to>")
    print("Stores:   <profile>[:memory|:user], space:<name>, shared\n")


def _memory_list(args) -> None:
    if not args.store:
        _overview()
        return
    for store in ms.resolve(args.store):
        entries = ms.read_entries(store)
        print(f"\n{store.label} — {len(entries)} entries, {_fill(store, entries)}")
        for i, entry in enumerate(entries, 1):
            print(f"{i:>3}. {entry}")
    print()


def _writable(spec: str) -> ms.Store:
    """A whole profile means its MEMORY.md for single-entry edits."""
    stores = ms.resolve(spec)
    return next((s for s in stores if s.kind == "memory"), stores[0]) if len(stores) > 1 else stores[0]


def _memory_add(args) -> None:
    store = _writable(args.store)
    added = ms.add_entries(store, [" ".join(args.text)])
    print(f"✓ Saved to {store.label}." if added else f"{store.label} already holds that entry.")
    print(_NEXT_SESSION)


def _memory_remove(args) -> None:
    store = ms.resolve_one(args.store)
    doomed = ms.select(ms.read_entries(store), numbers=args.numbers)
    ms.remove_entries(store, doomed)
    for entry in doomed:
        print(f"✓ Removed from {store.label}: {entry}")


def _transfer(args, *, move: bool) -> None:
    plans = ms.plan_transfer(args.source, args.dest, numbers=args.entries, match=args.match or None)
    if not plans:
        print("Nothing to transfer: no entries matched.")
        return
    verb = "Move" if move else "Copy"
    for plan in plans:
        print(f"\n{verb} {len(plan.entries)} entr{'y' if len(plan.entries) == 1 else 'ies'}: "
              f"{plan.source.label} → {plan.dest.label}")
        for entry in plan.entries:
            print(f"  • {entry}")
    if args.dry_run:
        print("\n(dry run — nothing changed)")
        return
    ms.apply_transfer(plans, move=move)
    added = sum(len(p.added) for p in plans)
    total = sum(len(p.entries) for p in plans)
    skipped = f" ({total - added} already there)" if added < total else ""
    print(f"\n✓ {'Moved' if move else 'Copied'} {total} entr{'y' if total == 1 else 'ies'}{skipped}. {_NEXT_SESSION}")


_ACTIONS = {
    "list": _memory_list, "ls": _memory_list, "add": _memory_add, "remove": _memory_remove,
    "copy": lambda args: _transfer(args, move=False), "move": lambda args: _transfer(args, move=True),
}


def cmd_memory_scope(args) -> None:
    try:
        _ACTIONS[args.memory_command](args)
    except (ValueError, KeyError, IndexError, FileNotFoundError, OSError) as exc:
        _die(exc.args[0] if exc.args else str(exc))
