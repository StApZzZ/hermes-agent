"""Remote development bridge for Hermes + VS Code + Claude Code.

Projects, agent state and execution live on the SSH host. On Windows, this
module is a thin launcher or a transparent ACP stdio transport.
"""

from __future__ import annotations

import json
import hashlib
import os
import posixpath
import re
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Sequence

_SSH_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_TMUX_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def _checked_host(host: str) -> str:
    if not isinstance(host, str) or not _SSH_ALIAS.fullmatch(host):
        raise ValueError("HOST must be an SSH config alias (letters, digits, . _ -)")
    return host


def _checked_workspace(path: str) -> str:
    if (not isinstance(path, str) or not path.startswith("/")
            or any(ord(c) < 32 or ord(c) == 127 for c in path)
            or ".." in path.split("/")):
        raise ValueError("--workspace must be an absolute Linux directory path")
    return "/" + posixpath.normpath(path).lstrip("/")


def _remote_shell_script(command: str) -> str:
    """OpenSSH joins remote arguments into a shell command; quote the script."""
    return "sh -c " + shlex.quote(command)


def _ssh_argv(host: str, command: str, *, tty: bool = False, batch: bool = False,
              ssh_config: Path | None = None) -> list[str]:
    mode = "-tt" if tty else "-T"
    options = ["-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
               "-o", "ServerAliveCountMax=3", "-o", "ForwardAgent=no",
               "-o", "ClearAllForwardings=yes", "-o", "RemoteCommand=none"]
    if batch:
        options += ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-e", "none"]
    if ssh_config is not None:
        options += ["-F", str(ssh_config.resolve())]
    return ["ssh", mode, *options, "--", _checked_host(host), _remote_shell_script(command)]


def _run(argv: Sequence[str], *, interactive: bool = False, env=None) -> int:
    """Inherit OS handles: no decoding, buffering or newline conversion in ACP.

    A TTY's Ctrl+C belongs to SSH/its remote foreground job. The launcher
    must keep waiting instead of killing the connection and detaching tmux.
    """
    child = None
    previous = {}

    def interrupted(signum, frame):
        if signum == signal.SIGINT and interactive:
            return
        raise SystemExit(128 + signum)

    try:
        child = subprocess.Popen(list(argv), env=env)
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, interrupted)
        # health: allow HX006 -- Interactive SSH/ACP lives until EOF; shutdown below is bounded.
        rc = child.wait()
        return 128 - rc if rc < 0 else rc
    except FileNotFoundError:
        print(f"Executable not found: {argv[0]}", file=sys.stderr)
        return 127
    except OSError as exc:
        print(f"Cannot launch {argv[0]}: {exc}", file=sys.stderr)
        return 126
    except KeyboardInterrupt:
        return 130
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _doctor(host: str, workspace: str, ssh_config: Path | None = None) -> int:
    from hermes_cli.subcommands.remote_doctor import doctor_script
    rc = _run(_ssh_argv(host, doctor_script(workspace), batch=True, ssh_config=ssh_config))
    if rc == 255:
        print(f"SSH failed. Run 'ssh {host}' interactively to verify the host key, "
              "key/agent authentication and ~/.ssh/config (HostName, User, Port).", file=sys.stderr)
    return rc


def _vscode_command(executable: str) -> tuple[list[str], dict | None]:
    # A .cmd would pass remote path metacharacters through cmd.exe on Windows.
    # Use VS Code's own Node CLI entry point without a shell instead.
    env = None
    args = [executable]
    if Path(executable).suffix.lower() in {".cmd", ".bat"}:
        install = Path(executable).resolve().parent.parent
        electron = install / "Code.exe"
        cli = install / "resources" / "app" / "out" / "cli.js"
        wrapper = Path(executable)
        if wrapper.is_file():
            # Recent VS Code installs put resources under a version directory.
            # Read only the literal CLI path from the trusted installed wrapper;
            # never execute it or expand batch variables supplied by a workspace.
            match = re.search(r'"%~dp0\.\.[\\/](?P<cli>(?:[A-Za-z0-9_-]+[\\/])?resources[\\/]app[\\/]out[\\/]cli\.js)"',
                              wrapper.read_text(encoding="utf-8-sig"), re.IGNORECASE)
            if match:
                cli = install.joinpath(*match["cli"].replace("\\", "/").split("/"))
        if not electron.is_file() or not cli.is_file():
            raise ValueError("Cannot locate Code.exe and the CLI JS declared by code.cmd; repair the VS Code install.")
        args = [str(electron), str(cli)]
        env = dict(os.environ, ELECTRON_RUN_AS_NODE="1")
        env.pop("VSCODE_DEV", None)
    return args, env


def _open(host: str, workspace: str, new_window: bool) -> int:
    executable = shutil.which("code") or shutil.which("code.cmd")
    if not executable:
        print("VS Code CLI 'code' missing from PATH (run from PowerShell).", file=sys.stderr)
        return 127
    try:
        args, env = _vscode_command(executable)
    except (OSError, ValueError) as exc:
        print(f"VS Code: {exc}", file=sys.stderr)
        return 127
    args += ["--remote", f"ssh-remote+{host}", workspace.rstrip("/") + "/"]
    if new_window:
        args.append("--new-window")
    return _run(args, env=env)


def _session_name(host: str, workspace: str, session: str | None, agent: str) -> str:
    label = session or agent
    if not isinstance(label, str) or not _TMUX_SESSION.fullmatch(label):
        raise ValueError("--session must contain 1-64 letters, digits, _ or - (no tmux . or :)")
    digest = hashlib.sha256(f"{host}\0{workspace}\0{agent}".encode()).hexdigest()[:16]
    return f"{label}-{digest}"


def _terminal_agent(host: str, workspace: str, *, tmux: bool, session: str | None,
                    resume: bool, agent: str = "claude", ssh_config: Path | None = None) -> int:
    agent_cmd = agent + (" --resume" if resume else "")
    prefix = 'export PATH="$HOME/.local/bin:$PATH"; '
    if tmux:
        name = _session_name(host, workspace, session, agent)
        command = (
            prefix +
            f"cd -- {shlex.quote(workspace)} && "
            f"exec tmux -L hermes-remote new-session -A -s {name} "
            f"-c {shlex.quote(workspace)} sh -c {shlex.quote('exec ' + agent_cmd)}"
        )
    else:
        command = prefix + f"cd -- {shlex.quote(workspace)} && exec {agent_cmd}"
    return _run(_ssh_argv(host, command, tty=True, ssh_config=ssh_config), interactive=True)


def _claude(host: str, workspace: str, *, tmux: bool, session: str | None, resume: bool) -> int:
    return _terminal_agent(host, workspace, tmux=tmux, session=session, resume=resume)


def _acp(host: str, workspace: str, ssh_config: Path | None = None) -> int:
    """Run remote Hermes as a byte-transparent stdio ACP server, without PTY.

    SSH batch mode avoids blocking on authentication prompts. This is intended
    for local IDE ACP clients; Remote-SSH extension hosts should use hermes acp.
    """
    command = (
        'export PATH="$HOME/.local/bin:$PATH"; ' +
        f"cd -- {shlex.quote(workspace)} && exec hermes acp"
    )
    return _run(_ssh_argv(host, command, batch=True, ssh_config=ssh_config))


def _config(host: str, workspace: str, proxy: bool, ssh_config: Path | None = None) -> int:
    if proxy:
        command = ('export PATH="$HOME/.local/bin:$PATH"; '
                   f"cd -- {shlex.quote(workspace)} && exec hermes acp")
        argv = _ssh_argv(host, command, batch=True, ssh_config=ssh_config)
        agent = {"command": argv[0], "args": argv[1:]}
    else:
        agent = {"command": "hermes", "args": ["acp"]}
    print(json.dumps({"acp.agents": {"Hermes Remote": agent},
                      "acp.defaultWorkingDirectory": workspace},
                     indent=2, ensure_ascii=False))
    return 0


def run_remote(args) -> int:
    try:
        from hermes_cli.subcommands.remote_workspaces import load_workspaces, resolve_target
        profiles = load_workspaces(args.config)
        if args.remote_action == "list":
            print(json.dumps(profiles, indent=2, ensure_ascii=False))
            return 0
        target = resolve_target(args.host, args.workspace, profiles)
        host = _checked_host(target["host"])
        workspace = _checked_workspace(target["workspace"])
        if args.remote_action == "doctor":
            return _doctor(host, workspace, args.ssh_config)
        if args.remote_action == "open":
            return _open(host, workspace, args.new_window)
        if args.remote_action in {"claude", "start"}:
            return _terminal_agent(host, workspace,
                tmux=target.get("tmux", True) if args.no_tmux is None else not args.no_tmux,
                session=args.session or target.get("session"),
                resume=args.resume,
                agent="claude" if args.remote_action == "claude" else target.get("agent", "hermes"),
                ssh_config=args.ssh_config)
        if args.remote_action == "acp":
            return _acp(host, workspace, args.ssh_config)
        if args.remote_action == "config":
            return _config(host, workspace, args.proxy, args.ssh_config)
    except (ValueError, OSError) as exc:
        print(f"Remote development: {exc}", file=sys.stderr)
        return 2
    return 2


def build_remote_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "remote", help="Open and manage SSH remote development workspaces"
    )
    actions = parser.add_subparsers(dest="remote_action", required=True)
    listing = actions.add_parser("list", help="List saved remote workspaces as JSON")
    listing.add_argument("--config", type=Path, help="Path to remote-workspaces.json")
    listing.set_defaults(func=run_remote)
    for action, description in (
        ("doctor", "Check SSH, development tools, RAM and disk"),
        ("open", "Open the remote project in the local VS Code GUI"),
        ("claude", "Run Claude Code CLI on the SSH host"),
        ("start", "Run the saved workspace's preferred agent in tmux"),
        ("acp", "Proxy raw ACP stdio to remote Hermes"),
        ("config", "Print VS Code ACP Client JSON settings"),
    ):
        sub = actions.add_parser(action, help=description)
        sub.add_argument("host", metavar="HOST_OR_WORKSPACE", help="SSH alias or saved workspace name")
        sub.add_argument("--workspace", metavar="/absolute/path")
        sub.add_argument("--config", type=Path, help="Path to remote-workspaces.json")
        sub.add_argument("--ssh-config", type=Path, help="OpenSSH -F config (SSH commands only; VS Code uses its own config)")
        sub.set_defaults(func=run_remote)
        if action == "open":
            sub.add_argument("--new-window", action="store_true")
        if action in {"claude", "start"}:
            sub.add_argument("--no-tmux", action="store_true", default=None,
                             help="Use a disposable SSH TTY")
            sub.add_argument("--session", help="Session label (workspace hash is always appended)")
            sub.add_argument("--resume", action="store_true",
                             help="Open Claude's session picker")
        if action == "config":
            sub.add_argument("--proxy", action="store_true",
                             help="Run Hermes via a local SSH ACP proxy")


def main(argv: Sequence[str] | None = None) -> int:
    """Standalone bridge entry point for Windows without locally installed Hermes."""
    import argparse
    # Script mode needs only this source tree, never an installed Hermes runtime.
    if __package__ in {None, ""}:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    parser = argparse.ArgumentParser(prog="remote.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_remote_parser(subparsers)
    args = parser.parse_args(["remote", *(list(argv) if argv is not None else sys.argv[1:])])
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
