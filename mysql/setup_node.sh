#!/usr/bin/env bash
set -euo pipefail
ROLE="${1:?role required}"
ID="${2:?server-id required}"
[[ "$ROLE" == primary || "$ROLE" == replica ]] || exit 1
[[ "$ID" =~ ^[123]$ ]] || exit 1
export DEBIAN_FRONTEND=noninteractive
# t3.micro has 1 GiB of RAM; create swap for package installation.
if ! swapon --show=NAME | grep -qx /swapfile; then
  if [[ ! -f /swapfile ]]; then
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
  fi
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
apt-get update \
  -o Acquire::ForceIPv4=true \
  -o APT::Update::Error-Mode=any \
  -o Acquire::Retries=3
apt-get install -y -qq mysql-server curl ca-certificates tar
sed -i 's/^bind-address[[:space:]]*=.*/bind-address = 0.0.0.0/' \
  /etc/mysql/mysql.conf.d/mysqld.cnf
cat > /etc/mysql/mysql.conf.d/99-log8415e.cnf <<CONF
[mysqld]
server-id=$ID
log_bin=mysql-bin
binlog_format=ROW
gtid_mode=ON
enforce_gtid_consistency=ON
log_replica_updates=ON
CONF
if [[ "$ROLE" == replica ]]; then
  printf 'read_only=ON\nsuper_read_only=ON\n' >> /etc/mysql/mysql.conf.d/99-log8415e.cnf
fi
systemctl enable mysql >/dev/null
systemctl restart mysql
mysql -N -e 'SELECT VERSION(), @@server_id, @@gtid_mode;'
