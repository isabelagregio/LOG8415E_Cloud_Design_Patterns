#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
ensure_access
for node in replica1 replica2 replica3; do
  echo "Checking $node"
  for attempt in $(seq 1 18); do
    if ssh_base "$node" 'sudo bash -s' < "$ROOT/mysql/verify_replica.sh"; then break; fi
    if [[ "$attempt" == 18 ]]; then echo "Replication check failed: $node" >&2; exit 1; fi
    sleep 5
  done
done
