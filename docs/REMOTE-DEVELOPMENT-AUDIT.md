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
3. Native subscription CLIs are separate agents: add `remote codex/gemini`
   and matching bookmark choices alongside Claude/Hermes. Preserve each CLI's
   resume syntax; Gemini uses its manual browserless authentication flow.
4. MVP parser registration was correct. However `remote` was missing from the
   built-in/session-token sets and passed through runtime bootstrap/config.
   Add it to both sets and dispatch normal remote calls before runtime imports.
5. Windows `code.cmd` exposed remote paths to cmd.exe interpretation. Invoke
   Code.exe and its CLI JS with an argv array instead. The trusted wrapper's
   literal path also supports the version-directory layout observed in VS Code
   1.142.0 on the Windows host.
6. A bare subprocess wait gave weak cancellation cleanup. Inherit binary OS
   handles; terminate/wait/kill on launcher shutdown, preserve interactive SIGINT
   and child exit codes. Export PATH and quote nested remote shell commands.
7. Validate paths/control characters, session names and strict bookmark schemas.
   Reject parent traversal instead of rewriting paths across symlinks.
8. Emit native SSH config for local ACP, explicit Linux cwd, strict host-key/batch
   options, keepalives, no forwarding/SSH escape parsing. No local Python required
   for that generated bridge.
9. Add saved bookmarks and list/start with explicit config paths. This is data-only
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
- Revised standalone: 45 passed on native Windows via `scripts/run_tests.sh`,
  including a repeat after completing the WSL source checkout. Includes real
  subprocess bytes/exit, standalone script from another cwd and actual main
  dispatch with forbidden PM/model/config/agent imports.
- Existing startup import-weight and command-resolution guards: 2 passed.
- Replayed the original MVP and revised launcher with the same inputs: default
  cross-project tmux collision and code.cmd execution reproduce on the MVP and
  are absent in the revised commands.
- [CI run 39285080430](https://github.com/StApZzZ/hermes-agent/actions/runs/39285080430)
  at `99f2d23`: Windows/Linux standalone contracts passed; all 5 Linux Docker
  SSH/tmux integration cases passed. The local Docker daemon was unavailable.
- The same 5 integration cases also passed through native Windows OpenSSH to
  a newly provisioned Ubuntu 24.04 VM (94.9 seconds). Only the server fixture
  was substituted in a temporary copy: dedicated `dev` account, pinned host key,
  unchanged test bodies and fixture agents. Tested Unicode/quoting, ACP frames,
  stderr/EOF/exit, missing tools/workspace, refused connection, changed host key,
  Ctrl+C, resize, two projects and reconnect to the same agent PID. The test
  account and its files were removed afterwards. This does not prove LLM edits.
- Full source checkout code-health/Ruff checks against the MVP passed with
  pinned Ruff 0.15.10 (0 blocking/advisory health findings). Completing the
  checkout exposed export-order/explicit-subprocess-status rules absent from
  the earlier partial-tree run; those were fixed. Windows footgun scan passed.
- Installed VS Code 1.142.0: native executable/CLI JS `--version` returned 0.
  `remote open` started Remote-SSH; after an initial bootstrap failure, its log
  confirmed the exec server connection and installed VS Code Server on Linux.
  ACP Client 0.2.0 was installed in the server extension directory; user settings
  pin it to the workspace extension host and remote settings use the absolute
  Hermes launcher. Physical UI inspection and extension activation remain pending.
- Real runtime installed as an unprivileged Linux user from `99f2d23` using
  the official Hermes installer; native Claude Code stable is 2.1.292.
  Windows `remote doctor` returned 0, including real `hermes acp --check`.
- The real Windows ACP bridge completed Hermes `initialize` (protocol 1) and
  `session/new`, then stdin EOF with exit 0. Protocol stdout contained only JSON
  frames; diagnostic stderr remained separate. This test made no model request.
  Hermes' existing shared gateway may remain with `--idle-exit` by design.
- Codex CLI 0.163.0 and Gemini CLI 0.64.0 were also installed remotely.
  Built Gemini's native node-pty dependency (npm 12 initially blocked its install
  script); an actual terminal spawn passed. All three native CLIs started in the
  server workspace via Windows launchers and detached successfully from tmux.
  No account login or model edit was performed in those smoke checks.
- The owner requires subscription access through official CLIs. Hermes'
  `auth.adopt_external_logins` was explicitly disabled on the deployment; the
  CLIs became Hermes model backends later (section below).
- Full Hermes suite and PM-managed whole-repository checks were not run; the
  standalone lane and scoped health/Ruff checks do not represent those suites.

### Hermes over the subscription CLIs (signed-in acceptance)

- The `cli-subscriptions` model-provider plugin registers `codex-cli`,
  `claude-cli` and `gemini-cli` (`external_process`). Main turns and every
  auxiliary route resolve through the same `SubscriptionCLIClient`; an
  external-process profile that fails to build its client raises instead of
  falling back to an HTTP client.
- Real Claude Code 2.1.292 (claude.ai team subscription), Hermes as the agent:
  a one-shot turn read a random nonce from a file with Hermes' own file tool and
  wrote it reversed into a new file (verified byte-for-byte); title generation
  logged `Auxiliary title_generation: using claude-cli`. Native CLI tools stay off.
- Prompt caching: the stable prefix (Hermes system prompt + tool contract) now
  goes to Claude via `--system-prompt-file`; cache read rose from 7 % to 94–96 %
  of input tokens on consecutive calls of one session. Codex/Gemini receive the
  same stable prefix first on stdin for their automatic prefix caching.
- Real Codex CLI 0.163.0 is signed in (ChatGPT) but its plan was at its usage
  limit; the turn now surfaces Codex's own message and is classified as rate
  limited (previously a bare "exit code 1"). A Codex model answer remains to be
  observed after the limit resets.
- Real Gemini CLI 0.64.0 has "Gemini API key" selected; Hermes refuses it with an
  instruction to choose Sign in with Google. A Gemini model answer remains to be
  observed after that switch.
- `hermes auth add|status|logout <engine>-cli` run the CLI's own account commands
  (inherited terminal, API-key variables removed); Hermes stores no tokens.
- `python scripts/check --base origin/main`: 12/12 checks pass. Related suites
  (`tests/plugins`, `tests/providers`, auxiliary client, provider seam, CLI
  client, remote dev, Copilot ACP): 1882 passed, 0 failed, 4 skipped.

### Server hardening and backups

- `scripts/remote-dev/harden-server.sh` was applied to the deployment with a
  5-minute automatic rollback, then confirmed from new SSH sessions (WSL and
  Windows). Inbound is `policy drop` except key-only SSH from the VPN exit
  allowlist; password login is refused (`Permission denied (publickey)`).
- `scripts/remote-dev/backup-pull.sh` pulls a consistent daily snapshot into the
  gitignored `server-backups/`; a Windows scheduled task runs it at logon and daily.

The authoring checkout was moved to WSL `~/repos/hermes-agent` at the owner's
request. A separate three-file Windows launcher runs production SSH operations
without WSL. Infrastructure identifiers/access notes remain in the private
Terraform project, outside this public PR.

## Changed files

| Files | Purpose |
| --- | --- |
| `hermes_cli/main.py`, `hermes_cli/_startup_fast.py` | Lightweight routing and CLI compatibility; move the existing gateway argv predicate to the startup sibling to preserve the facade size cap. |
| `hermes_cli/subcommands/remote.py` | SSH, VS Code, tmux, ACP and CLI orchestration. |
| `hermes_cli/subcommands/remote_doctor.py`, `remote_workspaces.py` | Remote probes and validated client bookmarks. |
| `tests/hermes_cli/test_remote_dev.py` | Standalone and process/argument/quoting contracts. |
| `tests/hermes_cli/test_remote_ssh_integration.py`, `remote_ssh/Dockerfile`, `remote_ssh/stub.py` | Disposable real SSH/tmux fixture and transport tests. |
| `tests/fixtures/resolution_allowlist.json` | Justified standalone VS Code lookup exemption. |
| `.github/workflows/remote-development.yml` | Windows/Linux standalone and Linux container lane. |
| `plugins/model-providers/cli-subscriptions/` | `codex-cli`/`claude-cli`/`gemini-cli` profiles and native-login `auth_handler`. |
| `agent/cli_subscription_client.py`, `agent/cli_subscription_process.py` | OpenAI-shaped client over one-shot CLI processes: prompt split, argv, env scrubbing, result/error decoding, process-tree cleanup. |
| `agent/agent_runtime_provider_clients.py`, `agent/agent_runtime_helpers.py`, `agent/auxiliary_client.py` | Provider-supplied client seam moved to a sibling; external-process profiles never fall back to HTTP. |
| `tests/agent/test_cli_subscription_client.py`, `tests/agent/fixtures/subscription_cli.py`, `tests/plugins/test_cli_subscription_auth.py`, `tests/agent/test_provider_client_seam.py` | Real child-process contracts for the three wire formats, caching split, errors, auth commands. |
| `scripts/remote-dev/` | Server hardening with auto-rollback; consistent backup snapshot and local pull. |
| `.gitignore` | `/server-backups/`. |
| `docs/REMOTE-DEVELOPMENT-RU.md`, `docs/REMOTE-DEVELOPMENT-AUDIT.md` | Installation, operations, architecture, review and validation limits. |

## Remaining acceptance checks and limits

Visually verify the remote folder's Explorer/Git/search/debugger and ACP extension
activation/host placement. Authenticate Claude on Linux and configure Hermes'
provider; verify real model-driven file edits and persisted/resumed history in a
disposable project. Transport-level same-PID reconnect, Ctrl+C and resize passed
with fixture agents; physical terminal/UI acceptance remains separate. Inspect
the production process trees/cache locations: no local agent/runtime/build or
WSL should be started by the Windows launcher.

The SSH allowlist is the VPN's shared exit address, so other users of that exit
can reach the key-only SSH port; a dedicated private tunnel from the VPN ingress
to the server would remove that, and needs a change on the VPN host itself.

tmux does not survive reboot/OOM. ACP in-flight generation need not survive
disconnect. Shell banners can corrupt stdio. Alias/symlink spellings form distinct
session identities. Windows requires a recognizable VS Code wrapper/CLI layout. Doctor
thresholds are heuristics, not workload sizing, and do not fully resolve ancestor
cgroups/quotas. A launcher without local Python and authenticated UI/model canaries
remain possible follow-ups, not implemented/verified claims.
