#!/usr/bin/env bash
set -euo pipefail
status="$(mysql -e 'SHOW REPLICA STATUS\G')"
[[ -n "$status" ]] || { echo 'Replication not configured' >&2; exit 1; }
io="$(awk '$1=="Replica_IO_Running:" {print $2}' <<< "$status")"
sql="$(awk '$1=="Replica_SQL_Running:" {print $2}' <<< "$status")"
lag="$(awk '$1=="Seconds_Behind_Source:" {print $2}' <<< "$status")"
rows="$(mysql -N -e 'SELECT COUNT(*) FROM sakila.film;')"
probe="$(mysql -N -e 'SELECT note FROM sakila.replication_probe WHERE id=1;' 2>/dev/null || true)"
echo "IO=$io SQL=$sql lag=$lag film_rows=$rows probe=$probe"
[[ "$io" == Yes && "$sql" == Yes && "$rows" == 1000 && -n "$probe" ]]
