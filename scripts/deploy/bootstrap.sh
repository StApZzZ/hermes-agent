#!/usr/bin/env bash
# Target half of hermes-deploy: builds the Hermes environment on THIS machine (Linux, macOS).
# hermes-deploy.sh / hermes-deploy.ps1 upload it with the rest of scripts/deploy and run it; from a
# checkout it also runs by hand. Every stage is repeatable.
#
#   bootstrap.sh system [--user NAME] [--pubkey "ssh-ed25519 ..."]
#       root or sudo: git, curl, tmux, python3; with --user creates that unprivileged account,
#       authorizes --pubkey for it and enables systemd linger (gateway and tmux outlive logout).
#   bootstrap.sh user [--repo URL] [--ref REF] [--dir PATH] [--workspace PATH] [--update]
#       [--clis claude,codex,gemini,agy] [--provider NAME|none] [--restore ARCHIVE] [--gateway]
#       [--with-browser] [--vscode-settings machine|user|none] [--acp-name NAME]
#       the Hermes user: Hermes from REPO@REF into DIR (official installer), subscription CLIs,
#       PATH for non-login shells, state from a backup-pull archive, gateway service, VS Code settings.
#   bootstrap.sh restore ARCHIVE
#       the Hermes user: Hermes and CLI state from a backup-pull archive (also part of `user --restore`).
#   bootstrap.sh check
#       one "name: value" line per component.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
say() { printf '\n== %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------- system
# Tool names are package names everywhere; "atomic" stands for libatomic, which the Node.js that
# `hermes pm` installs links against and minimal images (Ubuntu cloud/docker) leave out.
pkg_install() {
    local sudo=() pkgs=() p
    [ "$(id -u)" -eq 0 ] || { have sudo || die "need root or sudo to install: $*"; sudo=(sudo); }
    if have apt-get; then
        for p in "$@"; do [ "$p" = atomic ] && pkgs+=(libatomic1) || pkgs+=("$p"); done
        "${sudo[@]}" env DEBIAN_FRONTEND=noninteractive apt-get update -qq
        "${sudo[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
            "${pkgs[@]}" ca-certificates xz-utils >/dev/null
    elif have dnf; then
        for p in "$@"; do [ "$p" = atomic ] && pkgs+=(libatomic) || pkgs+=("$p"); done
        "${sudo[@]}" dnf install -y -q "${pkgs[@]}" xz
    elif have pacman; then
        for p in "$@"; do [ "$p" = atomic ] && pkgs+=(gcc-libs) || pkgs+=("$p"); done
        "${sudo[@]}" pacman -Sy --noconfirm --needed "${pkgs[@]}" xz
    elif have zypper; then
        for p in "$@"; do [ "$p" = atomic ] && pkgs+=(libatomic1) || pkgs+=("$p"); done
        "${sudo[@]}" zypper -n install "${pkgs[@]}" xz
    else die "no supported package manager: install $* yourself and rerun"
    fi
}

stage_system() {
    local user="" pubkey=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --user) user="$2"; shift 2 ;;
            --pubkey) pubkey="$2"; shift 2 ;;
            *) die "system: unknown option $1" ;;
        esac
    done
    say "System packages"
    local missing=()
    for tool in git curl tmux python3; do have "$tool" || missing+=("$tool"); done
    if [ "$(uname -s)" = Darwin ]; then
        if [ "${#missing[@]}" -gt 0 ]; then
            have brew || die "missing ${missing[*]}: install Homebrew (https://brew.sh) or the tools, then rerun"
            brew install "${missing[@]}"
        fi
    else
        ldconfig -p 2>/dev/null | grep -q 'libatomic\.so\.1' || missing+=(atomic)
        [ "${#missing[@]}" -eq 0 ] || pkg_install "${missing[@]}"
    fi
    echo "git, curl, tmux, python3 — ok"
    [ -n "$user" ] || return 0
    [ "$(id -u)" -eq 0 ] || die "--user needs root"
    [ "$user" != root ] || die "Hermes runs as an unprivileged user, not root"
    say "User $user"
    if id "$user" >/dev/null 2>&1; then
        echo "exists"
    else
        useradd -m -s /bin/bash "$user"
        echo "created"
    fi
    local home
    home=$(getent passwd "$user" | cut -d: -f6)
    if [ -n "$pubkey" ]; then
        install -d -m 700 -o "$user" -g "$(id -gn "$user")" "$home/.ssh"
        touch "$home/.ssh/authorized_keys"
        grep -qxF "$pubkey" "$home/.ssh/authorized_keys" || printf '%s\n' "$pubkey" >> "$home/.ssh/authorized_keys"
        chown "$user:$(id -gn "$user")" "$home/.ssh/authorized_keys"
        chmod 600 "$home/.ssh/authorized_keys"
        # SELinux (the RHEL family): sshd reads only ssh_home_t keys, and a file made here may not carry it.
        if have restorecon; then restorecon -R "$home/.ssh"; fi
        echo "key authorized"
    fi
    # Without linger systemd kills the user's processes at logout: the gateway unit and the
    # tmux sessions of `hermes remote` would die with the last SSH session.
    if have loginctl; then loginctl enable-linger "$user" && echo "linger on"; fi
}

# ---------------------------------------------------------------- user
# Non-login shells (ssh host cmd, the VS Code server, ACP) read only ~/.bashrc, and Debian's
# returns early for them: the PATH line has to sit above that return.
ensure_path() {
    export PATH="$HOME/.local/bin:$PATH"
    local line='case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac'
    local mark='# hermes-deploy: ~/.local/bin'
    local rc
    for rc in "$HOME/.bashrc" "$HOME/.profile" "$HOME/.zshenv"; do
        case "$rc" in */.zshenv) [ "$(uname -s)" = Darwin ] || [ -e "$rc" ] || continue ;; esac
        [ -e "$rc" ] && grep -qF "$mark" "$rc" && continue
        { printf '%s\n%s\n' "$mark" "$line"; [ -e "$rc" ] && cat "$rc"; } > "$rc.hermes-deploy.new"
        mv "$rc.hermes-deploy.new" "$rc"
        echo "PATH line added to $rc"
    done
}

install_hermes() {
    local repo="$1" ref="$2" dir="$3" update="$4" browser="$5"
    say "Hermes: $repo@$ref → $dir"
    local marker="$HOME/.cache/hermes-deploy/installed"
    local flags=(--dir "$dir" --branch "$ref" --channel main --non-interactive)
    [ "$browser" = 1 ] || flags+=(--skip-browser --skip-computer-use)
    if [ -d "$dir/.git" ] && [ "$update" = 0 ]; then
        if grep -qxF "$dir" "$marker" 2>/dev/null; then
            echo "installed — kept as is (--update reruns the installer; or hermes update)"
            return 0
        fi
        # An install that never finished reruns the installer, which parks local changes and may
        # reset the branch to origin: on a checkout with the user's own work that is their call.
        if [ -n "$(git -C "$dir" status --porcelain)" ] \
            || [ "$(git -C "$dir" rev-list --count '@{upstream}..HEAD' 2>/dev/null || echo 0)" != 0 ]; then
            echo "has local changes — kept as is. To finish or refresh the install: --update, or"
            echo "  HERMES_REPO_URL=$repo bash $dir/scripts/install.sh ${flags[*]}"
            return 0
        fi
    fi
    if [ ! -d "$dir/.git" ]; then
        mkdir -p "$(dirname "$dir")"
        git clone -q --filter=blob:none --branch "$ref" "$repo" "$dir"
    fi
    HERMES_REPO_URL="$repo" bash "$dir/scripts/install.sh" "${flags[@]}"
    have hermes || die "installer finished but hermes is not on PATH"
    mkdir -p "$(dirname "$marker")"
    grep -qxF "$dir" "$marker" 2>/dev/null || echo "$dir" >> "$marker"
}

npm_cmd() {
    local node
    if have node && [ "$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)" -ge 20 ] && have npm; then
        echo npm; return 0
    fi
    hermes pm install node npm >/dev/null
    node=$(ls -d "${HERMES_HOME:-$HOME/.hermes}"/tools/node-*/bin/node 2>/dev/null | sort -V | tail -1)
    [ -n "$node" ] || return 1
    # The npm CLIs' shebang is `env node`: expose Hermes's pinned node to every shell.
    ln -sf "$node" "$HOME/.local/bin/node"
    echo "$(dirname "$node")/npm"
}

install_cli() {
    local name="$1" update="$2" npm
    if have "$name" && [ "$update" = 0 ]; then
        echo "$name — present"
        return 0
    fi
    case "$name" in
        claude) curl -fsSL https://claude.ai/install.sh | bash ;;
        agy)
            curl -fsSL https://antigravity.google/cli/install.sh | bash
            # Hermes hands a provider CLI its whole environment, ~/.hermes/.env included: the
            # wrapper strips keys and tokens so agy bills only its own Google sign-in.
            cat > "$HOME/.local/bin/agy-hermes" <<'SH'
#!/usr/bin/env bash
for name in $(compgen -e); do
  case "$name" in
    *_API_KEY|*_TOKEN|*_SECRET|*_PASSWORD|GOOGLE_APPLICATION_CREDENTIALS|GOOGLE_GENAI_USE_*|GOOGLE_CLOUD_ACCESS_TOKEN|CLAUDE_CODE_USE_*)
      unset "$name" ;;
  esac
done
exec "$HOME/.local/bin/agy" "$@"
SH
            chmod 755 "$HOME/.local/bin/agy-hermes" ;;
        codex|gemini)
            npm=$(npm_cmd) || { warn "$name: no Node.js 20+ and hermes pm could not install one"; return 1; }
            local pkg=@openai/codex
            [ "$name" = codex ] || pkg=@google/gemini-cli
            "$npm" install --global --prefix "$HOME/.local" --no-fund --no-audit "$pkg" ;;
        *) warn "unknown CLI $name (claude, codex, gemini, agy)"; return 1 ;;
    esac
}

restore_state() {
    local archive="$1"
    say "Restoring state from $(basename "$archive")"
    gzip -t "$archive" || die "$archive is not a valid .tar.gz"
    hermes gateway stop >/dev/null 2>&1 || true
    local home="${HERMES_HOME:-$HOME/.hermes}" stamp
    stamp=$(date -u +%Y%m%d-%H%M%S)
    if [ -e "$home/config.yaml" ]; then
        mkdir -p "$home/backups/pre-restore-$stamp"
        for f in config.yaml .env SOUL.md; do [ -e "$home/$f" ] && cp -p "$home/$f" "$home/backups/pre-restore-$stamp/"; done
        echo "previous config kept in $home/backups/pre-restore-$stamp"
    fi
    # Only Hermes and CLI state: repos/ and work/ in the archive are the source machine's clones,
    # and the checkout here was just installed. The archive is the user's own backup-pull output.
    python3 - "$archive" "$HOME" <<'PY'
import sys, tarfile
archive, home = sys.argv[1], sys.argv[2]
roots = (".hermes", ".claude", ".claude.json", ".codex", ".gemini", ".config/git", ".gitconfig")
count = 0
# Paths are vetted below; the "data" filter (3.14's default) would also refuse the absolute
# symlinks ~/.hermes legitimately holds.
trusted = {"filter": "fully_trusted"} if hasattr(tarfile, "fully_trusted_filter") else {}
with tarfile.open(archive, "r:gz") as tar:
    for m in tar:
        name = m.name[2:] if m.name.startswith("./") else m.name
        parts = name.split("/")
        if name.startswith("/") or ".." in parts or not (m.isfile() or m.isdir() or m.issym()):
            continue
        if not any(name == r or name.startswith(r + "/") for r in roots):
            continue
        m.name = name
        tar.extract(m, home, set_attrs=m.isfile() or m.isdir(), **trusted)
        count += m.isfile()
print(f"{count} files restored")
PY
    # config.yaml may name absolute paths of the source machine's account.
    local foreign
    foreign=$(grep -n '/home/[^/"'"'"' ]*' "$home/config.yaml" 2>/dev/null | grep -v "$HOME" || true)
    [ -z "$foreign" ] || { warn "config.yaml names paths of another home — review them:"; printf '%s\n' "$foreign" >&2; }
}

vscode_settings() {
    local mode="$1" acp_name="$2" workspace="$3" path
    case "$mode" in
        none) return 0 ;;
        machine) path="$HOME/.vscode-server/data/Machine/settings.json" ;;
        user)
            if [ "$(uname -s)" = Darwin ]; then path="$HOME/Library/Application Support/Code/User/settings.json"
            else path="${XDG_CONFIG_HOME:-$HOME/.config}/Code/User/settings.json"; fi ;;
        *) die "--vscode-settings: machine, user or none" ;;
    esac
    say "VS Code settings: $path"
    local acp
    acp=$(python3 -c 'import json,sys; print(json.dumps({"acp.agents": {sys.argv[1]: {"command": sys.argv[2], "args": ["acp"]}}, "acp.defaultWorkingDirectory": sys.argv[3]}))' \
          "$acp_name" "$HOME/.local/bin/hermes" "$workspace")
    python3 "$HERE/vscode_settings.py" "$path" "$HERE/ide/machine-settings.json" - <<<"$acp"
}

stage_user() {
    local repo=https://github.com/StApZzZ/hermes-agent.git ref=main dir="$HOME/repos/hermes-agent" workspace=""
    local update=0 clis=claude,codex,gemini,agy provider=claude-cli restore="" gateway=0 browser=0
    local vsc=none acp_name=Hermes
    while [ $# -gt 0 ]; do
        case "$1" in
            --repo) repo="$2"; shift 2 ;;
            --ref) ref="$2"; shift 2 ;;
            --dir) dir="$2"; shift 2 ;;
            --workspace) workspace="$2"; shift 2 ;;
            --update) update=1; shift ;;
            --clis) clis="$2"; shift 2 ;;
            --provider) provider="$2"; shift 2 ;;
            --restore) restore="$2"; shift 2 ;;
            --gateway) gateway=1; shift ;;
            --with-browser) browser=1; shift ;;
            --vscode-settings) vsc="$2"; shift 2 ;;
            --acp-name) acp_name="$2"; shift 2 ;;
            *) die "user: unknown option $1" ;;
        esac
    done
    [ "$(id -u)" -ne 0 ] || die "run the user stage as the Hermes user, not root"
    dir="${dir/#\~/$HOME}"; workspace="${workspace/#\~/$HOME}"; workspace="${workspace:-$dir}"
    for tool in git curl python3; do have "$tool" || die "$tool missing: run the system stage first"; done
    mkdir -p "$HOME/.local/bin"

    say "PATH"
    ensure_path
    install_hermes "$repo" "$ref" "$dir" "$update" "$browser"

    say "Subscription CLIs: $clis"
    local failed=() name
    for name in ${clis//,/ }; do install_cli "$name" "$update" || failed+=("$name"); done
    [ "${#failed[@]}" -eq 0 ] || warn "not installed: ${failed[*]} (the rest of the environment is ready)"

    if [ -n "$restore" ]; then
        restore_state "$restore"
    elif [ "$provider" != none ] && case "$(hermes config get model.provider 2>/dev/null)" in ""|auto) true ;; *) false ;; esac; then
        say "Model provider: $provider"
        hermes config set model.provider "$provider"
        hermes config set model.default default
    fi

    if [ "$gateway" = 1 ]; then
        say "Gateway service"
        hermes gateway install && hermes gateway restart
    fi
    mkdir -p "$workspace"
    vscode_settings "$vsc" "$acp_name" "$workspace"
    stage_check
}

# ---------------------------------------------------------------- check
version_of() {
    have "$1" || { echo "missing"; return 0; }
    "$@" 2>/dev/null | head -1 || echo "present"
}

stage_check() {
    say "Check"
    echo "host: $(hostname) ($(uname -sm))"
    echo "hermes: $(version_of hermes --version)"
    echo "provider: $(hermes config get model.provider 2>/dev/null || echo '?')"
    local cli
    for cli in claude codex gemini agy node git; do echo "$cli: $(version_of "$cli" --version)"; done
    echo "tmux: $(version_of tmux -V)"
    if have systemctl && systemctl --user cat hermes-gateway.service >/dev/null 2>&1; then
        echo "gateway: $(systemctl --user is-active hermes-gateway.service 2>/dev/null || true)"
    fi
}

case "${1:-}" in
    system) shift; stage_system "$@" ;;
    user) shift; stage_user "$@" ;;
    restore) [ $# -eq 2 ] || die "usage: restore ARCHIVE"; restore_state "$2" ;;
    check) stage_check ;;
    -h|--help|"") sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//' ;;
    *) die "unknown stage $1 (system, user, restore, check)" ;;
esac
