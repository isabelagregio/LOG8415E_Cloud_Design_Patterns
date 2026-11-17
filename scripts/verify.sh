#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
terraform_install
printf '\nPrimary film count: '
ssh_private primary 'sudo mysql -N -e "SELECT COUNT(*) FROM sakila.film;"'
for node in replica1 replica2; do
  echo "Verifying $node..."
  healthy=0
  for attempt in $(seq 1 20); do
    if ssh_private "$node" 'sudo bash -s' < "$ROOT/mysql/verify_replica.sh"; then healthy=1; break; fi
    sleep 3
  done
  [[ "$healthy" == 1 ]] || { echo "$node failed replication checks" >&2; exit 1; }
done
echo 'Both replicas healthy.'
