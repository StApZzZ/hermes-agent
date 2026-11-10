#!/usr/bin/env bash
# Open Hermes in the browser on a server closed by harden-server.sh:
#   - https://<domain>/ the Webapp (the Desktop workspace), https://<domain>/panel/ the web dashboard,
#     through Angie with a Let's Encrypt certificate over TLS-ALPN (port 80 stays closed);
#   - password sign-in only from your VPN addresses (dashboard.auth_networks.basic);
#   - a Telegram Mini App in your bot's menu that signs in only your Telegram account
#     (dashboard.telegram_mini_app.allowed_users), from any network.
# Debian/Ubuntu (Angie comes from its own apt repository). Run as root on the server.
#
#   web-access.sh apply [--domain NAME] [--vpn ADDR[,ADDR]] [--telegram-user ID] [--user hermes] [--reset-password]
#   web-access.sh status [--user hermes]
#   web-access.sh remove [--user hermes]   # Angie site and port 443 off, Webapp and dashboard off, menu button back
#
# Defaults: --vpn is the SSH allowlist harden-server.sh applied (your VPN exits), --telegram-user the one id in
# TELEGRAM_ALLOWED_USERS, --domain <public IPv4 with dashes>.sslip.io. A rerun keeps the password unless
# --reset-password; it is printed once, when it is set.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ANGIE_SITE=/etc/angie/http.d/hermes-web-access.conf
MARK="# Managed by hermes web-access.sh"
PORT=9120 # webapp.port default; the gateway's Webapp listener
DASH_PORT=9119
DASH_UNIT=hermes-dashboard.service # the name hermes update restarts
HARDEN_STATE=/var/lib/hermes-hardening

die() { echo "web-access: $*" >&2; exit 1; }
say() { printf '\n==> %s\n' "$*"; }
[ "$(id -u)" = 0 ] || die "run as root"

ACTION=${1:-}; [ $# -eq 0 ] || shift
DOMAIN="" VPN=() TG_USER="" HUSER=hermes RESET=0
while [ $# -gt 0 ]; do
    case $1 in
        --domain) DOMAIN=$2; shift 2 ;;
        --vpn) IFS=, read -ra a <<<"$2"; VPN+=("${a[@]}"); shift 2 ;;
        --telegram-user) TG_USER=$2; shift 2 ;;
        --user) HUSER=$2; shift 2 ;;
        --reset-password) RESET=1; shift ;;
        *) die "unknown argument $1" ;;
    esac
done

id "$HUSER" >/dev/null 2>&1 || die "no user $HUSER (--user)"
HHOME=$(getent passwd "$HUSER" | cut -d: -f6)
ENV_FILE=$HHOME/.hermes/.env
[ -x "$HHOME/.local/bin/hermes" ] || die "hermes is not installed for $HUSER ($HHOME/.local/bin/hermes)"

# hermes as the Hermes user, in a login shell (its PATH); arguments are quoted for that shell.
hermes_as() { runuser -l "$HUSER" -c "$(printf '%q ' hermes "$@")"; }
# systemctl --user for the Hermes user (lingering, so its units run without a login session).
user_systemctl() { systemctl --user -M "$HUSER@" "$@"; }

# One .env value of the Hermes user, never on a command line.
env_value() {
    [ -r "$ENV_FILE" ] || return 0
    sed -n "s/^[[:space:]]*\(export[[:space:]]\+\)\?$1[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" | tail -1 \
        | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

# The SSH allowlist harden-server.sh applied: what it recorded, else the live nftables sets.
hardening_allowlist() {
    if [ -s "$HARDEN_STATE/allow" ]; then cat "$HARDEN_STATE/allow"; return; fi
    local set
    for set in ssh_allow ssh_allow6; do
        nft list set inet hermes_fw "$set" 2>/dev/null | tr -d ' \t\n' \
            | sed -n 's/.*elements={\([^}]*\)}.*/\1/p' | tr , '\n'
    done
}

public_ipv4() {
    local ip
    ip=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')
    case $ip in ''|10.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*|100.6[4-9].*|100.[7-9][0-9].*|100.1[01][0-9].*|100.12[0-7].*) return 1 ;; esac
    echo "$ip"
}

# Angie from its own repository (https://angie.software): its acme module gets the certificate itself.
install_angie() {
    command -v angie >/dev/null && return
    say "Installing Angie"
    command -v apt-get >/dev/null || die "Angie is installed here from its apt repository: Debian/Ubuntu only"
    local id version codename
    id=$(. /etc/os-release && echo "$ID") version=$(. /etc/os-release && echo "$VERSION_ID")
    codename=$(. /etc/os-release && echo "$VERSION_CODENAME")
    export DEBIAN_FRONTEND=noninteractive
    apt-get install -y -qq ca-certificates curl >/dev/null
    curl -fsSL -o /etc/apt/trusted.gpg.d/angie-signing.gpg https://angie.software/keys/angie-signing.gpg
    echo "deb https://download.angie.software/angie/$id/$version $codename main" > /etc/apt/sources.list.d/angie.list
    apt-get update -qq
    apt-get install -y -qq angie >/dev/null
}

# The proxy headers, repeated per location: a location that sets any proxy_set_header inherits none.
# X-Forwarded-For is the peer address, never the client's own header; Hermes trusts it from loopback only.
proxy_headers() {
    cat <<'HDR'
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $hermes_connection_upgrade;
HDR
}

write_angie_site() {
    [ -d "$(dirname "$ANGIE_SITE")" ] || die "$(dirname "$ANGIE_SITE") is missing: this Angie does not use http.d"
    grep -qE "include[[:space:]]+/etc/angie/http\.d/\*\.conf" /etc/angie/angie.conf \
        || die "/etc/angie/angie.conf does not include /etc/angie/http.d/*.conf"
    if [ -f "$ANGIE_SITE" ] && ! grep -qxF "$MARK" "$ANGIE_SITE"; then
        die "$ANGIE_SITE exists and is not managed by this script"
    fi
    # TLS-ALPN on 443 alone gets the certificate: port 80 (HTTP-01) stays closed.
    {
        echo "$MARK"
        cat <<EOF
acme_client hermes https://acme-v02.api.letsencrypt.org/directory challenge=alpn;

map \$http_upgrade \$hermes_connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 443 ssl;
    server_name $DOMAIN;
    acme hermes;
    ssl_certificate \$acme_cert_hermes;
    ssl_certificate_key \$acme_cert_key_hermes;

    client_max_body_size 512m;
    proxy_buffering off;
    proxy_read_timeout 1h;
    proxy_send_timeout 1h;

    location = /panel {
        return 308 /panel/;
    }
    location /panel/ {
        proxy_pass http://127.0.0.1:$DASH_PORT/;
$(proxy_headers)
        proxy_set_header X-Forwarded-Prefix /panel;
    }
    location / {
        proxy_pass http://127.0.0.1:$PORT;
$(proxy_headers)
    }
}
EOF
    } > "$ANGIE_SITE.new"
    mv "$ANGIE_SITE.new" "$ANGIE_SITE"
    if ! angie -t -q; then
        rm -f "$ANGIE_SITE"
        die "angie rejected the site (angie -t above); removed $ANGIE_SITE"
    fi
    systemctl enable --quiet angie
    if systemctl is-active --quiet angie; then systemctl reload angie; else systemctl start angie; fi
}

open_https() {
    if nft list table inet hermes_fw >/dev/null 2>&1; then
        "$HERE/harden-server.sh" public-port add 443
    else
        echo "web-access: this host has no hermes-hardening firewall; make TCP 443 reachable from anywhere yourself"
    fi
}

# The web dashboard as a user service of the Hermes user, on loopback behind Angie.
dashboard_service() {
    local dir=$HHOME/.config/systemd/user env_path
    runuser -u "$HUSER" -- mkdir -p "$dir"
    # The gateway unit's PATH carries Hermes's own Node, which the dashboard needs to build its UI.
    env_path=$(grep -m1 '^Environment="PATH=' "$dir/hermes-gateway.service" 2>/dev/null || true)
    cat > "$dir/$DASH_UNIT" <<UNIT
$MARK
[Unit]
Description=Hermes web dashboard (https://$DOMAIN/panel/ through Angie)
After=network-online.target hermes-gateway.service

[Service]
WorkingDirectory=$HHOME/.hermes
$env_path
Environment="HERMES_HOME=$HHOME/.hermes"
ExecStart=$HHOME/.local/bin/hermes dashboard --host 127.0.0.1 --port $DASH_PORT --no-open
Restart=always
RestartSec=10
# 78: a deliberate refusal (another backend owns this host); parking beats a restart loop.
RestartPreventExitStatus=78

[Install]
WantedBy=default.target
UNIT
    chown "$HUSER:" "$dir/$DASH_UNIT"
    user_systemctl daemon-reload
    user_systemctl enable --quiet "$DASH_UNIT"
    user_systemctl restart "$DASH_UNIT"
}

# Password sign-in for the browser on the VPN. Kept on a rerun unless --reset-password.
set_password() {
    local user password hash secret
    user=$(hermes_as config get dashboard.basic_auth.username 2>/dev/null | tail -1 || true)
    if [ -n "$user" ] && [ "$RESET" = 0 ]; then
        PASSWORD_NOTE="unchanged (user $user; --reset-password sets a new one)"
        return
    fi
    user=${user:-admin}
    password=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')
    # The scrypt$n$r$p$salt$key form plugins/dashboard_auth/basic verifies (its hash_password).
    hash=$(PW=$password python3 -c '
import base64, hashlib, os, secrets
salt = secrets.token_bytes(16)
key = hashlib.scrypt(os.environ["PW"].encode(), salt=salt, n=2**14, r=8, p=1, dklen=32, maxmem=0)
print("scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(key).decode())')
    secret=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
    hermes_as config set dashboard.basic_auth.username "$user" >/dev/null
    hermes_as config set dashboard.basic_auth.password_hash "$hash" >/dev/null
    hermes_as config set dashboard.basic_auth.password "" >/dev/null
    hermes_as config set dashboard.basic_auth.secret "$secret" >/dev/null
    PASSWORD_NOTE="user $user, password $password   <- shown once; store it in your password manager"
}

# The bot's menu button for one chat: the Mini App, or (no url) Telegram's default. The token stays in .env.
menu_button() {
    local chat=$1 url=${2:-}
    TOKEN=$(env_value TELEGRAM_BOT_TOKEN) CHAT=$chat URL=$url python3 - <<'EOF'
import json, os, sys, urllib.request
button = {"type": "web_app", "text": "Hermes", "web_app": {"url": os.environ["URL"]}} if os.environ["URL"] \
    else {"type": "default"}
body = json.dumps({"chat_id": int(os.environ["CHAT"]), "menu_button": button}).encode()
req = urllib.request.Request(f"https://api.telegram.org/bot{os.environ['TOKEN']}/setChatMenuButton", body,
                             {"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=20) as resp:
        ok = json.load(resp).get("ok")
except Exception as exc:
    sys.exit(f"web-access: Telegram refused the menu button: {exc}")
sys.exit(0 if ok else "web-access: Telegram refused the menu button")
EOF
}

wait_for_https() {
    local i code
    for i in $(seq 1 40); do
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 --resolve "$DOMAIN:443:127.0.0.1" \
            "https://$DOMAIN/login" || true)
        case $code in 200|302|403) return 0 ;; esac
        sleep 3
    done
    return 1
}

apply() {
    [ -n "$(env_value TELEGRAM_BOT_TOKEN)" ] || die "TELEGRAM_BOT_TOKEN is not in $ENV_FILE: set up the Telegram gateway first"
    if [ ${#VPN[@]} -eq 0 ]; then mapfile -t VPN < <(hardening_allowlist); fi
    [ ${#VPN[@]} -gt 0 ] || die "no VPN addresses: pass --vpn ADDR[,ADDR]"
    if [ -z "$TG_USER" ]; then
        TG_USER=$(env_value TELEGRAM_ALLOWED_USERS | tr -d ' ')
        case $TG_USER in ''|*,*) die "pass --telegram-user <your numeric Telegram id> (TELEGRAM_ALLOWED_USERS has ${TG_USER:-none})" ;; esac
    fi
    case $TG_USER in *[!0-9]*|'') die "--telegram-user must be a numeric Telegram user id" ;; esac
    local ip vpn_json tg_url
    if [ -z "$DOMAIN" ]; then
        ip=$(public_ipv4) || die "no public IPv4 on this host: pass --domain"
        DOMAIN="${ip//./-}.sslip.io"
    fi
    vpn_json=$(printf '"%s",' "${VPN[@]}"); vpn_json="[${vpn_json%,}]"
    tg_url="https://$DOMAIN/auth/login?provider=telegram-mini-app"

    install_angie
    say "Hermes settings (user $HUSER)"
    hermes_as config set webapp.enabled true >/dev/null
    hermes_as config set webapp.public_url "https://$DOMAIN" >/dev/null
    hermes_as config set dashboard.public_url "https://$DOMAIN/panel" >/dev/null
    hermes_as config set dashboard.auth_networks "{\"basic\": $vpn_json}" >/dev/null
    hermes_as config set dashboard.telegram_mini_app.allowed_users "[$TG_USER]" >/dev/null
    set_password
    say "Building the Webapp (a few minutes the first time)"
    hermes_as webapp --build-only
    say "Restarting the gateway; starting the dashboard (it builds its UI the first time)"
    loginctl enable-linger "$HUSER"
    user_systemctl restart hermes-gateway.service
    dashboard_service
    say "Angie and the firewall"
    write_angie_site
    open_https
    say "Waiting for the certificate"
    wait_for_https || die "https://$DOMAIN does not answer yet; check: journalctl -u angie -n 50 and /var/log/angie/error.log"
    say "Telegram menu button"
    menu_button "$TG_USER" "$tg_url"

    cat <<EOF

Done.
  Webapp:       https://$DOMAIN/          (password sign-in from ${VPN[*]} only)
  Dashboard:    https://$DOMAIN/panel/    (the same sign-in)
  Sign-in:      $PASSWORD_NOTE
  Telegram:     open your bot, tap the "Hermes" menu button (Telegram account $TG_USER only, any network)
  Status:       $0 status        Undo: $0 remove
EOF
}

status() {
    echo "angie:    $(systemctl is-active angie 2>/dev/null || true)  $(sed -n 's/^ *server_name \(.*\);/\1/p' "$ANGIE_SITE" 2>/dev/null)"
    echo "firewall: $(nft list set inet hermes_fw public_tcp 2>/dev/null | tr -d '\n\t' | sed -n 's/.*elements = {\([^}]*\)}.*/TCP open to everyone: \1/p')"
    echo "dashboard: $(user_systemctl is-active "$DASH_UNIT" 2>/dev/null || true)"
    for key in webapp.enabled webapp.public_url dashboard.public_url dashboard.auth_networks \
               dashboard.telegram_mini_app.allowed_users dashboard.basic_auth.username; do
        printf '%-44s %s\n' "$key" "$(hermes_as config get "$key" 2>/dev/null | tr '\n' ' ')"
    done
    hermes_as webapp --status || true
}

remove() {
    # Angie stays installed and running: it may serve other sites.
    if [ -f "$ANGIE_SITE" ] && grep -qxF "$MARK" "$ANGIE_SITE"; then
        rm -f "$ANGIE_SITE"
        systemctl reload angie || true
    fi
    if nft list table inet hermes_fw >/dev/null 2>&1; then "$HERE/harden-server.sh" public-port remove 443; fi
    if [ -f "$HHOME/.config/systemd/user/$DASH_UNIT" ] && grep -qxF "$MARK" "$HHOME/.config/systemd/user/$DASH_UNIT"; then
        user_systemctl disable --now --quiet "$DASH_UNIT" || true
        rm -f "$HHOME/.config/systemd/user/$DASH_UNIT"
        user_systemctl daemon-reload
    fi
    hermes_as config set webapp.enabled false >/dev/null
    hermes_as config set dashboard.public_url "" >/dev/null
    user_systemctl restart hermes-gateway.service
    local tg
    tg=$(hermes_as config get dashboard.telegram_mini_app.allowed_users 2>/dev/null | tr -c '0-9' ' ' | awk '{print $1}' || true)
    if [ -n "$tg" ]; then menu_button "$tg" "" || true; fi
    echo "web-access: removed (password, networks and Telegram settings stay in config.yaml)"
}

case $ACTION in
    apply) apply ;;
    status) status ;;
    remove) remove ;;
    *) die "usage: $0 apply|status|remove [--domain NAME] [--vpn ADDR[,ADDR]] [--telegram-user ID] [--user NAME] [--reset-password]" ;;
esac
