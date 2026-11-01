---
sidebar_position: 3
---

# Spaces: Work, Personal and Projects

A **space** groups the agents you use for one area of your life — work, personal, a project — and
lets you switch between those areas in one step, from the terminal or from a chat.

Spaces sit on top of [profiles](./profiles.md). Every agent in a space is still its own profile,
with its own `config.yaml`, keys, `SOUL.md`, `MEMORY.md`/`USER.md`, sessions and skills. A space
never merges two agents' memory. It adds:

- **Switching**: `hermes space use work` makes the work space's agent the default for plain
  `hermes` commands; `/space work` does the same for your messages in a chat.
- **Folder binding**: `hermes` (or `hermes chat`) launched inside a bound folder runs that space's
  agent, so a project repo always opens with the project's agent and memory.
- **Shared space notes**: short facts every agent of the space reads at the start of each session
  (`hermes space note work "…"`).
- **Grouping**: `hermes space list` shows which agents belong where, with a primary agent per space.

Next to spaces there is a **shared memory** every agent reads, and `hermes memory copy` /
`hermes memory move` carry all or part of one agent's memory to another agent, a space or the
shared memory (see [Shared memory](#shared-memory) and [Moving memory](#moving-memory)).

## Quick start

```bash
# A work space with its own new agent, keys and settings copied from your current profile,
# memory starting empty
hermes space create work --title "Work" --new-agent --clone-from default

# A personal space around the profile you already use
hermes space create personal --agent default

# A project space with its own agent, bound to the repo
hermes space create acme --kind project --new-agent acme --clone-from work --path ~/repos/acme

hermes space list
```

```
 Space                    Kind       Agents (* primary)             Folders
 ───────────────────────  ─────────  ─────────────────────────────  ────────────────────
 Work (work)              work       work*                          —
 personal                 personal   default*                       —
 acme                     project    acme*                          /home/you/repos/acme
```

`--kind` defaults to the space name when the name is itself a kind (`work`, `personal`,
`project`), else `other`.

## Switching

| Where | Command | Effect |
|-------|---------|--------|
| Terminal | `hermes space use work` | Plain `hermes` commands now run the work space's primary agent (same as `hermes profile use work`). `hermes space use default` leaves spaces. |
| Terminal | `cd ~/repos/acme && hermes` | A bound folder picks its space's agent for agent sessions (`hermes`, `hermes chat`). It beats the sticky default; `-p` beats both. |
| Inside a CLI session | `/space work` | Saves the choice for the **next** session. The running session keeps its agent, because switching would rebuild its system prompt. |
| Chat (Telegram, Discord, …) | `/space work` | Your next message runs under the work space's agent, with that agent's memory and its own conversation in this chat. `/space work coder` picks a specific agent; `/space off` returns to the chat's default agent. `/space` alone lists the spaces. |

Folder binding applies only to agent sessions. Management commands such as
`hermes gateway restart` or `hermes profile list` keep following `-p` and the sticky default, so
running them inside a project folder never re-targets another profile.

### Switching in a chat

Chat switching needs a [multiplexed gateway](./multi-profile-gateways.md) that serves every agent
you switch to (the default when you have two or more profiles). The choice is stored per
**sender, chat and receiving bot**: switching your lane never moves another person's messages in
a shared group. Each space keeps its own conversation in the same chat, so `/space personal` and
back to `/space work` resumes where the work conversation left off.

Replies keep coming from the bot you wrote to. If an agent is later deleted or no longer served,
the selection is ignored and the chat falls back to its normal routing.

:::caution Who may switch
On a bot several people can use, any allowed user can switch their own lane to any space you
created. Restrict the command with the gateway's
[admin/user split](../reference/slash-commands.md#permissions-and-adminuser-split) — leave
`space` out of `user_allowed_commands` so only admins can switch.
:::

## Agents in a space

```bash
hermes space add work coder            # put an existing profile into a space
hermes space add work coder --primary  # ...as the agent /space and `space use` pick
hermes space remove coder              # take it out (the profile stays)
```

An agent belongs to one space at a time; `add` moves it out of its previous space. Membership is
stored in the agent's own `profile.yaml` (`space`, `space_primary`), so renaming or deleting a
profile needs no clean-up.

## Shared space notes

```bash
hermes space note acme "Staging DB is read-only; migrations run from CI only"
hermes space notes acme              # list
hermes space notes acme --remove 1   # remove note #1
```

Notes live in `~/.hermes/spaces/<name>/SPACE.md` and enter each member agent's system prompt at
session start, after its own memory, together with the names of the other agents in the space.
Like `MEMORY.md`, they are read once per session and never mid-session, so prompt caching is
unaffected; a new note reaches agents from their next session. The store is capped at 3,000
characters and notes go through the same injection/exfiltration scan as the memory tool.

Agents are told about the command, so you can also ask an agent to "remember this for the whole
work space".

## Shared memory

Facts that hold everywhere — your name, language, time zone — go into the shared memory, which
every agent reads at session start, whether or not it belongs to a space:

```bash
hermes memory add shared "Name: Alex. Reply in Russian. Time zone: Europe/Moscow"
hermes memory list shared
hermes memory remove shared 2
```

It lives in `~/.hermes/spaces/SHARED.md`, is capped at 3,000 characters, goes through the same
injection scan as the memory tool, and enters the prompt right after the agent's own memory. A
space can opt out, for example a work bot that colleagues talk to and that should not know your
personal facts:

```bash
hermes space set work --no-shared-memory    # --shared-memory turns it back on
```

## Moving memory

Every memory store has an address:

| Address | Store |
|---------|-------|
| `<profile>` | Both of an agent's stores: `MEMORY.md` (its notes) and `USER.md` (what it knows about you). |
| `<profile>:memory`, `<profile>:user` | One of them. |
| `space:<name>` | The space's shared notes. |
| `shared` | The shared memory. |

`hermes memory list` shows every store with its size; `hermes memory list <address>` shows the
numbered entries. Then copy or move all of them, some numbers, or the ones containing a word:

```bash
# Everything the default agent knows, into the new work agent (MEMORY → MEMORY, USER → USER)
hermes memory copy default work

# Only entries 2 to 4 of the notes, taken out of the source
hermes memory move default:memory work --entries 2-4

# Every entry mentioning ACME, into the project space's notes
hermes memory move work space:acme --match acme

# Promote facts about you to the shared memory; preview first
hermes memory move default:user shared --match name --match language --dry-run
```

A copy leaves the source alone; a move removes the entries from the source only after every
destination has been written. Entries the destination already holds are skipped. A transfer that
would push a destination over its character limit is refused before anything changes. Writes use
the memory tool's own file lock, so moving memory while an agent is running loses nothing; agents
see the change from their next session.

`hermes space create … --clone-from <profile>` starts the new agent with empty memory; copy what
fits with `hermes memory copy <profile> <agent> --match …`, or pass `--keep-memory` to take all of it.

## Other commands

| Command | Description |
|---------|-------------|
| `hermes space` / `hermes space list` | List spaces, their agents and bound folders. |
| `hermes space current` | Show the space this shell runs in and whether the current folder is bound. |
| `hermes space show <name>` | Details of one space. |
| `hermes space set <name> [--kind K] [--title T] [--[no-]shared-memory]` | Change kind, title, or whether its agents read the shared memory. |
| `hermes space bind <name> [path]` / `unbind <name> [path]` | Bind or unbind a folder (default: the current directory). The deepest bound folder wins. |
| `hermes space delete <name> [-y]` | Forget the space, its notes and chat selections. Its agents stay as plain profiles. |

## Where things live

| File | Holds |
|------|-------|
| `~/.hermes/spaces.yaml` | Space titles, kinds and bound folders. |
| `~/.hermes/profiles/<agent>/profile.yaml` | The agent's `space` membership. |
| `~/.hermes/spaces/<name>/SPACE.md` | Shared space notes. |
| `~/.hermes/spaces/SHARED.md` | The shared memory. |
| `~/.hermes/spaces/chat_selections.json` | Which space each chat lane chose with `/space`. |

All of them sit under the installation root shared by every profile, not inside any one profile.

## Spaces, profiles and projects

- A **profile** is one agent's home: config, memory, sessions.
- A **space** groups profiles and switches between them.
- A **project** (`hermes project`) groups folders *inside* one profile for desktop sessions and
  kanban worktrees. A project space can bind the same repo folders so the project's agent opens
  there.
