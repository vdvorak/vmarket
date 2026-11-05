#!/bin/sh
# Spuštění kontejneru. S LITESTREAM_REPLICA_URL se chybějící databáze nejdřív obnoví ze zálohy a aplikace běží
# jako podproces Litestreamu. Bez ní běží bez zálohy (lokálně).
set -eu

# Jeden proces (App Contract: jeden stroj, bez horizontálního škálování).
APP="uvicorn vmarket.main:create_app --factory --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips '*'"

if [ -z "${LITESTREAM_REPLICA_URL:-}" ]; then
	echo "LITESTREAM_REPLICA_URL není nastavené, databáze se nezálohuje." >&2
	eval "exec $APP"
fi

CONFIG=/app/deploy/litestream.yml
litestream restore -config "$CONFIG" -if-db-not-exists -if-replica-exists "$DATABASE_PATH"
exec litestream replicate -config "$CONFIG" -exec "$APP"
