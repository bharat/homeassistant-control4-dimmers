# Custom Zigbee2MQTT Docker image with Control4 device support
#
# Adds:
#   1. Control4 external converter (control4.mjs)
#   2. Patched zigbee-herdsman to whitelist C4 profile 0xC25C
#
# Usage:
#   docker build -t z2m-control4 z2m/
#   docker compose -f z2m/docker-compose.yml up -d      (dev: builds locally)
#
# The resulting image is a drop-in replacement for koenkk/zigbee2mqtt.
# Production consumes the published image (ghcr.io/bharat/zigbee2mqtt-control4)
# rather than building; `docker compose pull && up -d` delivers everything.
#
# Note for deployments on z2m >= 2.11: external converters are gated behind
# `enable_external_js: true` in configuration.yaml; pin it explicitly.

FROM koenkk/zigbee2mqtt:latest

# ── Ship the Control4 converter (installed into the data dir at boot) ─
# The converter is copied to a path that is NOT shadowed when a host data
# directory is bind-mounted over /app/data (production does this). The
# entrypoint wrapper copies it into $ZIGBEE2MQTT_DATA/external_converters
# at container start, so image pulls actually deliver converter updates
# (issue #104: the old COPY into /app/data was silently mount-shadowed).
COPY converters/control4.mjs /app/c4/control4.mjs
COPY entrypoint.sh /app/c4/entrypoint.sh
RUN chmod +x /app/c4/entrypoint.sh

# ── Patch zigbee-herdsman to accept C4 profile (0xC25C) ─────────────
# The EZSP adapter silently drops messages on non-standard Zigbee profiles.
# We add 0xC25C (Control4 MIB) to the whitelist alongside Shelly's 0xC001.
#
# This patches the compiled JS directly. When the herdsman fork is ready,
# this section can be replaced with: RUN npm install zigbee-herdsman@<fork-url>

# Patch the Ember EZSP adapter (primary — used by EFR32-based coordinators)
RUN EZSP_FILE=$(find /app -name "ezsp.js" -path "*/ember/ezsp/*" -type f | head -1) && \
    if [ -n "$EZSP_FILE" ] && ! grep -q "0xC25C\|0xc25c" "$EZSP_FILE"; then \
        sed -i 's/apsFrame\.profileId === ZSpec\.CUSTOM_SHELLY_PROFILE_ID)/apsFrame.profileId === ZSpec.CUSTOM_SHELLY_PROFILE_ID || apsFrame.profileId === 0xC25C)/' "$EZSP_FILE" && \
        echo "Patched Ember EZSP: $EZSP_FILE"; \
    else \
        echo "Ember EZSP already patched or not found"; \
    fi

# Patch the legacy EZSP adapter (fallback — used by older Silicon Labs coordinators)
RUN LEGACY_FILE=$(find /app -name "ezspAdapter.js" -path "*/ezsp/adapter/*" -type f | head -1) && \
    if [ -n "$LEGACY_FILE" ] && ! grep -q "0xC25C\|0xc25c" "$LEGACY_FILE"; then \
        sed -i 's/frame\.apsFrame\.profileId === ZSpec\.CUSTOM_SHELLY_PROFILE_ID)/frame.apsFrame.profileId === ZSpec.CUSTOM_SHELLY_PROFILE_ID || frame.apsFrame.profileId === 0xC25C)/' "$LEGACY_FILE" && \
        echo "Patched legacy EZSP: $LEGACY_FILE"; \
    else \
        echo "Legacy EZSP already patched or not found"; \
    fi

# ── Entrypoint wrapper: install converter into the data dir at boot ──
# The base image's ENTRYPOINT is ["docker-entrypoint.sh"] with
# CMD ["/sbin/tini", "--", "node", "index.js"]. Overriding ENTRYPOINT
# resets CMD, so it is re-declared verbatim here.
ENTRYPOINT ["/app/c4/entrypoint.sh"]
CMD ["/sbin/tini", "--", "node", "index.js"]
