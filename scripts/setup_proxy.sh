#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
ensure_access

if [[ ! -s "$SECRETS/app_password" ]]; then
  (umask 077; openssl rand -hex 16 > "$SECRETS/app_password")
fi
password="$(cat "$SECRETS/app_password")"
[[ "$password" =~ ^[0-9a-f]{32}$ ]] || { echo 'Invalid app password file' >&2; exit 1; }

# The account is created on the primary and replicated to both read replicas.
printf "CREATE USER IF NOT EXISTS 'log8415e_app'@'10.42.2.%%' IDENTIFIED BY '%s';\nALTER USER 'log8415e_app'@'10.42.2.%%' IDENTIFIED BY '%s';\nGRANT SELECT, INSERT, UPDATE, DELETE ON sakila.* TO 'log8415e_app'@'10.42.2.%%';\n" "$password" "$password" | ssh_base primary 'sudo mysql'

# Ensure the grant has reached both replicas before starting the proxy.
for node in replica1 replica2; do
  for attempt in $(seq 1 30); do
    if ssh_base "$node" "sudo mysql -N -e \"SELECT COUNT(*) FROM mysql.user WHERE user='log8415e_app';\"" | grep -qx 1; then
      break
    fi
    if [[ "$attempt" == 30 ]]; then
      echo "Application user not replicated to $node" >&2
      exit 1
    fi
    sleep 2
  done
done

ssh_base proxy 'sudo DEBIAN_FRONTEND=noninteractive apt-get update -o Acquire::ForceIPv4=true -o APT::Update::Error-Mode=any -o Acquire::Retries=3 && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-pymysql python3-cryptography'
ssh_base proxy 'sudo mkdir -p /opt/log8415e'
ssh_base proxy 'sudo tee /opt/log8415e/proxy.py >/dev/null' < "$ROOT/proxy/app.py"

# Pass the password file path, not its contents, as a process argument.
python3 - "$SECRETS/app_password" "$(field ips.primary.private)" "$(field ips.replica1.private)" "$(field ips.replica2.private)" <<'PY' | ssh_base proxy 'sudo tee /opt/log8415e/proxy.json >/dev/null && sudo chmod 600 /opt/log8415e/proxy.json'
import json, sys
from pathlib import Path
print(json.dumps({'user': 'log8415e_app', 'password': Path(sys.argv[1]).read_text().strip(),
                  'hosts': dict(zip(('primary', 'replica1', 'replica2'), sys.argv[2:]))}))
PY

ssh_base proxy 'sudo tee /etc/systemd/system/log8415e-proxy.service >/dev/null' <<'SERVICE'
[Unit]
Description=LOG8415E MySQL proxy
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/log8415e/proxy.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
SERVICE
ssh_base proxy 'sudo systemctl daemon-reload && sudo systemctl enable --now log8415e-proxy && sudo systemctl restart log8415e-proxy'
ssh_base proxy 'curl -fsS http://127.0.0.1:8002/health'
echo
