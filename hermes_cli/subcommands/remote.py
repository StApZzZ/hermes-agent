"""Remote development bridge for Hermes + VS Code + Claude Code.

Projects, agent state and execution live on the SSH host. On Windows, this
module is a thin launcher or a transparent ACP stdio transport.
"""

from __future__ import annotations

import json
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
from typing import Sequence

_SSH_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_TMUX_SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _checked_host(host: str) -> str:
    if not _SSH_ALIAS.fullmatch(host):
        raise ValueError("HOST must be an SSH config alias (letters, digits, . _ -)")
    return host


def _checked_workspace(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("/") or "\x00" in path or "\n" in path:
        raise ValueError("--workspace must be an absolute Linux directory path")
    return posixpath.normpath(path)


def _remote_shell_script(command: str) -> str:
    """OpenSSH joins remote arguments into a shell command; quote the script."""
    return "sh -c " + shlex.quote(command)


def _ssh_argv(host: str, command: str, *, tty: bool = False, batch: bool = False) -> list[str]:
    mode = "-tt" if tty else "-T"
    options = ["-o", "ConnectTimeout=10"]
    if batch:
        options += ["-o", "BatchMode=yes"]
    return ["ssh", mode, *options, "--", _checked_host(host), _remote_shell_script(command)]


def _run(argv: Sequence[str]) -> int:
    try:
        return subprocess.run(list(argv), check=False).returncode
    except FileNotFoundError:
        print(f"Executable not found: {argv[0]}", file=sys.stderr)
        return 127
    except KeyboardInterrupt:
        return 130


def _doctor(host: str, workspace: str) -> int:
    root = shlex.quote(workspace)
    script = f"""\
PATH="$HOME/.local/bin:$PATH"
printf 'Host: '; hostname
printf 'Workspace: %s\n' {root}
if [ -d {root} ]; then printf 'workspace: OK\n'; else printf 'workspace: MISSING\n'; fi
for tool in git python3 node claude hermes tmux; do
  if command -v "$tool" >/dev/null 2>&1; then
    printf '%s: OK\n' "$tool"
  else
    printf '%s: MISSING\n' "$tool"
  fi
done
printf '\nMemory:\n'
if command -v free >/dev/null 2>&1; then free -h; else head -n 3 /proc/meminfo; fi
printf '\nDisk:\n'
if [ -d {root} ]; then df -hP {root}; else df -hP "$HOME"; fi
"""
    return _run(_ssh_argv(host, script))


def _open(host: str, workspace: str, new_window: bool) -> int:
    executable = shutil.which("code") or shutil.which("code.cmd")
    if not executable:
        print("VS Code CLI 'code' missing from PATH (run from PowerShell).", file=sys.stderr)
        return 127
    args = [executable, "--remote", f"ssh-remote+{host}", workspace + "/"]
    if new_window:
        args.append("--new-window")
    return _run(args)


def _claude(host: str, workspace: str, *, tmux: bool, session: str, resume: bool) -> int:
    claude_cmd = "claude" + (" --resume" if resume else "")
    prefix = 'PATH="$HOME/.local/bin:$PATH"; '
    if tmux:
        if not _TMUX_SESSION.fullmatch(session):
            raise ValueError("--session contains invalid tmux session characters")
        command = (
            prefix +
            f"cd -- {shlex.quote(workspace)} && "
            f"exec tmux new-session -A -s {shlex.quote(session)} "
            f"-c {shlex.quote(workspace)} {shlex.quote(claude_cmd)}"
        )
    else:
        command = prefix + f"cd -- {shlex.quote(workspace)} && exec {claude_cmd}"
    return _run(_ssh_argv(host, command, tty=True))


def _acp(host: str, workspace: str) -> int:
    """Run remote Hermes as a byte-transparent stdio ACP server, without PTY.

    SSH batch mode avoids blocking on authentication prompts. This is intended
    for local IDE ACP clients; Remote-SSH extension hosts should use hermes acp.
    """
    command = (
        'PATH="$HOME/.local/bin:$PATH"; ' +
        f"cd -- {shlex.quote(workspace)} && exec hermes acp"
    )
    return _run(_ssh_argv(host, command, batch=True))


def _config(host: str, workspace: str, proxy: bool) -> int:
    if proxy:
        agent = {"command": "hermes",
                 "args": ["remote", "acp", host, "--workspace", workspace]}
    else:
        agent = {"command": "hermes", "args": ["acp"]}
    print(json.dumps({"acp.agents": {"Hermes Remote": agent}},
                     indent=2, ensure_ascii=False))
    return 0


def run_remote(args) -> int:
    try:
        host = _checked_host(args.host)
        workspace = _checked_workspace(args.workspace)
        if args.remote_action == "doctor":
            return _doctor(host, workspace)
        if args.remote_action == "open":
            return _open(host, workspace, args.new_window)
        if args.remote_action == "claude":
            return _claude(host, workspace, tmux=not args.no_tmux,
                           session=args.session, resume=args.resume)
        if args.remote_action == "acp":
            return _acp(host, workspace)
        if args.remote_action == "config":
            return _config(host, workspace, args.proxy)
    except ValueError as exc:
        print(f"Remote development: {exc}", file=sys.stderr)
        return 2
    return 2


def build_remote_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "remote", help="Open and manage SSH remote development workspaces"
    )
    actions = parser.add_subparsers(dest="remote_action", required=True)
    for action, description in (
        ("doctor", "Check SSH, development tools, RAM and disk"),
        ("open", "Open the remote project in the local VS Code GUI"),
        ("claude", "Run Claude Code CLI on the SSH host"),
        ("acp", "Proxy raw ACP stdio to remote Hermes"),
        ("config", "Print VS Code ACP Client JSON settings"),
    ):
        sub = actions.add_parser(action, help=description)
        sub.add_argument("host", metavar="HOST", help="Alias from ~/.ssh/config")
        sub.add_argument("--workspace", required=True, metavar="/absolute/path")
        sub.set_defaults(func=run_remote)
        if action == "open":
            sub.add_argument("--new-window", action="store_true")
        if action == "claude":
            sub.add_argument("--no-tmux", action="store_true",
                             help="Use a disposable SSH TTY")
            sub.add_argument("--session", default="claude-work",
                             help="Persistent tmux session name")
            sub.add_argument("--resume", action="store_true",
                             help="Open Claude's session picker")
        if action == "config":
            sub.add_argument("--proxy", action="store_true",
                             help="Run Hermes via a local SSH ACP proxy")


def main(argv: Sequence[str] | None = None) -> int:
    """Standalone bridge entry point for Windows without locally installed Hermes."""
    import argparse
    parser = argparse.ArgumentParser(prog="remote.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_remote_parser(subparsers)
    args = parser.parse_args(["remote", *(list(argv) if argv is not None else sys.argv[1:])])
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
