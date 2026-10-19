#!/bin/bash
# Lock a Hermes remote-development server down to key-only SSH from an allowlist (VPN exits).
#
#   harden-server.sh apply --allow <ipv4[/len]> [--allow ...] [--admin-user NAME]
#   harden-server.sh confirm      # keep the new state; cancels the automatic rollback
#   harden-server.sh rollback     # restore the pre-apply sshd/nftables state
#
# `apply` arms a rollback that fires after 5 minutes unless `confirm` runs from a NEW SSH
# session, so a wrong allowlist cannot lock the operator out. Outbound traffic stays open:
# the subscription CLIs, git and package managers need it. Run as root on the server.
set -euo pipefail

STATE=/var/lib/hermes-hardening
SELF=/usr/local/sbin/hermes-hardening
TIMER=hermes-hardening-rollback
SSHD_DROPIN=/etc/ssh/sshd_config.d/00-hermes-hardening.conf
NFT=/etc/nftables.conf

die() { echo "harden-server: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "run as root"

rollback() {
    [ -d "$STATE/backup" ] || die "no backup in $STATE/backup"
    nft delete table inet hermes_fw 2>/dev/null || true
    rm -f "$SSHD_DROPIN"
    cp -a "$STATE/backup/sshd_config.d/." /etc/ssh/sshd_config.d/
    if [ -f "$STATE/backup/nftables.conf" ]; then cp -a "$STATE/backup/nftables.conf" "$NFT"; else rm -f "$NFT"; fi
    [ -f "$STATE/backup/nftables.enabled" ] || systemctl disable --now nftables.service 2>/dev/null || true
    sshd -t && systemctl reload ssh
    echo "harden-server: rolled back to the pre-apply state"
}

write_nft() {
    local allow; allow=$(IFS=,; echo "$*")
    cat > "$NFT.new" <<EOF
#!/usr/sbin/nft -f
# Managed by hermes-hardening. Only this table is (re)loaded; other tables are left alone.
table inet hermes_fw
delete table inet hermes_fw
table inet hermes_fw {
    set ssh_allow {
        type ipv4_addr; flags interval
        elements = { $allow }
    }
    chain input {
        type filter hook input priority filter; policy drop;
        iif "lo" accept
        ct state invalid drop
        ct state established,related accept
        ip saddr @ssh_allow tcp dport 22 accept
        ip saddr @ssh_allow icmp type echo-request accept
        icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-solicit, nd-router-advert } ip6 hoplimit 255 accept
        counter drop
    }
    chain forward {
        type filter hook forward priority filter; policy drop;
    }
}
EOF
    nft -c -f "$NFT.new"
    mv "$NFT.new" "$NFT"
}

write_sshd() {
    local admin=$1
    cat > "$SSHD_DROPIN" <<EOF
# Managed by hermes-hardening. sshd takes the first value it reads, and 00- sorts first.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
AuthenticationMethods publickey
PermitRootLogin prohibit-password
AllowUsers $admin root
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
}

apply() {
    local allow=() admin=hermes
    while [ $# -gt 0 ]; do
        case $1 in
            --allow) allow+=("$2"); shift 2 ;;
            --admin-user) admin=$2; shift 2 ;;
            *) die "unknown argument $1" ;;
        esac
    done
    [ ${#allow[@]} -gt 0 ] || die "at least one --allow address is required"
    id "$admin" >/dev/null 2>&1 || die "admin user $admin does not exist"
    for user in "$admin" root; do
        home=$(getent passwd "$user" | cut -d: -f6)
        [ -s "$home/.ssh/authorized_keys" ] || die "$user has no authorized_keys; refusing to disable passwords"
    done

    mkdir -p "$STATE"; chmod 700 "$STATE"
    if [ ! -d "$STATE/backup" ]; then
        mkdir -p "$STATE/backup/sshd_config.d"
        cp -a /etc/ssh/sshd_config.d/. "$STATE/backup/sshd_config.d/"
        [ -f "$NFT" ] && cp -a "$NFT" "$STATE/backup/nftables.conf"
        systemctl is-enabled --quiet nftables.service 2>/dev/null && touch "$STATE/backup/nftables.enabled"
    fi
    install -m 700 "$0" "$SELF"

    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq nftables unattended-upgrades >/dev/null

    systemctl stop "$TIMER.timer" 2>/dev/null || true
    systemctl reset-failed "$TIMER.service" "$TIMER.timer" 2>/dev/null || true
    systemd-run --quiet --unit "$TIMER" --on-active=300 "$SELF" rollback

    write_nft "${allow[@]}"
    write_sshd "$admin"
    nft -f "$NFT"
    systemctl enable --quiet nftables.service
    systemctl reload ssh

    # Cloud images re-assert ssh_pwauth on some reprovisioning paths.
    printf 'ssh_pwauth: false\n' > /etc/cloud/cloud.cfg.d/99-hermes-hardening.cfg
    # Image-provided account nobody uses: no password, no shell, expired.
    if id tmpuser >/dev/null 2>&1; then usermod -L -e 1 -s /usr/sbin/nologin tmpuser; fi
    cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
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
    echo "harden-server: applied; open a NEW ssh session and run '$SELF confirm' within 5 minutes"
}

case ${1:-} in
    apply) shift; apply "$@" ;;
    confirm) systemctl stop "$TIMER.timer" 2>/dev/null || true; echo "harden-server: confirmed, rollback disarmed" ;;
    rollback) rollback ;;
    *) die "usage: $0 apply --allow <ip> [--allow ...] [--admin-user NAME] | confirm | rollback" ;;
esac
