#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
ensure_access

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
ssh_base gatekeeper 'sudo tee /etc/systemd/system/log8415e-gatekeeper.service >/dev/null' <<SERVICE
[Unit]
Description=Gatekeeper
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/log8415e/gateway.py gatekeeper http://$(field ips.trusted.private):8001 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
SERVICE
ssh_base gatekeeper 'sudo systemctl daemon-reload && sudo systemctl enable --now log8415e-gatekeeper && sudo systemctl restart log8415e-gatekeeper'
ssh_base gatekeeper 'python3 -c "import urllib.request; print(urllib.request.urlopen(\"http://127.0.0.1:8000/health\", timeout=10).read().decode())"'
