#!/usr/bin/env bash
# Add a SECOND SSH port that only the listed users may use, leaving port 22 and every other account untouched.
# Meant for shared servers: other apps keep using 22; this app's deploy pipeline uses the new port.
#
#   sudo bash deploy/add-ssh-port.sh <port> <user> [user ...]     e.g.  add-ssh-port.sh 47531 narizmart hasan
#   sudo bash deploy/add-ssh-port.sh --undo                        remove it again
#
# What it does (Ubuntu 24.04 starts SSH through ssh.socket, which is fixed to port 22, so both parts are needed):
#   1. ssh.socket drop-in    -> listens on 22 AND the new port
#   2. sshd_config.d drop-in -> "Match LocalPort <port>  AllowUsers <users>" (affects only the new port)
#   3. ufw rule for the new port, and fail2ban watches it too
# Safety: configs are backed up; port-22 behaviour is compared before/after and the change is reverted if it differs;
# restarting the socket does not drop existing SSH sessions.
set -euo pipefail

[[ "$EUID" -eq 0 ]] || { echo "run as root (sudo)" >&2; exit 1; }
DROPIN_SOCKET_DIR=/etc/systemd/system/ssh.socket.d
DROPIN_SOCKET="$DROPIN_SOCKET_DIR/60-extra-port.conf"
DROPIN_SSHD=/etc/ssh/sshd_config.d/70-extra-port.conf
DROPIN_F2B=/etc/fail2ban/jail.d/zz-extra-ssh-port.conf
SOCKET_MODE=0
systemctl is-enabled --quiet ssh.socket 2>/dev/null && SOCKET_MODE=1

die() { echo "ERROR: $*" >&2; exit 1; }
restart_ssh() {
  if [[ "$SOCKET_MODE" == 1 ]]; then systemctl daemon-reload && systemctl restart ssh.socket; else systemctl reload ssh; fi
}
baseline() { sshd -T -C "user=nobody,host=localhost,addr=127.0.0.1,lport=22" | sort; }

if [[ "${1:-}" == "--undo" ]]; then
  PORT_OLD="$(grep -hoE 'LocalPort [0-9]+' "$DROPIN_SSHD" 2>/dev/null | head -1 | awk '{print $2}' || true)"
  rm -f "$DROPIN_SOCKET" "$DROPIN_SSHD" "$DROPIN_F2B"
  rmdir "$DROPIN_SOCKET_DIR" 2>/dev/null || true
  sshd -t || die "sshd config invalid after removal"
  restart_ssh
  if command -v fail2ban-client >/dev/null; then fail2ban-client reload >/dev/null 2>&1 || true; fi
  if [[ -n "$PORT_OLD" ]] && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw delete allow "$PORT_OLD"/tcp >/dev/null 2>&1 || true
  fi
  echo "removed the extra SSH port (port 22 unchanged)"
  exit 0
fi

PORT="${1:-}"; shift || true
USERS=("$@")
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1024 || PORT > 65535 )); then die "give a port between 1024 and 65535"; fi
[[ ${#USERS[@]} -ge 1 ]] || die "list at least one user allowed on the new port"
for u in "${USERS[@]}"; do id "$u" >/dev/null 2>&1 || die "user '$u' does not exist"; done
ss -ltn | awk '{print $4}' | grep -qE "[:.]$PORT\$" && die "port $PORT is already in use"

BACKUP="/root/ssh-port-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP"
cp -a /etc/ssh/sshd_config /etc/ssh/sshd_config.d "$BACKUP"/ 2>/dev/null || true
if [[ -d "$DROPIN_SOCKET_DIR" ]]; then cp -a "$DROPIN_SOCKET_DIR" "$BACKUP"/; fi
BEFORE="$(baseline)"
echo "backup: $BACKUP"

revert() {
  echo "reverting..." >&2
  rm -f "$DROPIN_SOCKET" "$DROPIN_SSHD" "$DROPIN_F2B"
  sshd -t && restart_ssh || echo "WARNING: could not restart ssh automatically; originals are in $BACKUP" >&2
}

# firewall first, so the new port is reachable the moment it opens
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow "$PORT"/tcp comment "ssh (extra port)" >/dev/null
  echo "ufw: allowed $PORT/tcp"
fi

# 2. sshd: restrict the new port to the listed users (Match applies only to connections on that port)
cat > "$DROPIN_SSHD" <<EOF
# Extra SSH port $PORT: only these users may log in on it. Port 22 is unaffected.
Match LocalPort $PORT
    AllowUsers ${USERS[*]}
EOF
sshd -t || { revert; die "sshd rejected the configuration"; }

# 1. listening sockets
if [[ "$SOCKET_MODE" == 1 ]]; then
  mkdir -p "$DROPIN_SOCKET_DIR"
  {
    echo "[Socket]"
    echo "ListenStream="
    systemctl show ssh.socket -p Listen | sed -nE 's/^Listen=(.*) \(Stream\)$/ListenStream=\1/p' | sort -u
    echo "ListenStream=0.0.0.0:$PORT"
    echo "ListenStream=[::]:$PORT"
  } > "$DROPIN_SOCKET"
else
  printf 'Port 22\nPort %s\n' "$PORT" >> "$DROPIN_SSHD"   # classic (non-socket) sshd
  sshd -t || { revert; die "sshd rejected the Port lines"; }
fi

# port 22 must behave exactly as before
AFTER="$(baseline)"
if [[ "$BEFORE" != "$AFTER" ]]; then
  diff <(echo "$BEFORE") <(echo "$AFTER") || true
  revert; die "port 22 settings would change; reverted"
fi
# the new port must allow the listed users only
for u in "${USERS[@]}"; do
  sshd -T -C "user=$u,host=localhost,addr=127.0.0.1,lport=$PORT" | grep -q "^allowusers .*$u" || { revert; die "AllowUsers check failed for $u"; }
done

restart_ssh
sleep 1
ss -ltn | grep -qE "[:.]$PORT " || { revert; die "nothing is listening on $PORT after restart"; }
ss -ltn | grep -qE "[:.]22 "   || { revert; die "port 22 stopped listening"; }

# 3. fail2ban should watch the new port too
if command -v fail2ban-client >/dev/null; then
  printf '[sshd]\nport = ssh,%s\n' "$PORT" > "$DROPIN_F2B"
  fail2ban-client reload >/dev/null 2>&1 && echo "fail2ban: sshd jail now watches 22 and $PORT" || echo "note: fail2ban reload failed, check it manually"
fi

echo
echo "OK: SSH now listens on 22 and $PORT."
echo "  port 22      : unchanged for everyone"
echo "  port $PORT : only ${USERS[*]}"
echo "  host key line for known_hosts on the new port:"
echo "    [$(hostname -I | awk '{print $1}')]:$PORT $(cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)"
echo "Check your cloud provider's own firewall (if it has one) allows TCP $PORT."
