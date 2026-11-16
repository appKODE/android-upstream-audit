#!/usr/bin/env python3
"""Re-locates every anchor of the previous run slice in the current tree.

    python3 anchors.py [--slice PATH] [--json]

For each finding it prints where each anchor's excerpt is now (OK / MOVED / GONE /
FILE_GONE) and how old the finding's last verification is, so the run knows which
findings must be re-verified upstream (see "Freshness" in SKILL.md).
"""
import argparse
import json
import sys

import common


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--slice')
    p.add_argument('--json', action='store_true')
    args = p.parse_args()

    root = common.repo_root()
    path = args.slice or common.newest_slice(root)
    if not path:
        print('no previous run slice: first run, nothing to re-locate')
        return 0
    prev = common.load_slice(path)
    fallback = prev['run']['started'][:10]

    rows, problems = [], 0
    for f in prev['findings']:
        anchors = []
        for a in f['anchors']:
            status, hits = common.locate(root, a)
            if status != 'OK':
                problems += 1
            anchors.append({'file': a['file'], 'recorded': a['line'], 'status': status,
                            'now': common.nearest(hits, a['line']) if hits else None,
                            'all': hits[:6], 'excerpt': a['excerpt']})
        age = common.age_days(f.get('last_verified') or fallback)
        need = 'FULL' if age > common.MAX_AGE_DAYS else ('RECHECK' if age > common.FRESH_DAYS else 'fresh')
        rows.append({'key': f['key'], 'subject': f['subject'], 'last_verified': f.get('last_verified') or fallback,
                     'age_days': age, 'verify': need, 'anchors': anchors})

    if args.json:
        json.dump(rows, sys.stdout, ensure_ascii=False, indent=2)
        return 0

    print(f'previous slice: {path}')
    for r in rows:
        bad = [a for a in r['anchors'] if a['status'] != 'OK']
        flag = '' if r['verify'] == 'fresh' else f"  <-- {r['verify']} ({r['age_days']} days)"
        print(f"{'!!' if bad else 'ok'} {r['key']}  [verified {r['last_verified']}]{flag}")
        for a in bad:
            print(f"     {a['status']:9} {a['file']}:{a['recorded']} -> {a['all'] or '-'}   excerpt: {a['excerpt']!r}")
    total = sum(len(r['anchors']) for r in rows)
    stale = sum(r['verify'] != 'fresh' for r in rows)
    print(f'\nanchors: {total} checked, {problems} not at the recorded line')
    print(f'findings: {len(rows)}, to re-verify upstream (older than {common.FRESH_DAYS} days): {stale}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
