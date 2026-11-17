#!/usr/bin/env bash
# Complete Marco 1: provision 6 EC2, install MySQL 8 and bootstrap Sakila replication.
set -euo pipefail
source "$(dirname "$0")/common.sh"
for command in aws curl ssh ssh-keygen python3 openssl sha256sum; do
  command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }
done
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || true)}}"
AWS_REGION="${AWS_REGION:-us-east-1}"
aws sts get-caller-identity >/dev/null || { echo 'AWS credentials missing/expired' >&2; exit 1; }
region_status="$(aws ec2 describe-regions --region-names "$AWS_REGION" --query 'Regions[0].RegionName' --output text 2>/dev/null || true)"
[[ "$region_status" == "$AWS_REGION" ]] || { echo "Cannot access EC2 in $AWS_REGION" >&2; exit 1; }
ensure_key
terraform_install
# AZ can be overridden if EC2 capacity in the selected zone is insufficient.
AZ="${AWS_AZ:-}"
if [[ -z "$AZ" ]]; then
  AZ="$(aws ec2 describe-availability-zones --region "$AWS_REGION" --filters Name=state,Values=available --query 'AvailabilityZones[0].ZoneName' --output text)"
fi
[[ "$AZ" != None && -n "$AZ" ]] || { echo 'No available AZ discovered' >&2; exit 1; }
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.micro}"
available="$(aws ec2 describe-instance-type-offerings --region "$AWS_REGION" --location-type availability-zone --filters "Name=location,Values=$AZ" "Name=instance-type,Values=$INSTANCE_TYPE" --query 'length(InstanceTypeOfferings)' --output text)"
[[ "$available" != 0 ]] || { echo "$INSTANCE_TYPE not offered in $AZ" >&2; exit 1; }
# By default find the public egress IP used by this development terminal.
ADMIN_CIDR="${ADMIN_CIDR:-$(curl -fsSL --retry 2 https://checkip.amazonaws.com | tr -d '\n\r')/32}"
[[ "$ADMIN_CIDR" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/32$ ]] || { echo 'Set ADMIN_CIDR to your real egress IP/32' >&2; exit 1; }
PROFILE="${INSTANCE_PROFILE_NAME-LabInstanceProfile}"
TF_VARS=(-var="aws_region=$AWS_REGION" -var="availability_zone=$AZ" -var="instance_type=$INSTANCE_TYPE" -var="ssh_public_key_path=$STATE/id_ed25519.pub" -var="admin_cidr=$ADMIN_CIDR" -var="instance_profile_name=$PROFILE")
echo "Region=$AWS_REGION AZ=$AZ EC2=$INSTANCE_TYPE (6 nodes) admin=$ADMIN_CIDR"
echo "WARNING: NAT Gateway + six EC2 instances + EBS + public IPv4 incur charges."
echo "Check Learner Lab remaining credits BEFORE continuing."
if [[ "${SKIP_COST_CONFIRMATION:-0}" != 1 ]]; then
  read -r -p 'Type DEPLOY to provision chargeable resources: ' answer
  [[ "$answer" == DEPLOY ]] || { echo 'Cancelled.'; exit 1; }
fi
"$TF" -chdir="$ROOT/infra" init -input=false
"$TF" -chdir="$ROOT/infra" apply -input=false "${TF_VARS[@]}" -auto-approve
for node in gatekeeper trusted proxy primary replica1 replica2; do
  echo "Waiting for $node SSH..."
  wait_ssh "$node"
done
# Fresh secrets are created only once; prevent accidental change on re-run.
if [[ ! -f "$SECRETS/mysql_replication_password" ]]; then
  (umask 077; openssl rand -hex 24 > "$SECRETS/mysql_replication_password")
fi
replication_password="$(cat "$SECRETS/mysql_replication_password")"
[[ "$replication_password" =~ ^[0-9a-f]{48}$ ]] || { echo 'Invalid replication secret file' >&2; exit 1; }
for entry in 'primary:1' 'replica1:2' 'replica2:3'; do
  node="${entry%%:*}"; id="${entry##*:}"
  role=replica; [[ "$node" == primary ]] && role=primary
  echo "Installing MySQL on $node..."
  ssh_private "$node" "sudo bash -s -- $role $id" < "$ROOT/mysql/setup_node.sh"
done
printf 'Installing Sakila on Primary...\n'
ssh_private primary 'sudo bash -s' < "$ROOT/mysql/load_sakila.sh"
# Limit the replication MySQL account to the private VPC network in addition to SGs.
printf "CREATE USER IF NOT EXISTS 'log8415e_repl'@'10.42.2.%%' IDENTIFIED BY '%s';\nALTER USER 'log8415e_repl'@'10.42.2.%%' IDENTIFIED BY '%s';\nGRANT REPLICATION SLAVE ON *.* TO 'log8415e_repl'@'10.42.2.%%';\n" "$replication_password" "$replication_password" | ssh_private primary 'sudo mysql'
# Snapshot of only Sakila, but preserve the complete PRIMARY GTID set.
# The dump must be restored on an otherwise fresh replica. Stored outside Git.
echo 'Exporting consistent Sakila GTID snapshot...'
ssh_private primary 'sudo mysqldump --databases sakila --single-transaction --triggers --routines --events --set-gtid-purged=ON' > "$STATE/sakila-snapshot.sql"
[[ -s "$STATE/sakila-snapshot.sql" ]] || { echo 'Empty MySQL backup' >&2; exit 1; }
primary_ip="$(node_ip primary)"
for node in replica1 replica2; do
  echo "Restoring Sakila on $node..."
  # On a fresh instance these settings allow one controlled initial import.
  ssh_private "$node" 'sudo mysql -e "SET GLOBAL super_read_only=OFF; SET GLOBAL read_only=OFF;"'
  ssh_private "$node" 'sudo mysql' < "$STATE/sakila-snapshot.sql"
  ssh_private "$node" 'sudo mysql -e "SET GLOBAL read_only=ON; SET GLOBAL super_read_only=ON;"'
  printf "STOP REPLICA;\nCHANGE REPLICATION SOURCE TO SOURCE_HOST='%s', SOURCE_PORT=3306, SOURCE_USER='log8415e_repl', SOURCE_PASSWORD='%s', SOURCE_AUTO_POSITION=1, GET_SOURCE_PUBLIC_KEY=1;\nSTART REPLICA;\n" "$primary_ip" "$replication_password" | ssh_private "$node" 'sudo mysql'
done
# Prove that a NEW transaction, not just the initial snapshot, reaches replicas.
printf "%s\n" "CREATE TABLE IF NOT EXISTS sakila.replication_probe (id INT PRIMARY KEY, note VARCHAR(32));" "INSERT INTO sakila.replication_probe VALUES (1, 'marco1') ON DUPLICATE KEY UPDATE note=VALUES(note);" | ssh_private primary 'sudo mysql'
"$ROOT/scripts/verify.sh"
echo 'Marco 1 deployed successfully. Run ./scripts/teardown.sh after collecting evidence.'
