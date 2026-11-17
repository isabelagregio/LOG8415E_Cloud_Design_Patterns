#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
ensure_access
if [[ ! -s "$SECRETS/gatekeeper_token" ]]; then
  (umask 077; openssl rand -hex 24 > "$SECRETS/gatekeeper_token")
fi
[[ "$(cat "$SECRETS/gatekeeper_token")" =~ ^[0-9a-f]{48}$ ]] || { echo 'Invalid Gatekeeper token' >&2; exit 1; }

ssh_base trusted 'sudo mkdir -p /opt/log8415e'
ssh_base trusted 'sudo tee /opt/log8415e/gateway.py >/dev/null' < "$ROOT/gateway/app.py"
ssh_base trusted 'sudo tee /etc/systemd/system/log8415e-trusted.service >/dev/null' <<SERVICE
[Unit]
Description=Trusted Host
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/log8415e/gateway.py trusted http://$(field ips.proxy.private):8002 8001
Restart=on-failure

[Install]
WantedBy=multi-user.target
SERVICE
ssh_base trusted 'sudo systemctl daemon-reload && sudo systemctl enable --now log8415e-trusted && sudo systemctl restart log8415e-trusted'

ssh_base gatekeeper 'sudo mkdir -p /opt/log8415e'
ssh_base gatekeeper 'sudo tee /opt/log8415e/gateway.py >/dev/null' < "$ROOT/gateway/app.py"
# This private systemd environment file is only stored on the Gatekeeper EC2.
{ printf 'GATEKEEPER_TOKEN='; cat "$SECRETS/gatekeeper_token"; } | ssh_base gatekeeper 'sudo tee /opt/log8415e/gatekeeper.env >/dev/null && sudo chmod 600 /opt/log8415e/gatekeeper.env'

ssh_base gatekeeper 'sudo tee /etc/systemd/system/log8415e-gatekeeper.service >/dev/null' <<SERVICE
[Unit]
Description=Gatekeeper
After=network.target

[Service]
EnvironmentFile=/opt/log8415e/gatekeeper.env
ExecStart=/usr/bin/python3 /opt/log8415e/gateway.py gatekeeper http://$(field ips.trusted.private):8001 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
SERVICE
ssh_base gatekeeper 'sudo systemctl daemon-reload && sudo systemctl enable --now log8415e-gatekeeper && sudo systemctl restart log8415e-gatekeeper'
ssh_base gatekeeper 'python3 -c "import urllib.request; print(urllib.request.urlopen(\"http://127.0.0.1:8000/health\", timeout=10).read().decode())"'
