#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
python3 -c 'import boto3' || { echo 'Install boto3 first (see README)' >&2; exit 1; }
for tool in aws ssh ssh-keygen openssl; do command -v "$tool" >/dev/null || { echo "Missing: $tool" >&2; exit 1; }; done
if [[ ! -f "$STATE/aws.json" ]]; then
  python3 "$ROOT/infra/aws.py" deploy
else
  echo 'Reusing existing AWS infrastructure from .state/aws.json'
fi
python3 "$ROOT/scripts/refresh_access.py"
for node in gatekeeper trusted proxy primary replica1 replica2; do
  echo "Waiting for SSH: $node"
  wait_ssh "$node"
done
for entry in primary:1 replica1:2 replica2:3; do
  node="${entry%%:*}"; server_id="${entry##*:}"
  role=replica; [[ "$node" == primary ]] && role=primary
  echo "Installing MySQL on $node"
  ssh_base "$node" "sudo bash -s -- $role $server_id" < "$ROOT/mysql/setup_node.sh"
done
echo 'Loading Sakila on primary'
ssh_base primary 'sudo bash -s' < "$ROOT/mysql/load_sakila.sh"
if [[ ! -s "$SECRETS/replication_password" ]]; then
  (umask 077; openssl rand -hex 24 > "$SECRETS/replication_password")
fi
password="$(cat "$SECRETS/replication_password")"
[[ "$password" =~ ^[0-9a-f]{48}$ ]] || { echo 'Invalid replication password file' >&2; exit 1; }
# Password is random hexadecimal, allowing safe escaping in this generated SQL.
printf "CREATE USER IF NOT EXISTS 'log8415e_repl'@'10.42.2.%%' IDENTIFIED BY '%s';\nALTER USER 'log8415e_repl'@'10.42.2.%%' IDENTIFIED BY '%s';\nGRANT REPLICATION SLAVE ON *.* TO 'log8415e_repl'@'10.42.2.%%';\n" "$password" "$password" | ssh_base primary 'sudo mysql'
# GTIDs are retained in the dump so replicas know the consistent starting point.
ssh_base primary 'sudo mysqldump --databases sakila --single-transaction --triggers --routines --events --set-gtid-purged=ON' > "$STATE/sakila-snapshot.sql"
[[ -s "$STATE/sakila-snapshot.sql" ]] || { echo 'Empty database snapshot' >&2; exit 1; }
primary_ip="$(field ips.primary.private)"
for node in replica1 replica2; do
  echo "Initializing $node from Sakila snapshot"
  ssh_base "$node" 'sudo mysql -e "SET GLOBAL super_read_only=OFF; SET GLOBAL read_only=OFF;"'
  ssh_base "$node" 'sudo mysql' < "$STATE/sakila-snapshot.sql"
  ssh_base "$node" 'sudo mysql -e "SET GLOBAL read_only=ON; SET GLOBAL super_read_only=ON;"'
  printf "CHANGE REPLICATION SOURCE TO SOURCE_HOST='%s', SOURCE_PORT=3306, SOURCE_USER='log8415e_repl', SOURCE_PASSWORD='%s', SOURCE_AUTO_POSITION=1, GET_SOURCE_PUBLIC_KEY=1;\nSTART REPLICA;\n" "$primary_ip" "$password" | ssh_base "$node" 'sudo mysql'
done
printf "CREATE TABLE IF NOT EXISTS sakila.replication_probe (id INT PRIMARY KEY, note VARCHAR(32));\nINSERT INTO sakila.replication_probe VALUES (1, 'marco1') ON DUPLICATE KEY UPDATE note=VALUES(note);\n" | ssh_base primary 'sudo mysql'
"$ROOT/scripts/verify.sh"
echo 'Marco 1 deployed.'
