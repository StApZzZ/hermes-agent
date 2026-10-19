# Hermes Remote Development (experimental MVP)

Run Hermes, Claude Code, dependencies, build/test commands and Git worktrees on
a Linux SSH host. Keep only the VS Code window on Windows. WSL is not required.

The new command is a lightweight bridge, not a replacement for VS Code
Remote-SSH. Hermes and Claude Code are **separate agents**: Hermes ACP provides
Hermes sessions; it does not internally run Claude Code or share its billing.

## Prerequisites

- Windows: OpenSSH client, Python or locally installed Hermes, VS Code CLI
  (code in PATH), Microsoft's Remote - SSH extension.
- Linux SSH host: Git, Python, tmux, Hermes and Claude Code; enough disk/RAM for
  your project and its dependencies. Use an unprivileged user.
- SSH key access and a host alias in %USERPROFILE%\.ssh\config:

    Host devbox
        HostName YOUR_HOST
        User dev
        IdentityFile ~/.ssh/id_ed25519
        IdentitiesOnly yes
        ServerAliveInterval 30

Verify the SSH host fingerprint out-of-band on first connection.

## Usage from Windows PowerShell

Clone this fork on Windows, but do not need to install its dependencies locally.
The bridge may be invoked as a standalone Python script:

    python .\hermes_cli\subcommands\remote.py doctor devbox --workspace /home/dev/projects/myproject
    python .\hermes_cli\subcommands\remote.py open devbox --workspace /home/dev/projects/myproject

When Hermes is installed locally, equivalent commands are:

    hermes remote doctor devbox --workspace /home/dev/projects/myproject
    hermes remote open devbox --workspace /home/dev/projects/myproject
    hermes remote claude devbox --workspace /home/dev/projects/myproject --session claude-myproject
    hermes remote config devbox --workspace /home/dev/projects/myproject

Connect from VS Code to the server (Remote-SSH), edit remote code directly and
run commands in its integrated terminal. For Claude Code:

    cd /home/dev/projects/myproject
    tmux new-session -A -s claude-myproject -c /home/dev/projects/myproject claude

A disconnected SSH session does not delete tmux state, although running
applications may still fail or exit for other reasons.

For Hermes ACP in a remotely hosted VS Code extension, configure that extension
to run hermes acp on the SSH host. If the ACP client extension runs locally,
use the compatibility SSH stdio proxy:

    hermes remote acp devbox --workspace /home/dev/projects/myproject
    hermes remote config devbox --workspace /home/dev/projects/myproject --proxy

The config command prints ACP Client JSON settings; inspect whether the chosen
extension actually supports the acp.agents setting. Install/authenticate Hermes
and Claude Code **on the server**, not inside WSL. Do not commit tokens,
~/.claude, ~/.hermes credentials or .env. The SSH proxy does not allocate a
PTY and uses batch SSH auth to keep stdout clean for JSON-RPC.

## Limitations and validation

- Source code lives remotely. There is no local filesystem mirroring or rsync.
  Your Git remote remains the source for backup and multi-machine changes.
- This does not provision servers, install packages or configure authentication.
- No integration making Hermes invoke Claude as its underlying LLM is supplied.
- The standalone bridge avoids local agent-memory and package overhead.
- The doctor command reports tool availability and host RAM/disk but does not
  guarantee adequacy; inspect disk quotas, Docker limits and swap as needed.
- Confirm Remote-SSH, VS Code extensions, Hermes ACP and Claude Code on a real
  Linux host. Unit tests mock SSH and cannot prove end-to-end operation.

Tests: python -m pytest tests/hermes_cli/test_remote_dev.py -q
