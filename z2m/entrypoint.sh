#!/bin/sh
# Entrypoint wrapper: deliver the Control4 converter into the data dir.
#
# The image ships the converter at /app/c4/control4.mjs, a path that is NOT
# shadowed when deployments bind-mount a host directory over /app/data
# (which production does). Copying it into the data dir at boot means a
# plain `docker compose pull && up -d` delivers z2m, the herdsman profile
# patch, and the converter atomically; no rsync step, no stale converter.
#
# See issue #104 for the gap this closes: the old COPY into /app/data was
# silently shadowed by the production bind mount, so image pulls never
# updated the running converter.

set -eu

DATA="${ZIGBEE2MQTT_DATA:-/app/data}"

mkdir -p "$DATA/external_converters"
cp -f /app/c4/control4.mjs "$DATA/external_converters/control4.mjs"
echo "[C4 ENTRYPOINT] Installed converter into $DATA/external_converters/"

# Hand off to the stock zigbee2mqtt entrypoint (resolves ZIGBEE2MQTT_DATA
# and execs the CMD, normally: /sbin/tini -- node index.js).
exec docker-entrypoint.sh "$@"
