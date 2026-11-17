#!/usr/bin/env bash
# Shared local helpers; expected to run on the user's development terminal.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="$ROOT/.state"
SECRETS="$ROOT/.secrets"
mkdir -p "$STATE" "$SECRETS"
chmod 700 "$STATE" "$SECRETS"
export TF_IN_AUTOMATION=1
TF="${TF:-}"
if [[ -z "$TF" ]]; then
  if command -v terraform >/dev/null 2>&1; then TF="$(command -v terraform)";
  elif [[ -x "$ROOT/.tools/terraform" ]]; then TF="$ROOT/.tools/terraform";
  else TF="$ROOT/.tools/terraform"; fi
fi
terraform_install() {
  if [[ -x "$TF" ]]; then return; fi
  local version=1.10.5 arch url temp
  arch="$(uname -m)"
  case "$arch" in x86_64) arch=amd64;; aarch64) arch=arm64;; *) echo "Unsupported Terraform architecture: $arch" >&2; exit 1;; esac
  mkdir -p "$ROOT/.tools"
  temp="$(mktemp -d)"
  url="https://releases.hashicorp.com/terraform/${version}"
  curl -fsSL --retry 3 "$url/terraform_${version}_linux_${arch}.zip" -o "$temp/terraform.zip"
  curl -fsSL --retry 3 "$url/terraform_${version}_SHA256SUMS" -o "$temp/SHA256SUMS"
  (cd "$temp" && grep " terraform_${version}_linux_${arch}.zip$" SHA256SUMS | sed "s|terraform_${version}_linux_${arch}.zip|terraform.zip|" | sha256sum -c -)
  python3 -m zipfile -e "$temp/terraform.zip" "$ROOT/.tools"
  chmod 755 "$ROOT/.tools/terraform"
  rm -rf "$temp"
  TF="$ROOT/.tools/terraform"
}
ensure_key() {
  if [[ ! -f "$STATE/id_ed25519" ]]; then
    ssh-keygen -q -t ed25519 -N '' -f "$STATE/id_ed25519" -C 'log8415e-ec2'
  fi
  chmod 600 "$STATE/id_ed25519"
}
ssh_options=(-i "$STATE/id_ed25519" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$STATE/known_hosts")
terraform_output() {
  "$TF" -chdir="$ROOT/infra" output -raw "$1"
}
node_ip() {
  "$TF" -chdir="$ROOT/infra" output -json node_private_ips | python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"
}
ssh_gatekeeper() { ssh "${ssh_options[@]}" "ubuntu@$(terraform_output gatekeeper_public_ip)" "$@"; }
ssh_private() {
  local node="$1"; shift
  ssh "${ssh_options[@]}" -o "ProxyCommand=ssh -i $STATE/id_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$STATE/known_hosts -W %h:%p ubuntu@$(terraform_output gatekeeper_public_ip)" "ubuntu@$(node_ip "$node")" "$@"
}
wait_ssh() {
  local node="$1" attempt
  for attempt in $(seq 1 40); do
    if [[ "$node" == gatekeeper ]]; then
      if ssh_gatekeeper 'true' >/dev/null 2>&1; then return 0; fi
    elif ssh_private "$node" 'true' >/dev/null 2>&1; then return 0; fi
    sleep 8
  done
  echo "SSH timeout for $node. Check security groups and source IP." >&2
  return 1
}
