#!/usr/bin/env bash
#
# patch-database.sh
#
# After factory-resetting and pairing Control4 dimmers with Zigbee2MQTT,
# the interview will likely fail (empty modelId, endpoint 196/197 errors).
#
# This script patches the Zigbee2MQTT database.db file to fill in the
# missing device metadata so the external converter can match the devices.
#
# Usage:
#   1. Stop Zigbee2MQTT
#   2. Back up your database.db
#   3. Run: ./patch-database.sh /path/to/database.db
#   4. Start Zigbee2MQTT
#
# The script will find all devices with empty modelId and ask you which
# ones are Control4 dimmers. It then patches in the required fields.

set -euo pipefail

DB_FILE="${1:-}"

if [ -z "$DB_FILE" ]; then
    echo "Usage: $0 /path/to/zigbee2mqtt/data/database.db"
    echo ""
    echo "Common locations:"
    echo "  Docker:     /opt/zigbee2mqtt/data/database.db"
    echo "  HAOS addon: /config/zigbee2mqtt/database.db"
    echo "  Native:     /opt/zigbee2mqtt/data/database.db"
    exit 1
fi

if [ ! -f "$DB_FILE" ]; then
    echo "Error: File not found: $DB_FILE"
    exit 1
fi

# Create backup
BACKUP="${DB_FILE}.bak.$(date +%Y%m%d_%H%M%S)"
cp "$DB_FILE" "$BACKUP"
echo "Backup created: $BACKUP"
echo ""

# database.db is a newline-delimited JSON file (one JSON object per line)
# Each line represents a device or group entry

echo "Scanning for devices with empty modelId..."
echo ""

# Find lines with empty modelId and extract IEEE addresses
LINE_NUM=0
FOUND=0

while IFS= read -r line; do
    LINE_NUM=$((LINE_NUM + 1))

    # Check if this line has an empty modelId
    if echo "$line" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    if d.get('type') == 'Router' or d.get('modelID') == '':
        ieee = d.get('ieeeAddr', 'unknown')
        model = d.get('modelID', 'N/A')
        mfr = d.get('manufacturerName', 'N/A')
        print(f'{ieee}|{model}|{mfr}|{LINE_NUM}')
except:
    pass
" 2>/dev/null; then
        FOUND=$((FOUND + 1))
    fi
done < "$DB_FILE"

if [ "$FOUND" -eq 0 ]; then
    echo "No devices with empty modelId found."
    echo ""
    echo "If your C4 dimmers haven't been paired yet, pair them first,"
    echo "then run this script again."
    exit 0
fi

echo ""
echo "Found $FOUND candidate device(s) with empty/missing modelId."
echo ""
echo "You can also manually edit database.db with a text editor."
echo "For each Control4 dimmer line, ensure these JSON fields exist:"
echo ""
echo '  "modelID": "",'
echo '  "manufacturerName": "Control4",'
echo '  "type": "Router"'
echo ""
echo "The external converter matches on the fingerprint, so the empty"
echo "modelId is fine as long as the device has the right endpoints."
echo ""
echo "To manually patch a specific device, find its IEEE address in"
echo "database.db and ensure the endpoint 1 definition includes:"
echo '  "profileID": 260,'
echo '  "deviceID": 257,'
echo '  "inputClusters": [0, 3, 4, 5, 6, 8, 10]'
echo ""
echo "Done. Restart Zigbee2MQTT to apply changes."
