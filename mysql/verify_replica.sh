#!/usr/bin/env bash
# Run as sudo on a replica. Fail unless BOTH replication threads are running.
set -euo pipefail
status="$(mysql -e 'SHOW REPLICA STATUS\G')"
[[ -n "$status" ]] || { echo 'No replication channel found' >&2; exit 1; }
io="$(printf '%s\n' "$status" | awk '$1=="Replica_IO_Running:" {print $2}')"
sql="$(printf '%s\n' "$status" | awk '$1=="Replica_SQL_Running:" {print $2}')"
lag="$(printf '%s\n' "$status" | awk '$1=="Seconds_Behind_Source:" {print $2}')"
error="$(printf '%s\n' "$status" | awk '/Last_SQL_Error:/{sub(/^[[:space:]]*Last_SQL_Error:[[:space:]]*/,"");print}')"
rows="$(mysql -N -e 'SELECT COUNT(*) FROM sakila.film;')"
probe="$(mysql -N -e 'SELECT note FROM sakila.replication_probe WHERE id=1;' 2>/dev/null || true)"
echo "Replica_IO_Running=$io Replica_SQL_Running=$sql Seconds_Behind_Source=$lag sakila.film.rows=$rows probe=$probe"
if [[ "$io" != "Yes" || "$sql" != "Yes" || "$rows" != "1000" || "$probe" != "marco1" ]]; then
  echo "Replication not healthy. Last SQL error: $error" >&2
  exit 1
fi
