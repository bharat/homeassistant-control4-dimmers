#!/usr/bin/env python3
"""
Fix Control4 dimmer entries in the Zigbee2MQTT database.

Z2M's database.db is a newline-delimited JSON file (one JSON object per line).
C4 dimmers have proprietary endpoints (196, 197) that refuse simpleDescriptor
requests, causing the interview to permanently show as "FAILED" in Z2M.

This script:
  1. Finds all C4 dimmer entries (manufId == 43981 / 0xABCD)
  2. Reports what needs fixing (dry-run is the default)
  3. With --apply: creates a timestamped backup, then writes fixes
  4. Is idempotent — safe to run multiple times

Usage:
  Stop Z2M first!  It overwrites database.db on shutdown.

  python3 fix-c4-database.py /path/to/database.db          # dry-run (default)
  python3 fix-c4-database.py /path/to/database.db --apply   # actually fix
"""

import json
import sys
import shutil
from datetime import datetime
from pathlib import Path

C4_MANUF_ID = 43981  # 0xABCD — Control4 manufacturer ID


def is_c4_dimmer(entry):
    """Check if a database entry is a Control4 dimmer."""
    return entry.get('manufId') == C4_MANUF_ID


def fix_entry(entry):
    """Fix a C4 dimmer entry. Returns (fixed_entry, list_of_changes)."""
    changes = []

    # Fix interview state — this is the field Z2M actually reads
    if entry.get('interviewState') != 'SUCCESSFUL':
        old = entry.get('interviewState', '<missing>')
        entry['interviewState'] = 'SUCCESSFUL'
        changes.append(f'interviewState: {old} -> SUCCESSFUL')

    # Fix legacy boolean — kept for backwards compatibility
    if entry.get('interviewCompleted') is not True:
        entry['interviewCompleted'] = True
        changes.append('interviewCompleted: -> true')

    return entry, changes


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Fix Control4 dimmer interview state in Z2M database',
        epilog='Stop Zigbee2MQTT before running this script!')
    parser.add_argument('database', type=Path,
                        help='Path to Z2M database.db')
    parser.add_argument('--apply', action='store_true',
                        help='Actually write changes (default is dry-run)')
    args = parser.parse_args()

    db_path = args.database
    dry_run = not args.apply

    if not db_path.exists():
        print(f'Error: {db_path} not found', file=sys.stderr)
        sys.exit(1)

    # Read all lines
    raw = db_path.read_text()
    lines = raw.splitlines()
    print(f'Read {len(lines)} entries from {db_path}')
    print()

    # Parse, find C4 dimmers, fix them
    fixed_lines = []
    total_fixed = 0
    total_c4 = 0
    total_ok = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            fixed_lines.append(line)
            continue

        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError as e:
            print(f'  Warning: line {i+1} is not valid JSON, skipping ({e})')
            fixed_lines.append(line)
            continue

        if is_c4_dimmer(entry):
            total_c4 += 1
            addr = entry.get('ieeeAddr', '???')
            model = entry.get('modelId', '') or '<empty>'
            state = entry.get('interviewState', '<missing>')

            entry, changes = fix_entry(entry)

            if changes:
                total_fixed += 1
                print(f'  FIX  {addr} (model={model}, was {state}):')
                for c in changes:
                    print(f'         - {c}')
                fixed_lines.append(json.dumps(entry, separators=(',', ':')))
            else:
                total_ok += 1
                print(f'  OK   {addr} (model={model}, interview=SUCCESSFUL)')
                fixed_lines.append(line)  # preserve original formatting
        else:
            fixed_lines.append(line)

    print()
    print(f'C4 dimmers found: {total_c4}')
    print(f'  Already OK:     {total_ok}')
    print(f'  Need fixing:    {total_fixed}')

    if total_c4 == 0:
        print()
        print('No Control4 dimmers found (manufId=43981).')
        print('Make sure the dimmers are paired before running this script.')
        return

    if total_fixed == 0:
        print()
        print('Nothing to fix — all C4 dimmers already have SUCCESSFUL interview state.')
        return

    if dry_run:
        print()
        print('DRY RUN — no changes written. Use --apply to write fixes.')
        return

    # Create timestamped backup
    timestamp = datetime.now().strftime('%Y-%m-%d-%H%M%S')
    backup_path = db_path.parent / f'{db_path.name}.{timestamp}.bak'
    shutil.copy2(db_path, backup_path)
    print(f'Backup:  {backup_path}')

    # Write fixed database
    db_path.write_text('\n'.join(fixed_lines) + '\n')
    print(f'Written: {db_path}')
    print()
    print('Now restart Zigbee2MQTT to pick up the changes.')


if __name__ == '__main__':
    main()
