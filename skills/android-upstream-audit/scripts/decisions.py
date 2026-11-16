#!/usr/bin/env python3
"""Reads upstream-audit/decisions.txt and matches it against the previous run slice.

    python3 decisions.py [--slice PATH] [--json]

Reports each decision as applied / expired / reopened / unknown key, and every line it could not
parse. Keys are matched exactly; for an unknown key the closest existing key is printed
as a hint only — the decision is never applied to it.
"""
import argparse
import difflib
import json
import sys

import common


def evaluate(root, prev, new_keys=()):
    """`new_keys` are the findings this run adds: on a first run they are the only keys there
    are, and a decision must apply to them as much as to a carried finding."""
    decisions, unparseable = common.read_decisions(root)
    keys = (common.known_keys(prev) if prev else set()) | set(new_keys)
    # A run that reopened a finding recorded the decision it overrode; that decision stays
    # overridden until a person adds a newer line for the key.
    reopened = {r['key']: r['date'] for r in (prev or {}).get('reopened', []) or []}
    out = []
    for d in decisions.values():
        item = dict(d)
        item['age_days'] = common.age_days(d['date'])
        if d['key'] not in keys and d['status'] == 'done':
            # The run that read it closed the finding; the line stays as the person's record.
            item['state'] = 'done'
        elif d['key'] not in keys:
            item['state'] = 'unknown_key'
            close = difflib.get_close_matches(d['key'], sorted(keys), n=1, cutoff=0.8)
            item['closest'] = close[0] if close else None
        elif d['key'] in reopened and d['date'] <= reopened[d['key']]:
            item['state'] = 'reopened'
        elif d['status'] == 'checked' and item['age_days'] > common.CHECK_EXPIRY_DAYS:
            item['state'] = 'expired'
        else:
            item['state'] = 'applied'
        out.append(item)
    return out, unparseable


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--slice')
    p.add_argument('--json', action='store_true')
    args = p.parse_args()

    root = common.repo_root()
    path = args.slice or common.newest_slice(root)
    prev = common.load_slice(path) if path else None
    items, unparseable = evaluate(root, prev)

    if args.json:
        json.dump({'decisions': items, 'unparseable': unparseable}, sys.stdout, ensure_ascii=False, indent=2)
        return 0
    if not items and not unparseable:
        print('decisions.txt: no decisions')
        return 0
    for d in items:
        extra = ''
        if d['state'] == 'unknown_key':
            extra = f"   closest existing key: {d['closest'] or 'none'}"
        if d['state'] == 'expired':
            extra = f"   older than {common.CHECK_EXPIRY_DAYS} days: bring the finding back as re-check"
        if d['state'] == 'reopened':
            extra = '   overridden by an earlier run: reported until a person adds a newer line'
        print(f"{d['state']:11} {d['status']:8} {d['date']} {d['key']} — {d['reason']}{extra}")
    for line in unparseable:
        print(f'unparseable  {line}')
    print('\nA checked decision also expires when upstream published something for its key after the '
          'check date, or its anchor changed; that part is for the run to judge.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
