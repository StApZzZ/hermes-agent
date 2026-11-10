#!/usr/bin/env bash
# Lock a Hermes remote-development server down to key-only SSH from an allowlist (VPN exits).
# Debian/Ubuntu and the RHEL family (Oracle Linux, Rocky, Alma, CentOS Stream, Fedora) with systemd;
# hermes-deploy --lock runs it at the end of a deploy.
#
#   harden-server.sh apply --allow <ipv4|ipv6[/len]> [--allow ...] [--admin-user NAME ...]
#   harden-server.sh confirm      # keep the new state; cancels the automatic rollback
#   harden-server.sh rollback     # restore the pre-apply sshd/nftables state
#   harden-server.sh public-port add|remove <port>   # a TCP port open to every address (HTTPS for a
#                                 # Telegram Mini App); kept across later applies. SSH stays as it is.
#
# `apply` arms a rollback that fires after 5 minutes unless `confirm` runs from a NEW SSH
# session, so a wrong allowlist cannot lock the operator out. Outbound traffic stays open:
# the subscription CLIs, git and package managers need it. firewalld or ufw, when present, stay as
# they are: a packet has to pass every table, so this one only narrows them. Run as root on the server.
set -euo pipefail

STATE=/var/lib/hermes-hardening
SELF=/usr/local/sbin/hermes-hardening
TIMER=hermes-hardening-rollback
SSHD_DROPIN=/etc/ssh/sshd_config.d/00-hermes-hardening.conf
# One port per line: TCP ports open to everyone (public-port); the allowlist apply last wrote.
PUBLIC_PORTS=$STATE/public-ports
ALLOWED=$STATE/allow

die() { echo "harden-server: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "run as root"
command -v systemctl >/dev/null || die "systemd is required"
# NFT: the file nftables.service loads at boot.
if command -v apt-get >/dev/null; then FAMILY=debian NFT=/etc/nftables.conf
elif command -v dnf >/dev/null; then FAMILY=rhel NFT=/etc/sysconfig/nftables.conf
else die "Debian/Ubuntu (apt) or the RHEL family (dnf) only"
fi
# Debian names the unit ssh (sshd is an alias only while it is enabled), the RHEL family sshd.
if systemctl cat ssh.service >/dev/null 2>&1; then SSH_UNIT=ssh; else SSH_UNIT=sshd; fi

rollback() {
    [ -d "$STATE/backup" ] || die "no backup in $STATE/backup"
    nft delete table inet hermes_fw 2>/dev/null || true
    rm -f "$SSHD_DROPIN"
    cp -a "$STATE/backup/sshd_config.d/." /etc/ssh/sshd_config.d/
    if [ -f "$STATE/backup/sshd_config" ]; then cp -a "$STATE/backup/sshd_config" /etc/ssh/sshd_config; fi
    if [ -f "$STATE/backup/nftables.conf" ]; then cp -a "$STATE/backup/nftables.conf" "$NFT"; else rm -f "$NFT"; fi
    # Never --now: stopping nftables.service runs `nft flush ruleset`, which would also wipe firewalld's
    # and iptables' rules and leave the host open until a reboot.
    [ -f "$STATE/backup/nftables.enabled" ] || systemctl disable --quiet nftables.service 2>/dev/null || true
    sshd -t && systemctl try-reload-or-restart "$SSH_UNIT"
    echo "harden-server: rolled back to the pre-apply state"
}

# The ports sshd listens on now (a deploy with --port moves it off 22), comma-separated for nft.
ssh_ports() { sshd -T 2>/dev/null | awk '$1 == "port" {print $2}' | sort -un | paste -sd, -; }

write_nft() {
    local a v4=() v6=() set4="" set6="" ports public=""
    for a in "$@"; do case $a in *:*) v6+=("$a") ;; *) v4+=("$a") ;; esac; done
    [ ${#v4[@]} -eq 0 ] || set4="elements = { $(IFS=,; echo "${v4[*]}") }"
    [ ${#v6[@]} -eq 0 ] || set6="elements = { $(IFS=,; echo "${v6[*]}") }"
    ports=$(ssh_ports); [ -n "$ports" ] || die "cannot read the sshd port (sshd -T)"
    [ ! -s "$PUBLIC_PORTS" ] || public="elements = { $(sort -un "$PUBLIC_PORTS" | paste -sd, -) }"
    cat > "$NFT.new" <<EOF
#!/usr/sbin/nft -f
# Managed by hermes-hardening. Only this table is (re)loaded; other tables are left alone.
table inet hermes_fw
delete table inet hermes_fw
table inet hermes_fw {
    set ssh_allow {
        type ipv4_addr; flags interval
        $set4
    }
    set ssh_allow6 {
        type ipv6_addr; flags interval
        $set6
    }
    set public_tcp {
        type inet_service
        $public
    }
    chain input {
        type filter hook input priority filter; policy drop;
        iif "lo" accept
        ct state invalid drop
        ct state established,related accept
        ip saddr @ssh_allow tcp dport { $ports } accept
        ip6 saddr @ssh_allow6 tcp dport { $ports } accept
        tcp dport @public_tcp accept
        ip saddr @ssh_allow icmp type echo-request accept
        ip6 saddr @ssh_allow6 icmpv6 type echo-request accept
        icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-solicit, nd-router-advert } ip6 hoplimit 255 accept
        counter drop
    }
    chain forward {
        type filter hook forward priority filter; policy drop;
    }
}
EOF
    nft -c -f "$NFT.new" || { rm -f "$NFT.new"; die "nft rejected the ruleset (a malformed --allow address?)"; }
}

# OpenSSH reads sshd_config.d only where sshd_config includes it: not on RHEL 8 / Oracle Linux 8 or
# Debian 10. At the top, so the drop-in's values come first.
include_dropins() {
    grep -qiE '^[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config\.d/' /etc/ssh/sshd_config && return
    sed -i '1i Include /etc/ssh/sshd_config.d/*.conf' /etc/ssh/sshd_config
}

write_sshd() {   # $@: the users besides root that may log in
    include_dropins
    cat > "$SSHD_DROPIN" <<EOF
# Managed by hermes-hardening. sshd takes the first value it reads, and 00- sorts first.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
AuthenticationMethods publickey
PermitRootLogin prohibit-password
AllowUsers $* root
MaxAuthTries 3
LoginGraceTime 20
X11Forwarding no
AllowAgentForwarding no
PermitTunnel no
GatewayPorts no
# VS Code Remote-SSH and port previews use local (-L) forwards only.
AllowTcpForwarding local
ClientAliveInterval 30
ClientAliveCountMax 4
EOF
    # cloud-init ships PasswordAuthentication yes; keep a later file from contradicting the policy.
    for f in /etc/ssh/sshd_config.d/*cloud-init*.conf; do
        [ -f "$f" ] && sed -i -E 's/^\s*PasswordAuthentication\s+yes/PasswordAuthentication no/' "$f"
    done
    sshd -t
    sshd -T 2>/dev/null | grep -x 'passwordauthentication no' >/dev/null || die "sshd does not read $SSHD_DROPIN"
}

install_packages() {
    if [ "$FAMILY" = debian ]; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        apt-get install -y -qq nftables unattended-upgrades >/dev/null
    else
        dnf install -y -q nftables >/dev/null
        # Fedora's dnf5 ships it as dnf5-plugin-automatic: security updates are a bonus, not a reason to stop.
        dnf install -y -q dnf-automatic >/dev/null 2>&1 || echo "harden-server: no dnf-automatic, security updates stay manual" >&2
    fi
}

auto_updates() {
    if [ "$FAMILY" = debian ]; then
        cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
    elif [ -f /etc/dnf/automatic.conf ]; then
        sed -i -E 's/^(upgrade_type[[:space:]]*=).*/\1 security/; s/^(apply_updates[[:space:]]*=).*/\1 yes/' /etc/dnf/automatic.conf
        systemctl enable --quiet --now dnf-automatic.timer
    fi
}

apply() {
    local allow=() admins=() a root_way=0
    while [ $# -gt 0 ]; do
        case $1 in
            --allow) allow+=("$2"); shift 2 ;;
            --admin-user) admins+=("$2"); shift 2 ;;
            *) die "unknown argument $1" ;;
        esac
    done
    [ ${#allow[@]} -gt 0 ] || die "at least one --allow address is required"
    [ ${#admins[@]} -gt 0 ] || admins=(hermes)
    for a in "${admins[@]}"; do
        id "$a" >/dev/null 2>&1 || die "admin user $a does not exist"
        [ -s "$(getent passwd "$a" | cut -d: -f6)/.ssh/authorized_keys" ] \
            || die "$a has no authorized_keys; refusing to disable passwords"
        if sudo -n -l -U "$a" 2>/dev/null | grep "may run" >/dev/null; then root_way=1; fi
    done
    # Root keeps a way in: an allowed user's sudo (Oracle Cloud's opc, a deploy without --root-login) or
    # root's own key. A cloud root key that only prints "log in as opc" is no way in.
    if [ "$root_way" = 0 ] && [ -s /root/.ssh/authorized_keys ] && ! grep -q 'command=' /root/.ssh/authorized_keys; then
        root_way=1
    fi
    [ "$root_way" = 1 ] || die "no allowed user has sudo and root has no usable key; refusing to disable passwords"

    mkdir -p "$STATE"; chmod 700 "$STATE"
    if [ ! -d "$STATE/backup" ]; then
        mkdir -p "$STATE/backup/sshd_config.d" /etc/ssh/sshd_config.d
        cp -a /etc/ssh/sshd_config.d/. "$STATE/backup/sshd_config.d/"
        [ -f "$NFT" ] && cp -a "$NFT" "$STATE/backup/nftables.conf"
        systemctl is-enabled --quiet nftables.service 2>/dev/null && touch "$STATE/backup/nftables.enabled"
    fi
    # A backup older than the Include handling lacks it; sshd_config is still the original then.
    [ -f "$STATE/backup/sshd_config" ] || cp -a /etc/ssh/sshd_config "$STATE/backup/sshd_config"
    install -m 700 "$0" "$SELF"
    install_packages

    # Checked before the rollback is armed, so a bad address changes nothing.
    write_nft "${allow[@]}"
    printf '%s\n' "${allow[@]}" > "$ALLOWED"

    systemctl stop "$TIMER.timer" 2>/dev/null || true
    systemctl reset-failed "$TIMER.service" "$TIMER.timer" 2>/dev/null || true
    systemd-run --quiet --unit "$TIMER" --on-active=300 "$SELF" rollback

    mv "$NFT.new" "$NFT"
    write_sshd "${admins[@]}"
    nft -f "$NFT"
    systemctl enable --quiet nftables.service
    # Ubuntu 24.04 starts sshd from ssh.socket on demand: an inactive ssh.service reads the file when it starts.
    systemctl try-reload-or-restart "$SSH_UNIT"

    # Cloud images re-assert ssh_pwauth on some reprovisioning paths.
    if [ -d /etc/cloud/cloud.cfg.d ]; then printf 'ssh_pwauth: false\n' > /etc/cloud/cloud.cfg.d/99-hermes-hardening.cfg; fi
    # Image-provided account nobody uses: no password, no shell, expired.
    if id tmpuser >/dev/null 2>&1; then usermod -L -e 1 -s /usr/sbin/nologin tmpuser; fi
    auto_updates
    mkdir -p /etc/sysctl.d
    cat > /etc/sysctl.d/90-hermes-hardening.conf <<'EOF'
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv4.conf.all.rp_filter = 1
net.ipv4.tcp_syncookies = 1
net.ipv4.icmp_echo_ignore_broadcasts = 1
kernel.dmesg_restrict = 1
kernel.kptr_restrict = 2
EOF
    sysctl -q --system >/dev/null
    echo "harden-server: applied; it rolls back in 5 minutes unless '$SELF confirm' runs from a NEW ssh session"
}

# The allowlist in force: what apply last wrote, else (a host locked before it was recorded) the live sets.
current_allow() {
    if [ -s "$ALLOWED" ]; then cat "$ALLOWED"; return; fi
    local set
    for set in ssh_allow ssh_allow6; do
        nft list set inet hermes_fw "$set" 2>/dev/null | tr -d ' \t\n' \
            | sed -n 's/.*elements={\([^}]*\)}.*/\1/p' | tr , '\n'
    done
}

# Opening or closing a public port leaves SSH as it is, so no rollback is armed.
public_port() {
    local action=${1:-} port=${2:-} allow
    case $port in ''|*[!0-9]*) die "usage: $0 public-port add|remove <port>" ;; esac
    [ "$port" -ge 1 ] && [ "$port" -le 65535 ] || die "port out of range: $port"
    nft list table inet hermes_fw >/dev/null 2>&1 || die "the host is not locked (run apply first)"
    install -m 700 "$0" "$SELF"   # an older installed copy has no public_tcp set to roll forward
    mapfile -t allow < <(current_allow)
    [ ${#allow[@]} -gt 0 ] || die "cannot read the current SSH allowlist"
    touch "$PUBLIC_PORTS"
    case $action in
        add) grep -qx "$port" "$PUBLIC_PORTS" || echo "$port" >> "$PUBLIC_PORTS" ;;
        remove) sed -i "/^$port\$/d" "$PUBLIC_PORTS" ;;
        *) die "usage: $0 public-port add|remove <port>" ;;
    esac
    write_nft "${allow[@]}"
    mv "$NFT.new" "$NFT"
    nft -f "$NFT"
    echo "harden-server: TCP open to everyone: $(sort -un "$PUBLIC_PORTS" | paste -sd' ' -)"
}

case ${1:-} in
    apply) shift; apply "$@" ;;
    public-port) shift; public_port "$@" ;;
    confirm) systemctl stop "$TIMER.timer" 2>/dev/null || true; echo "harden-server: confirmed, rollback disarmed" ;;
    rollback) rollback ;;
    *) die "usage: $0 apply --allow <ip> [--allow ...] [--admin-user NAME ...] | confirm | rollback | public-port add|remove <port>" ;;
esac
