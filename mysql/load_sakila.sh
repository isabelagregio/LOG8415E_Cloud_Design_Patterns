#!/usr/bin/env bash
set -euo pipefail
if mysql -N -e "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME='sakila'" | grep -qx sakila; then
  echo 'Sakila already installed'
  exit 0
fi
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
curl -fsSL --retry 3 https://downloads.mysql.com/docs/sakila-db.tar.gz -o "$work/sakila.tar.gz"
tar -xzf "$work/sakila.tar.gz" -C "$work"
mysql < "$work/sakila-db/sakila-schema.sql"
mysql < "$work/sakila-db/sakila-data.sql"
mysql -N -e 'SELECT COUNT(*) FROM sakila.film;'
