#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="$ROOT/.state"
SECRETS="$ROOT/.secrets"
mkdir -p "$STATE" "$SECRETS"
chmod 700 "$STATE" "$SECRETS"

field() {
  python3 - "$STATE/aws.json" "$1" <<'PY'
import json,sys
value=json.load(open(sys.argv[1]))
for item in sys.argv[2].split('.'):
    value=value[item]
print(value)
PY
}

ssh_args=(-o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=7 -o ServerAliveInterval=15)
ssh_base() {
  local node="$1"; shift
  local key="$STATE/id_ed25519"
  local jump="ubuntu@$(field ips.gatekeeper.public)"
  local host
  if [[ "$node" == gatekeeper ]]; then
    host="$jump"
    ssh -i "$key" "${ssh_args[@]}" "$host" "$@"
  else
    host="ubuntu@$(field "ips.$node.private")"
    ssh -i "$key" "${ssh_args[@]}" -o "ProxyCommand=ssh -i $key -o BatchMode=yes -o StrictHostKeyChecking=no -W %h:%p $jump" "$host" "$@"
  fi
}

wait_ssh() {
  local node="$1" i
  for i in $(seq 1 50); do
    if ssh_base "$node" true >/dev/null 2>&1; then return 0; fi
    sleep 6
  done
  echo "SSH unavailable: $node" >&2
  return 1
}
