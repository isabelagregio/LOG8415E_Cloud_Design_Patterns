#!/usr/bin/env bash
# Runs on an Ubuntu EC2 via SSH: sudo bash -s -- ROLE SERVER_ID
set -euo pipefail
ROLE="${1:?primary or replica required}"
SERVER_ID="${2:?server id required}"
case "$ROLE" in primary|replica) ;; *) echo "Invalid role" >&2; exit 1;; esac
case "$SERVER_ID" in 1|2|3) ;; *) echo "Invalid server id" >&2; exit 1;; esac
export DEBIAN_FRONTEND=noninteractive
# A 1-GiB t3.micro benefits from swap during MySQL package installation.
if ! swapon --show=NAME | grep -qx '/swapfile'; then
  if [[ ! -f /swapfile ]]; then
    fallocate -l 1G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=1024
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
  fi
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
apt-get update -qq
apt-get install -y -qq mysql-server curl ca-certificates tar
cat > /etc/mysql/mysql.conf.d/99-log8415e.cnf <<CONF
[mysqld]
server-id=${SERVER_ID}
bind-address=0.0.0.0
log_bin=mysql-bin
binlog_format=ROW
gtid_mode=ON
enforce_gtid_consistency=ON
log_replica_updates=ON
CONF
# Primary writable; replicas default to read-only, except during snapshot restore.
if [[ "$ROLE" == "replica" ]]; then
  printf 'read_only=ON\nsuper_read_only=ON\n' >> /etc/mysql/mysql.conf.d/99-log8415e.cnf
fi
systemctl enable mysql >/dev/null
systemctl restart mysql
mysql --batch --skip-column-names -e 'SELECT VERSION(), @@gtid_mode, @@server_id;'
