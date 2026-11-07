#!/usr/bin/env bash
# hermes-deploy — one command that builds the Hermes environment here or on a remote Linux host. Linux, macOS, WSL.
# Windows without WSL: hermes-deploy.ps1.
#
#   hermes-deploy.sh [options]                              this machine
#   hermes-deploy.sh --host ADDR [--name ALIAS] [options]   remote host over SSH (writes a Host block)
#   hermes-deploy.sh --name ALIAS [options]                 remote host already in ~/.ssh/config
#
# Remote:  --user NAME (hermes)  --port N  --key ~/.ssh/KEY  --ssh-config FILE (~/.ssh/config)  --root-login (connect as root first: packages,
#          create --user, authorize the key, linger)  --admin NAME (the same through a sudo user where root logins are
#          refused: opc on Oracle Cloud, ubuntu, ec2-user, rocky)  --skip-system
# Hermes:  --repo URL (fork)  --ref REF (main)  --dir PATH (~/repos/hermes-agent)  --workspace PATH (= --dir)
#          --clis claude,codex,gemini,agy  --provider NAME|none (claude-cli)  --update  --with-browser  --gateway
# State:   --restore ARCHIVE (a backup-pull.sh .tar.gz)  --clone-from ALIAS (snapshot a live host now)
# Lock:    --lock: close the host to everything but key-only SSH from the allowed addresses (harden-server.sh);
#          --allow ADDR[,ADDR] (IPv4/IPv6[/len], repeatable; implies --lock). Your current address is offered too.
# IDE:     --ide code|none (code)  --no-open
#          --dry-run: checks and the plan only, writes nothing here or there.
#
# Steps: SSH Host block → login → system stage (packages; root/sudo) → user stage (Hermes from the fork via the
# official installer, Claude/Codex/Gemini/Antigravity CLIs, state, gateway, VS Code server settings) → --lock → VS Code
# here (Remote-SSH, remote.SSH.defaultExtensions) → VS Code opens the workspace. Rerunning is safe; an existing
# checkout is left alone unless --update. Subscription logins stay with each CLI: no token is copied.
set -euo pipefail

REPO=https://github.com/StApZzZ/hermes-agent.git REF=main
HOST="" NAME="" RUSER=hermes PORT=22 KEY="" ROOT_LOGIN=0 ADMIN=root SKIP_SYSTEM=0
DIR="" WORKSPACE="" CLIS="" PROVIDER="" UPDATE=0 BROWSER=0 GATEWAY=0
RESTORE="" CLONE_FROM="" IDE=code NO_OPEN=0 DRY=0 SSH_CFG="" LOCK=0 ALLOW=()
while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --name) NAME="$2"; shift 2 ;;
        --user) RUSER="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --key) KEY="${2%.pub}"; shift 2 ;;
        --ssh-config) SSH_CFG="$2"; shift 2 ;;
        --root-login) ROOT_LOGIN=1; shift ;;
        --admin) ADMIN="$2"; ROOT_LOGIN=1; shift 2 ;;
        --skip-system) SKIP_SYSTEM=1; shift ;;
        --repo) REPO="$2"; shift 2 ;;
        --ref) REF="$2"; shift 2 ;;
        --dir) DIR="$2"; shift 2 ;;
        --workspace) WORKSPACE="$2"; shift 2 ;;
        --clis) CLIS="$2"; shift 2 ;;
        --provider) PROVIDER="$2"; shift 2 ;;
        --update) UPDATE=1; shift ;;
        --with-browser) BROWSER=1; shift ;;
        --gateway) GATEWAY=1; shift ;;
        --restore) RESTORE="$2"; shift 2 ;;
        --clone-from) CLONE_FROM="$2"; shift 2 ;;
        --lock) LOCK=1; shift ;;
        --allow) IFS=, read -ra a <<<"$2"; ALLOW+=("${a[@]}"); LOCK=1; shift 2 ;;
        --ide) IDE="$2"; shift 2 ;;
        --no-open) NO_OPEN=1; shift ;;
        --dry-run) DRY=1; shift ;;
        -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1 (--help)" >&2; exit 2 ;;
    esac
done
case "$IDE" in code|none) ;; *) echo "--ide: code or none (Cursor and JetBrains come later)" >&2; exit 2 ;; esac
[ -z "$RESTORE" ] || [ -z "$CLONE_FROM" ] || { echo "--restore or --clone-from, not both" >&2; exit 2; }
[ -z "$RESTORE" ] || [ -f "$RESTORE" ] || { echo "no archive $RESTORE" >&2; exit 2; }
for t in tar curl python3; do command -v "$t" >/dev/null || { echo "need $t" >&2; exit 2; }; done
for a in ${ALLOW[@]+"${ALLOW[@]}"}; do
    [[ $a =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}(/[0-9]{1,2})?$ || $a =~ ^[0-9A-Fa-f:]*:[0-9A-Fa-f:.]*(/[0-9]{1,3})?$ ]] \
        || { echo "--allow: not an IPv4/IPv6 address or range: $a" >&2; exit 2; }
done

say() { printf '\n== %s\n' "$*"; }
q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }   # one word for the remote shell
REMOTE=0; [ -z "$HOST$NAME" ] || REMOTE=1
[ "$LOCK" = 0 ] || [ "$REMOTE" = 1 ] || { echo "--lock closes a remote host: pass --host or --name" >&2; exit 2; }
WSL=0; grep -qi microsoft /proc/version 2>/dev/null && WSL=1
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/hermes-deploy"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/hermes-deploy.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
SSH=(ssh -o ConnectionAttempts=3 -o ServerAliveInterval=30); [ -z "$SSH_CFG" ] || SSH+=(-F "$SSH_CFG")

# ---------------------------------------------------------------- bundle
# From a checkout the files sit next to this script; `curl … | bash -s --` fetches them for --ref.
SELF=$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)
FILES=(bootstrap.sh vscode_settings.py hermes-deploy.ps1 ide/machine-settings.json ide/vscode/local.txt ide/vscode/remote.txt)
raw_base() {
    local path="${REPO%.git}"
    case "$path" in https://github.com/*) printf 'https://raw.githubusercontent.com/%s/%s' "${path#https://github.com/}" "$REF" ;;
        *) echo "standalone run needs a GitHub --repo; run from a checkout instead" >&2; return 1 ;; esac
}
BUNDLE="$SELF"
if [ ! -f "$SELF/bootstrap.sh" ]; then
    BUNDLE="$WORK/bundle"
    base="$(raw_base)/scripts/deploy"
    for f in "${FILES[@]}"; do
        mkdir -p "$BUNDLE/$(dirname "$f")"
        curl -fsSL --retry 3 "$base/$f" -o "$BUNDLE/$f" || { echo "cannot fetch $base/$f" >&2; exit 3; }
    done
fi
remote_dev_script() {   # $1: a file of scripts/remote-dev
    if [ -f "$SELF/../remote-dev/$1" ]; then echo "$SELF/../remote-dev/$1"; return; fi
    curl -fsSL --retry 3 "$(raw_base)/scripts/remote-dev/$1" -o "$WORK/$1"
    echo "$WORK/$1"
}

USER_ARGS=(--repo "$REPO" --ref "$REF")
[ -z "$DIR" ] || USER_ARGS+=(--dir "$DIR")
[ -z "$WORKSPACE" ] || USER_ARGS+=(--workspace "$WORKSPACE")
[ -z "$CLIS" ] || USER_ARGS+=(--clis "$CLIS")
[ -z "$PROVIDER" ] || USER_ARGS+=(--provider "$PROVIDER")
[ "$UPDATE" = 0 ] || USER_ARGS+=(--update)
[ "$BROWSER" = 0 ] || USER_ARGS+=(--with-browser)
[ "$GATEWAY" = 0 ] || USER_ARGS+=(--gateway)

# --clone-from: the same snapshot backup-pull.sh takes, streamed into this run's scratch dir.
if [ -n "$CLONE_FROM" ] && [ "$DRY" = 0 ]; then
    say "Snapshot of $CLONE_FROM"
    (umask 077; "${SSH[@]}" -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15 "$CLONE_FROM" 'bash -s' \
        < "$(remote_dev_script backup-snapshot.sh)" > "$WORK/clone.tar.gz")
    gzip -t "$WORK/clone.tar.gz"
    du -h "$WORK/clone.tar.gz" | cut -f1
    RESTORE="$WORK/clone.tar.gz"
    [ "$GATEWAY" = 0 ] || echo "note: $CLONE_FROM's gateway polls the same bot tokens — stop one of the two" >&2
fi

# ---------------------------------------------------------------- VS Code
code_cli() {
    command -v code 2>/dev/null && return
    local c="/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"
    [ -x "$c" ] && echo "$c"
}
ext_list() { sed 's/#.*//; s/[[:space:]]//g; /^$/d' "$BUNDLE/ide/vscode/$1"; }
user_settings() {
    if [ "$(uname -s)" = Darwin ]; then echo "$HOME/Library/Application Support/Code/User/settings.json"
    else echo "${XDG_CONFIG_HOME:-$HOME/.config}/Code/User/settings.json"; fi
}
install_exts() {
    local cli="$1" e; shift
    for e in "$@"; do "$cli" --install-extension "$e" --force >/dev/null 2>&1 && echo "  $e — ok" || echo "  $e — failed" >&2; done
}

# ---------------------------------------------------------------- local
if [ "$REMOTE" = 0 ]; then
    VSC=none; [ "$IDE" = none ] || { VSC=user; [ "$WSL" = 0 ] || VSC=machine; }
    if [ "$DRY" = 1 ]; then
        say "Plan (--dry-run)"
        [ "$SKIP_SYSTEM" = 1 ] || echo "bash $BUNDLE/bootstrap.sh system"
        echo "bash $BUNDLE/bootstrap.sh user ${USER_ARGS[*]} --vscode-settings $VSC${RESTORE:+ --restore $RESTORE}"
        [ "$IDE" = none ] || echo "code --install-extension: $(ext_list remote.txt | tr '\n' ' ')"
        exit 0
    fi
    [ "$SKIP_SYSTEM" = 1 ] || bash "$BUNDLE/bootstrap.sh" system
    bash "$BUNDLE/bootstrap.sh" user "${USER_ARGS[@]}" --vscode-settings "$VSC" --acp-name Hermes ${RESTORE:+--restore "$RESTORE"}
    WS="${WORKSPACE:-${DIR:-$HOME/repos/hermes-agent}}"; WS="${WS/#\~/$HOME}"
    if [ "$IDE" = code ]; then
        say "VS Code"
        CLI=$(code_cli || true)
        if [ -z "$CLI" ]; then
            echo "no 'code' command: install VS Code and run 'Shell Command: Install code in PATH', then rerun"
        else
            # In WSL `code` is the Windows wrapper; it installs into the WSL server, where the workspace lives.
            # shellcheck disable=SC2046  # extension IDs have no spaces: one word each
            install_exts "$CLI" $(ext_list remote.txt)
            [ "$NO_OPEN" = 1 ] || "$CLI" "$WS" || true
        fi
    fi
    say "Done"
    echo "Sign in once per CLI: claude auth login · codex login --device-auth · agy (Google sign-in)"
    echo "Then: hermes · hermes doctor · bash $BUNDLE/bootstrap.sh check"
    exit 0
fi

# ---------------------------------------------------------------- remote
NAME="${NAME:-hermes-${HOST//[^A-Za-z0-9_-]/-}}"
if [ -z "$KEY" ]; then
    for k in id_ed25519 id_ecdsa id_rsa; do [ -f "$HOME/.ssh/$k" ] && { KEY="$HOME/.ssh/$k"; break; }; done
fi
TTY=(); [ -t 0 ] && [ -t 1 ] && TTY=(-t)

if [ -n "$HOST" ]; then
    [ -n "$KEY" ] && [ -f "$KEY" ] || { echo "no SSH key: ssh-keygen -t ed25519 makes one; put it on the host with ssh-copy-id (docs/HERMES-RU.md, section 3), or pass --key" >&2; exit 4; }
    say "SSH: Host $NAME → $RUSER@$HOST:$PORT ($KEY)"
    CFG_FILE="${SSH_CFG:-$HOME/.ssh/config}"
    # First in the file: ssh takes the first value it finds. `Match all` closes the block so global
    # options and Includes that used to open the file apply to every host again.
    block="# >>> hermes-deploy $NAME
Host $NAME
  HostName $HOST
  User $RUSER
  Port $PORT
  IdentityFile \"$KEY\"
  IdentitiesOnly yes
  ServerAliveInterval 30
Match all
# <<< hermes-deploy $NAME"
    if [ "$DRY" = 1 ]; then
        printf '%s\n(--dry-run: not written)\n' "$block"
        SSH+=(-o "HostName=$HOST" -o "User=$RUSER" -o "Port=$PORT" -o "IdentityFile=$KEY" -o IdentitiesOnly=yes)
    else
        mkdir -p "$(dirname "$CFG_FILE")" "$CACHE"
        [ -e "$CFG_FILE" ] || { touch "$CFG_FILE"; chmod 600 "$CFG_FILE"; }
        bak="$CACHE/ssh-config.$(printf %s "$CFG_FILE" | cksum | cut -d' ' -f1).bak"   # one per config file
        [ -e "$bak" ] || cp -p "$CFG_FILE" "$bak"   # the first original
        BLOCK="$block" python3 - "$CFG_FILE" "$NAME" <<'PY'
import os, re, sys
path, name = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
text = re.sub(rf"# >>> hermes-deploy {re.escape(name)}\n.*?# <<< hermes-deploy {re.escape(name)}\n?", "", text, flags=re.S)
open(path, "w", encoding="utf-8").write(os.environ["BLOCK"] + "\n\n" + text.lstrip("\n"))
PY
        echo "written (original kept in $bak)"
    fi
fi

ssh_err() {
    printf '%s\n' "$1" | tail -2 >&2
    if printf '%s' "$1" | grep -q "Permission denied"; then
        if [ "$2" = "$ADMIN" ]; then echo "the host refused the key: add it for $2 there — ssh-copy-id -i $KEY.pub $2@<host>" >&2
        else echo "the host refused the key: authorize $KEY.pub for $2 (ssh-copy-id), or use --root-login / --admin" >&2; fi
    elif printf '%s' "$1" | grep -qiE "timed out|refused|no route|unreachable"; then
        echo "host unreachable: check the address and that your egress IP ($(curl -s --max-time 10 https://api.ipify.org || echo ?)) is allowlisted" >&2
    fi
    exit 5
}
# ssh takes the first value it is given, so User= must precede the -o User of a --dry-run.
# ROOT_SSH logs in as root, or as the --admin sudo user that stands in for it (RSUDO).
ROOT_SSH=(ssh -o "User=$ADMIN" "${SSH[@]:1}") RSUDO=""
[ "$ADMIN" = root ] || RSUDO="sudo "
upload_bundle() {   # $1: root or user
    local ssh=("${SSH[@]}"); [ "$1" = user ] || ssh=("${ROOT_SSH[@]}")
    COPYFILE_DISABLE=1 tar -C "$BUNDLE" -cf - "${FILES[@]}" \
        | "${ssh[@]}" "$NAME" 'umask 077; rm -rf ~/.cache/hermes-deploy/ide; mkdir -p ~/.cache/hermes-deploy && tar -xf - -C ~/.cache/hermes-deploy'
}

if [ "$ROOT_LOGIN" = 1 ]; then
    say "System stage as $ADMIN@$NAME"
    [ -f "$KEY.pub" ] || { echo "no $KEY.pub to authorize for $RUSER" >&2; exit 4; }
    err=$("${ROOT_SSH[@]}" -n -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new "$NAME" true 2>&1) || ssh_err "$err" "$ADMIN"
    if [ "$DRY" = 1 ]; then
        echo "would run: ${RSUDO}bootstrap.sh system --user $RUSER --pubkey <$KEY.pub>"
    else
        upload_bundle root
        "${ROOT_SSH[@]}" ${TTY[@]+"${TTY[@]}"} "$NAME" "${RSUDO}bash ~/.cache/hermes-deploy/bootstrap.sh system --user $(q "$RUSER") --pubkey $(q "$(cat "$KEY.pub")")"
    fi
fi

say "Login as $RUSER@$NAME"
if [ "$DRY" = 1 ] && [ "$ROOT_LOGIN" = 1 ]; then
    echo "(--dry-run: $RUSER may not exist yet)"
else
    err=$("${SSH[@]}" -n -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new "$NAME" true 2>&1) || ssh_err "$err" "$RUSER"
    echo "ok"
fi

# --lock needs root: the root or --admin login, or the user's sudo. The address the host sees us from
# (a VPN exit, a NAT) is the one that must stay allowed, so the host is asked, not a what-is-my-IP service.
# sshd keeps admitting the Hermes user and the --admin user: on Oracle Cloud the latter is the only way to root.
LOCK_SSH=("${SSH[@]}") SUDO="sudo " ADMINS=("$RUSER")
[ "$ROOT_LOGIN" = 0 ] || { LOCK_SSH=("${ROOT_SSH[@]}"); SUDO="$RSUDO"; }
[ "$ROOT_LOGIN" = 0 ] || [ "$ADMIN" = root ] || [ "$ADMIN" = "$RUSER" ] || ADMINS+=("$ADMIN")
if [ "$LOCK" = 1 ]; then
    "${LOCK_SSH[@]}" -n "$NAME" '(command -v apt-get || command -v dnf) && command -v systemctl' >/dev/null \
        || { echo "--lock supports Debian/Ubuntu and RHEL-family (dnf) hosts with systemd" >&2; exit 2; }
    CUR=$("${LOCK_SSH[@]}" -n "$NAME" 'printf %s "${SSH_CLIENT%% *}"')
    if [ -n "$CUR" ] && ! printf '%s\n' ${ALLOW[@]+"${ALLOW[@]}"} | grep -qxF "$CUR"; then
        ans=y
        # /dev/tty, not stdin: under `curl … | bash` stdin is this script. No terminal: allowed without asking.
        if (: </dev/tty) 2>/dev/null; then
            read -r -p "Your address as $NAME sees it: $CUR. Allow SSH from it? [Y/n] " ans </dev/tty || ans=y
        fi
        case "$ans" in [nN]*) ;; *) ALLOW+=("$CUR") ;; esac
    fi
    [ ${#ALLOW[@]} -gt 0 ] || { echo "--lock with no address to allow would shut everyone out: pass --allow" >&2; exit 2; }
fi

RCMD="bash ~/.cache/hermes-deploy/bootstrap.sh"
VSC=none; [ "$IDE" = none ] || VSC=machine
if [ "$DRY" = 1 ]; then
    say "Plan (--dry-run)"
    [ "$SKIP_SYSTEM" = 1 ] || [ "$ROOT_LOGIN" = 1 ] || echo "$NAME: bootstrap.sh system (sudo only for missing packages)"
    [ -z "$RESTORE$CLONE_FROM" ] || echo "upload ${RESTORE:-snapshot of $CLONE_FROM} → restore"
    echo "$NAME: bootstrap.sh user ${USER_ARGS[*]} --vscode-settings $VSC"
    [ "$LOCK" = 0 ] || echo "$NAME: ${SUDO}harden-server.sh apply --allow ${ALLOW[*]} --admin-user ${ADMINS[*]}" \
        "(inbound: key-only SSH from these addresses; everything else closed)"
    [ "$IDE" = none ] || echo "VS Code here: $(ext_list local.txt | tr '\n' ' '); defaultExtensions: $(ext_list remote.txt | tr '\n' ' ')"
    exit 0
fi

upload_bundle user
if [ "$SKIP_SYSTEM" = 0 ] && [ "$ROOT_LOGIN" = 0 ]; then
    say "System stage on $NAME"
    "${SSH[@]}" ${TTY[@]+"${TTY[@]}"} "$NAME" "$RCMD system"
fi
RHOME=$("${SSH[@]}" -n "$NAME" 'printf %s "$HOME"')
if [ -n "$RESTORE" ]; then
    say "Uploading $(basename "$RESTORE") ($(du -h "$RESTORE" | cut -f1))"
    "${SSH[@]}" "$NAME" 'umask 077; cat > ~/.cache/hermes-deploy/restore.tar.gz' < "$RESTORE"
    USER_ARGS+=(--restore "$RHOME/.cache/hermes-deploy/restore.tar.gz")
fi
args=""; for a in "${USER_ARGS[@]}" --vscode-settings "$VSC" --acp-name "Hermes Remote"; do args+=" $(q "$a")"; done
rc=0; "${SSH[@]}" ${TTY[@]+"${TTY[@]}"} "$NAME" "$RCMD user$args" || rc=$?
[ -z "$RESTORE" ] || "${SSH[@]}" -n "$NAME" 'rm -f ~/.cache/hermes-deploy/restore.tar.gz'
[ "$rc" = 0 ] || { echo "user stage failed on $NAME (exit $rc)" >&2; exit "$rc"; }

if [ "$LOCK" = 1 ]; then
    say "Closing $NAME to all but key-only SSH from ${ALLOW[*]}"
    hs=$(remote_dev_script harden-server.sh)
    "${LOCK_SSH[@]}" "$NAME" 'umask 077; mkdir -p ~/.cache/hermes-deploy && cat > ~/.cache/hermes-deploy/harden-server.sh' < "$hs"
    args=""; for a in "${ALLOW[@]}"; do args+=" --allow $(q "$a")"; done
    for a in "${ADMINS[@]}"; do args+=" --admin-user $(q "$a")"; done
    "${LOCK_SSH[@]}" ${TTY[@]+"${TTY[@]}"} "$NAME" "${SUDO}bash ~/.cache/hermes-deploy/harden-server.sh apply$args" \
        || { echo "closing $NAME failed; whatever it changed it undoes within 5 minutes" >&2; exit 6; }
    # A new TCP connection, never a multiplexed one, proves the allowlist lets this machine back in;
    # without the confirm the host rolls the change back by itself after 5 minutes.
    say "Checking that a new connection gets in (this confirms the lock)"
    if "${LOCK_SSH[@]}" ${TTY[@]+"${TTY[@]}"} -o ControlMaster=no -o ControlPath=none -o ConnectTimeout=20 \
        "$NAME" "${SUDO}/usr/local/sbin/hermes-hardening confirm"; then
        echo "locked: inbound is key-only SSH from ${ALLOW[*]}"
    else
        echo "a new connection did not get in: $NAME undoes the firewall and sshd changes within 5 minutes." >&2
        echo "Wait for that, check --allow, then rerun this same command." >&2
        exit 6
    fi
fi

WS="${WORKSPACE:-${DIR:-$RHOME/repos/hermes-agent}}"; WS="${WS/#\~/$RHOME}"
if [ "$IDE" = code ]; then
    if [ "$WSL" = 1 ]; then
        # VS Code on Windows dials SSH with Windows OpenSSH: its own ~/.ssh/config and key. Authorize that key
        # from here (this login works), then let the PowerShell half configure the Windows side.
        say "VS Code on Windows"
        # From a \\wsl$ working directory cmd.exe falls back to C:\Windows and warns; run Windows tools from /mnt/c.
        WINHOME=$(wslpath "$(cd /mnt/c && cmd.exe /c 'echo %USERPROFILE%' 2>/dev/null | tr -d '\r')")
        WINKEY=""
        # The key of the same name as this side's first, then the default names; both halves must be there.
        KEYNAME=$("${SSH[@]}" -G "$NAME" 2>/dev/null | awk '$1 == "identityfile" {print $2; exit}')
        for k in "$(basename "${KEYNAME:-id_ed25519}")" id_ed25519 id_ecdsa id_rsa; do
            [ -f "$WINHOME/.ssh/$k" ] && [ -f "$WINHOME/.ssh/$k.pub" ] && { WINKEY="$WINHOME/.ssh/$k"; break; }
        done
        if [ -z "$WINKEY" ]; then
            echo "no key in $WINHOME/.ssh: in PowerShell run ssh-keygen -t ed25519, then rerun this script"
        else
            "${SSH[@]}" "$NAME" 'umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; k=$(cat); grep -qxF "$k" ~/.ssh/authorized_keys || printf "%s\n" "$k" >> ~/.ssh/authorized_keys' < "$WINKEY.pub"
            echo "Windows key $(basename "$WINKEY") authorized for $RUSER@$NAME"
            HN=$("${SSH[@]}" -G "$NAME" 2>/dev/null | awk '$1 == "hostname" {print $2}')
            PT=$("${SSH[@]}" -G "$NAME" 2>/dev/null | awk '$1 == "port" {print $2}')
            # Windows PowerShell refuses scripts on \\wsl$ ("AuthorizationManager check failed"): run a copy from %TEMP%.
            WINTMP=$(wslpath "$(cd /mnt/c && cmd.exe /c 'echo %TEMP%' 2>/dev/null | tr -d '\r')")/hermes-deploy-$$
            mkdir -p "$WINTMP"
            (cd "$BUNDLE" && cp --parents "${FILES[@]}" "$WINTMP/")
            ps=(-NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w "$WINTMP/hermes-deploy.ps1")" -IdeOnly
                -Name "$NAME" -HostName "$HN" -User "$RUSER" -Port "$PT" -Key "$(wslpath -w "$WINKEY")" -Workspace "$WS")
            [ "$NO_OPEN" = 0 ] || ps+=(-NoOpen)
            (cd /mnt/c && powershell.exe "${ps[@]}") \
                || echo "the Windows half failed — rerun it in PowerShell: hermes-deploy.ps1 -IdeOnly -Name $NAME -HostName $HN -User $RUSER" >&2
            rm -rf "$WINTMP"
        fi
    else
        say "VS Code"
        CLI=$(code_cli || true)
        if [ -z "$CLI" ]; then
            echo "no 'code' command: install VS Code and run 'Shell Command: Install code in PATH', then rerun"
        else
            # shellcheck disable=SC2046  # extension IDs have no spaces: one word each
            install_exts "$CLI" $(ext_list local.txt)
            REMOTE_EXTS=$(ext_list remote.txt) python3 -c '
import json, os, sys
patch = {"remote.SSH.defaultExtensions": os.environ["REMOTE_EXTS"].split(),
         "remote.SSH.remotePlatform": {sys.argv[1]: "linux"}}
if sys.argv[2]:
    patch["remote.SSH.configFile"] = sys.argv[2]
print(json.dumps(patch))' "$NAME" "${SSH_CFG:+$(cd "$(dirname "$SSH_CFG")" && pwd)/$(basename "$SSH_CFG")}" \
                | python3 "$BUNDLE/vscode_settings.py" "$(user_settings)" -
            [ "$NO_OPEN" = 1 ] || "$CLI" --folder-uri "vscode-remote://ssh-remote+$NAME$WS" || true
        fi
    fi
fi

say "Done: $NAME:$WS"
echo "Sign in once per CLI in a VS Code terminal on $NAME (Terminal → New Terminal), or over ssh -t $NAME:"
echo "  claude auth login     codex login --device-auth     agy"
echo "Then: hermes remote doctor $NAME --workspace $WS · hermes remote claude $NAME --workspace $WS"
if [ "$LOCK" = 1 ]; then
    login=""; [ "$ROOT_LOGIN" = 0 ] || login="-o User=$ADMIN "
    echo "SSH only from ${ALLOW[*]}. Another address: rerun this command with the full --allow list."
    echo "Undo: ssh -t $login$NAME ${SUDO}/usr/local/sbin/hermes-hardening rollback"
fi
