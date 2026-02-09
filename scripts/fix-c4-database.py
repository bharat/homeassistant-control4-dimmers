#!/usr/bin/env python3
"""
Fix Control4 dimmer entries in the Zigbee2MQTT database.

Z2M's database.db is a newline-delimited JSON file (one JSON object per line).
C4 dimmers have proprietary endpoints (196, 197) that refuse simpleDescriptor
requests, causing the interview to permanently show as "FAILED" in Z2M.

This script:
  1. Creates a timestamped backup of database.db
  2. Finds all C4 dimmer entries (manufId == 43981 / 0xABCD)
  3. Fixes interviewState → "SUCCESSFUL" and interviewCompleted → true
  4. Is idempotent — safe to run multiple times

Usage:
  Stop Z2M first!  It overwrites database.db on shutdown.

  python3 fix-c4-database.py /path/to/database.db
  python3 fix-c4-database.py /path/to/database.db --dry-run
"""

import json
import sys
import shutil
from datetime import datetime
from pathlib import Path

C4_MANUF_ID = 43981  # 0xABCD — Control4 manufacturer ID

# Known C4 dimmer model IDs (for reporting; we match on manufId)
C4_MODELS = {'C4-APD120', 'C4-DIM', 'C4-KD120', 'C4-KD277', 'C4-FPD120', 'LDZ-102'}


def is_c4_dimmer(entry):
    """Check if a database entry is a Control4 dimmer."""
    return entry.get('manufId') == C4_MANUF_ID


def fix_entry(entry):
    """Fix a C4 dimmer entry. Returns (fixed_entry, list_of_changes)."""
    changes = []

    # Fix interview state
    if entry.get('interviewState') != 'SUCCESSFUL':
        old = entry.get('interviewState', '<missing>')
        entry['interviewState'] = 'SUCCESSFUL'
        changes.append(f'interviewState: {old} → SUCCESSFUL')

    if entry.get('interviewCompleted') is not True:
        entry['interviewCompleted'] = True
        changes.append('interviewCompleted: → true')

    return entry, changes


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Fix Control4 dimmer interview state in Z2M database',
        epilog='Stop Zigbee2MQTT before running this script!')
    parser.add_argument('database', type=Path,
                        help='Path to Z2M database.db')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would change without modifying the file')
    args = parser.parse_args()

    db_path = args.database

    if not db_path.exists():
        print(f'Error: {db_path} not found', file=sys.stderr)
        sys.exit(1)

    # Read all lines
    lines = db_path.read_text().splitlines()
    print(f'Read {len(lines)} entries from {db_path}')

    # Parse, find C4 dimmers, fix them
    fixed_lines = []
    total_fixed = 0
    total_c4 = 0

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            fixed_lines.append(line)
            continue

        try:
            entry = json.loads(line)
        except json.JSONDecodeError as e:
            print(f'  Warning: line {i+1} is not valid JSON, skipping ({e})')
            fixed_lines.append(line)
            continue

        if is_c4_dimmer(entry):
            total_c4 += 1
            addr = entry.get('ieeeAddr', '???')
            model = entry.get('modelId', 'unknown')

            entry, changes = fix_entry(entry)

            if changes:
                total_fixed += 1
                print(f'  Fix {addr} ({model}):')
                for c in changes:
                    print(f'    - {c}')
                fixed_lines.append(json.dumps(entry, separators=(',', ':')))
            else:
                print(f'  OK  {addr} ({model}): already correct')
                fixed_lines.append(line)  # preserve original formatting
        else:
            fixed_lines.append(line)

    print()
    print(f'Found {total_c4} C4 dimmer(s), {total_fixed} needed fixing')

    if total_fixed == 0:
        print('Nothing to do.')
        return

    if args.dry_run:
        print('Dry run — no changes written.')
        return

    # Create timestamped backup
    timestamp = datetime.now().strftime('%Y-%m-%d-%H%M%S')
    backup_path = db_path.with_suffix(f'.db.{timestamp}.bak')
    shutil.copy2(db_path, backup_path)
    print(f'Backup: {backup_path}')

    # Write fixed database
    db_path.write_text('\n'.join(fixed_lines) + '\n')
    print(f'Written: {db_path}')
    print()
    print('Now restart Zigbee2MQTT to pick up the changes.')


if __name__ == '__main__':
    main()
