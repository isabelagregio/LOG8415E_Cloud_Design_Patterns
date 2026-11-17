#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
terraform_install
if [[ ! -f "$ROOT/infra/terraform.tfstate" ]]; then
  echo 'No local Terraform state found. Refusing to guess which AWS resources to delete.' >&2
  exit 1
fi
if [[ "${SKIP_COST_CONFIRMATION:-0}" != 1 ]]; then
  read -r -p 'Type DESTROY to remove this project infrastructure: ' answer
  [[ "$answer" == DESTROY ]] || { echo 'Cancelled.'; exit 1; }
fi
# Terraform remembers variable values in state; required inputs may still be demanded.
# Use the same region/AZ and key path stored locally from initial deploy.
region="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || true)}}"
region="${region:-us-east-1}"
"$TF" -chdir="$ROOT/infra" destroy -input=false -auto-approve \
  -var="aws_region=$region" \
  -var="availability_zone=${AWS_AZ:-us-east-1a}" \
  -var="ssh_public_key_path=$STATE/id_ed25519.pub" \
  -var="admin_cidr=${ADMIN_CIDR:-127.0.0.1/32}" \
  -var="instance_profile_name=${INSTANCE_PROFILE_NAME-LabInstanceProfile}"
echo 'Terraform destroy complete. Keep .state and .secrets out of Git.'
