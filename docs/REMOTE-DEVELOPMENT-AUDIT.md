# Remote development audit — 2026-10-19

Reviewed MVP: `2abe5fd66796d8442c349d6c2764b7497128f6b5`, existing branch
`feat/remote-development`, PR #1. Upstream main observed during review:
`6e9b3e3b8852f12bf965f888213470574b72803e`. No upstream merge/rebase is included.

## Architecture and reuse

| Component | Decision |
| --- | --- |
| VS Code Remote-SSH | Owns filesystem, Git, search, terminal, debugging and remote extension host. No custom IDE or filesystem transport. |
| `tools/environments/ssh.py`, `remote_common.py` | A local agent's per-tool SSH backend, snapshots, optional file sync and ControlMaster (disabled on Windows). Not an interactive terminal/ACP transport. Using it here would retain local agent state and unnecessary sync. Orchestrate native OpenSSH instead. |
| `acp_adapter/entry.py`, `server.py`, `session.py` | Reuse `hermes acp`, its JSON-RPC server and shared SessionDB. No protocol rewrite or alternative session store. |
| `agent/runtime_session_*`, `hermes_state*` | Durable Hermes conversation history already exists; it does not keep an interactive Claude terminal process alive. tmux handles that. |
| `hermes_cli/projects_cmd.py`, `projects_db.py` | Server/profile-local multi-folder projects and desktop/kanban grouping, not an SSH target registry. Bookmarks contain only alias/path/preferences; they do not synchronize Projects or server state. |
| `_startup_fast.py`, main parser builders | Reuse early-dispatch convention and shared remote parser. Client-side remote commands bypass agent profile/container initialization; explicit global flags keep the normal CLI path. |

The current upstream SSH backend retains this control-host/tool-target distinction.
A terminal backend alone cannot move model clients, MCP, plugins, Python kernels
and caches off Windows. Running the entire agent on Linux does. No Python
dependency, HTTP endpoint or daemon is added.

## MVP findings and fixes

1. Default `claude-work` attached different projects to one process. Hash
   host/workspace/agent, even for explicit labels; use a dedicated tmux socket.
   Reject tmux `.`/`:` labels.
2. Doctor's last `df` exit status hid missing tools/workspace. Accumulate failures,
   print remediation, inspect ACP runtime, swap/inodes/ulimit/cgroups. System Node
   is optional with native Claude; Hermes owns its managed Python runtime.
3. MVP parser registration was correct. However `remote` was missing from the
   built-in/session-token sets and passed through runtime bootstrap/config.
   Add it to both sets and dispatch normal remote calls before runtime imports.
4. Windows `code.cmd` exposed remote paths to cmd.exe interpretation. Invoke
   Code.exe and its CLI JS with an argv array instead. The trusted wrapper's
   literal path also supports the version-directory layout observed in VS Code
   1.142.0 on the Windows host.
5. A bare subprocess wait gave weak cancellation cleanup. Inherit binary OS
   handles; terminate/wait/kill on launcher shutdown, preserve interactive SIGINT
   and child exit codes. Export PATH and quote nested remote shell commands.
6. Validate paths/control characters, session names and strict bookmark schemas.
   Reject parent traversal instead of rewriting paths across symlinks.
7. Emit native SSH config for local ACP, explicit Linux cwd, strict host-key/batch
   options, keepalives, no forwarding/SSH escape parsing. No local Python required
   for that generated bridge.
8. Add saved bookmarks and list/start with explicit config paths. This is data-only
   client state, not a second server workspace/session database.

## IDE compatibility

Inspected `formulahendry/vscode-acp` revision:
`e7371659e3ac100db842b419b1361205a193032e`, package version 0.2.0.
Its manifest declares `acp.agents` and `acp.defaultWorkingDirectory`, without an
explicit extensionKind. `src/core/ConnectionManager.ts` uses `ndJsonStream` and
advertises filesystem/terminal client capabilities. Install into SSH, verify the
running extension's location, and use the workspace override when needed. SSH
stdio alone cannot move local filesystem callbacks to the server.

Primary references: [Remote-SSH](https://code.visualstudio.com/docs/remote/ssh),
[Extension Host](https://code.visualstudio.com/api/advanced-topics/extension-host),
[ACP client source](https://github.com/formulahendry/vscode-acp/tree/e7371659e3ac100db842b419b1361205a193032e),
[Hermes ACP](https://hermes-agent.nousresearch.com/docs/user-guide/features/acp),
[Claude CLI](https://code.claude.com/docs/en/cli-reference).

## Validation

- Original MVP: 7/7 isolated tests passed on native Windows before changes.
- Revised standalone: 41 passed on native Windows via `scripts/run_tests.sh`.
  Includes real subprocess bytes/exit, standalone script from another cwd and
  actual main dispatch with forbidden PM/model/config/agent imports.
- Existing startup import-weight and command-resolution guards: 2 passed.
- Installed VS Code 1.142.0: native executable/CLI JS `--version` returned 0,
  expected version output, and empty stderr; no window/remote server was opened.
- Scoped `scripts/check --only health,ruff --base <MVP>` passed with pinned Ruff
  0.15.10; Windows footgun scan passed. The complete snapshot check could not
  materialize this partial clone because GitHub blob fetches timed out.
- Local Docker daemon is not running: 5 container tests skipped locally.
- Dedicated CI runs Windows/Linux contracts and Linux container integration.
  Runs are queued on GitHub at reporting time; check PR #1 for final results.
- Full Hermes suite and PM-managed code-health checks are not represented by
  the standalone lane; they require a complete Hermes dev/test environment.
- No development server/credentials supplied: unrelated SSH hosts were not used.
  Real VS Code UI, actual Hermes ACP with model/provider, and authenticated Claude
  remain target-server acceptance checks.

## Remaining acceptance checks and limits

Open a remote folder and verify Explorer/Git/search/debugger. Check ACP extension
host placement, real initialize/session/file edit and persisted history. Authenticate
Claude on Linux, edit a disposable project, disconnect/reconnect to the same PID,
and exercise physical Ctrl+C/window resizing. Inspect Windows/Linux process trees
and remote cache locations; no local agent/runtime/build or WSL should be started.

tmux does not survive reboot/OOM. ACP in-flight generation need not survive
disconnect. Shell banners can corrupt stdio. Alias/symlink spellings form distinct
session identities. Windows requires a recognizable VS Code wrapper/CLI layout. Doctor
thresholds are heuristics, not workload sizing, and do not fully resolve ancestor
cgroups/quotas. A pure-native Windows launcher and real UI/model canaries remain
possible follow-ups, not implemented/verified claims.
