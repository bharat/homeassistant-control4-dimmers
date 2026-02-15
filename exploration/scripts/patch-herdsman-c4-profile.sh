#!/bin/bash
#
# Patches zigbee-herdsman inside the Z2M Docker container to accept
# C4 MIB profile (0xC25C) messages. Without this patch, the EZSP
# adapter silently drops ALL messages on non-standard Zigbee profiles.
#
# This adds the C4 profile ID (0xC25C = 49756) to the whitelist in
# ezspIncomingMessageHandler(), right next to the Shelly custom profile.
#
# Usage:
#   ./scripts/patch-herdsman-c4-profile.sh            # patch + restart
#   ./scripts/patch-herdsman-c4-profile.sh --no-restart  # patch only
#   DOCKER_CONTAINER=my_z2m ./scripts/patch-herdsman-c4-profile.sh
#
# NOTE: This patch is lost when the Z2M Docker image is updated.
#       Re-run this script after updating Z2M.

set -euo pipefail

CONTAINER="${DOCKER_CONTAINER:-zigbee2mqtt}"
NO_RESTART=false
[[ "${1:-}" == "--no-restart" ]] && NO_RESTART=true

echo "=== Control4 Profile Patch for zigbee-herdsman ==="
echo "Container: $CONTAINER"
echo ""

# ── Step 1: Find the compiled ezsp.js file ──────────────────────────
# Try the known pnpm path first, then fall back to find
EZSP_FILE=""

# Known path pattern for pnpm-based Z2M Docker images
KNOWN=$(docker exec "$CONTAINER" sh -c 'ls /app/node_modules/.pnpm/zigbee-herdsman@*/node_modules/zigbee-herdsman/dist/adapter/ember/ezsp/ezsp.js 2>/dev/null' | head -1)
if [ -n "$KNOWN" ]; then
    EZSP_FILE="$KNOWN"
fi

# Fallback: search for it
if [ -z "$EZSP_FILE" ]; then
    EZSP_FILE=$(docker exec "$CONTAINER" find /app -name "ezsp.js" -path "*/ember/ezsp/*" -type f 2>/dev/null | head -1)
fi

if [ -z "$EZSP_FILE" ]; then
    echo "ERROR: Could not find ezsp.js in container '$CONTAINER'."
    echo "Is zigbee2mqtt running?  (docker ps)"
    exit 1
fi

echo "Found: $EZSP_FILE"

# ── Step 2: Check if already patched ────────────────────────────────
if docker exec "$CONTAINER" grep -q "0xC25C" "$EZSP_FILE" 2>/dev/null; then
    echo "Already patched! (0xC25C found in file)"
    echo ""
    docker exec "$CONTAINER" grep -n "0xC25C" "$EZSP_FILE"
    exit 0
fi

# ── Step 3: Verify the Shelly anchor line exists ────────────────────
if ! docker exec "$CONTAINER" grep -q "CUSTOM_SHELLY_PROFILE_ID" "$EZSP_FILE" 2>/dev/null; then
    echo "WARNING: CUSTOM_SHELLY_PROFILE_ID not found — code structure may have changed."
    echo ""
    echo "Searching for the profileId whitelist..."
    docker exec "$CONTAINER" grep -n "profileId.*===" "$EZSP_FILE" | grep -i "HA_PROFILE\|GP_PROFILE\|WILDCARD\|SHELLY\|0x0104\|0xa1e0\|0xffff\|0xc001" | head -10
    echo ""
    echo "Manual fix: add '|| apsFrame.profileId === 0xC25C' to the profileId"
    echo "whitelist in $EZSP_FILE inside the container, then restart Z2M."
    exit 1
fi

# ── Step 4: Apply the patch ─────────────────────────────────────────
#
# The actual line in the compiled JS looks like:
#   apsFrame.profileId === ZSpec.CUSTOM_SHELLY_PROFILE_ID) {
#
# We replace the closing ')' to insert our profile before it:
#   apsFrame.profileId === ZSpec.CUSTOM_SHELLY_PROFILE_ID || apsFrame.profileId === 0xC25C) {

echo "Applying patch..."

docker exec "$CONTAINER" sed -i.bak \
    's/apsFrame\.profileId === ZSpec\.CUSTOM_SHELLY_PROFILE_ID)/apsFrame.profileId === ZSpec.CUSTOM_SHELLY_PROFILE_ID || apsFrame.profileId === 0xC25C)/' \
    "$EZSP_FILE"

# ── Step 5: Verify the patch ────────────────────────────────────────
if docker exec "$CONTAINER" grep -q "0xC25C" "$EZSP_FILE"; then
    echo ""
    echo "SUCCESS! Patched line:"
    docker exec "$CONTAINER" grep -n "0xC25C" "$EZSP_FILE"
    echo ""

    if [ "$NO_RESTART" = true ]; then
        echo "Restart Z2M when ready:"
        echo "  docker restart $CONTAINER"
    else
        echo "Restarting $CONTAINER..."
        docker restart "$CONTAINER"
        echo "Done. Watch for button events:"
        echo "  docker logs -f $CONTAINER 2>&1 | grep 'C4 RECV\|C4 BUTTON\|c4_response'"
    fi
    exit 0
fi

# ── Patch failed — show context for manual fix ──────────────────────
echo ""
echo "ERROR: sed replacement did not produce expected result."
echo ""
echo "Current state of the Shelly line:"
docker exec "$CONTAINER" grep -n -B2 -A2 "CUSTOM_SHELLY" "$EZSP_FILE" | head -10
echo ""
echo "Manual fix: inside the container, edit $EZSP_FILE"
echo "  Find:    apsFrame.profileId === ZSpec.CUSTOM_SHELLY_PROFILE_ID)"
echo "  Replace: apsFrame.profileId === ZSpec.CUSTOM_SHELLY_PROFILE_ID || apsFrame.profileId === 0xC25C)"
echo "Then: docker restart $CONTAINER"

# Restore backup
docker exec "$CONTAINER" cp "$EZSP_FILE.bak" "$EZSP_FILE" 2>/dev/null
exit 1
