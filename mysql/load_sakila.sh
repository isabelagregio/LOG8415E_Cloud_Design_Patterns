#!/usr/bin/env bash
# Run only on the PRIMARY, via sudo bash -s.
set -euo pipefail
if mysql -N -e "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME='sakila'" | grep -qx sakila; then
  echo 'Sakila already exists; skipping re-import.'
  exit 0
fi
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
curl -fsSL --retry 3 https://downloads.mysql.com/docs/sakila-db.tar.gz -o "$workdir/sakila.tar.gz"
tar -xzf "$workdir/sakila.tar.gz" -C "$workdir"
mysql < "$workdir/sakila-db/sakila-schema.sql"
mysql < "$workdir/sakila-db/sakila-data.sql"
mysql -N -e 'SELECT COUNT(*) FROM sakila.film;'
