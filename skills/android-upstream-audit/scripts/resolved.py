#!/usr/bin/env python3
"""Which versions Gradle really ships in the release build, where they differ from what the
build asks for.

    python3 resolved.py --out <scratch dir> [--mode on|off]

A version in a build file or a BOM is a request: when another library needs a newer one, Gradle
takes the newer one, and the app ships it. facts.py reads the requests only. This script runs
Gradle once more to resolve the release runtime classpath of every application module and
writes `resolved.json` to the scratch dir: the modules whose shipped version is not one the
build asks for, with Gradle's reason.

Resolving needs the libraries' metadata: from Gradle's cache when the project was built on this
machine (seconds), otherwise downloaded (minutes). So it is a choice of the project —
`resolved_versions` in config.json, asked by --init — that `--mode` overrides for one run. A
failed or slow resolution never stops the audit: resolved.json says why, and the report says
the shipped versions were not checked.
"""
import argparse
import glob
import json
import os
import re
import sys

import common
import facts as facts_mod

TASK = 'upstreamAuditResolved'
# Seconds. A run of its own, so the configuration run keeps its own limit; past it the audit goes
# on without resolved versions rather than waiting.
RESOLVE_TIMEOUT = 300
SHOWN = 15


def read_raw(raw_dir):
    """The task's files, one per application module, merged: mismatches once per module."""
    configurations, mismatches, errors, resolved = [], {}, [], 0
    for path in sorted(glob.glob(os.path.join(raw_dir, 'resolved-*.json'))):
        data = common.load_json(path, 'resolved versions')
        configurations += [f"{data['project']} {c}" for c in data.get('configurations') or []]
        resolved = max(resolved, data.get('resolved') or 0)
        errors += data.get('errors') or []
        for m in data.get('mismatches') or []:
            entry = mismatches.setdefault(m['module'], dict(m, projects=[]))
            if data['project'] not in entry['projects']:
                entry['projects'].append(data['project'])
    return {'configurations': configurations, 'resolved': resolved, 'errors': errors,
            'mismatches': [mismatches[k] for k in sorted(mismatches)]}


# Gradle's reasons that every mismatch has; what is left says why the version moved.
PLAIN_REASONS = ('requested', 'constraint', 'by constraint')
KMP_SUFFIXES = ('-android', '-jvm', '-desktop')


def reason(text):
    kept = []
    for part in (p.strip() for p in text.split(';')):
        if part.lower() in PLAIN_REASONS or ' is in atomic group ' in part:
            continue
        if part.startswith('between versions '):
            # "between versions 1.12.0, 1.9.3, 1.9.0 and 1.8.2": other libraries asked too.
            n = len(re.split(r', | and ', part[len('between versions '):]))
            part = f'the highest of {n} versions asked by the build and its libraries'
        kept.append(part)
    return '; '.join(kept)


def grouped(mismatches):
    """One line per group of a library that moved together: `androidx.compose.ui (ui, ui-text, …)`.
    The `-android` artifacts of a multiplatform library repeat their base one and are left out."""
    names = {m['module'] for m in mismatches}
    groups = {}
    for m in mismatches:
        if m['module'].endswith(KMP_SUFFIXES) and m['module'].rsplit('-', 1)[0] in names:
            continue
        group, artifact = m['module'].split(':', 1)
        key = (group, tuple(m['asked']), m['resolved'])
        groups.setdefault(key, {'group': group, 'artifacts': [], 'asked': m['asked'],
                                'resolved': m['resolved'], 'reason': reason(m['reason'])})['artifacts'].append(artifact)
    return [groups[k] for k in sorted(groups)]


def line(g):
    artifacts = g['artifacts'][0] if len(g['artifacts']) == 1 else f"{len(g['artifacts'])} artifacts: " + \
        ', '.join(g['artifacts'][:4]) + (', …' if len(g['artifacts']) > 4 else '')
    why = f" — {g['reason']}" if g['reason'] else ''
    return f"{g['group']} ({artifacts}): asks {', '.join(g['asked'])} → ships {g['resolved']}{why}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', required=True, help='scratch directory: reads facts.json, writes resolved.json')
    p.add_argument('--mode', choices=('on', 'off'), help="this run only; default: config.json's resolved_versions")
    args = p.parse_args()
    root = common.repo_root()
    config = common.load_config(root)
    target = os.path.join(args.out, 'resolved.json')

    def write(result):
        with open(target, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f'written {target}')
        return 0

    if args.mode:
        on, source = args.mode == 'on', f'--mode {args.mode}'
    elif config.get('migrated_from') == 1:
        on, source = False, 'config.json from before this setting; run --init to choose'
    else:
        on, source = config['resolved_versions'], 'config.json'
    if not on:
        print(f'Resolved versions: off ({source}) — only the versions the build asks for are checked')
        return write({'status': 'off', 'source': source})
    build_facts = common.load_json(os.path.join(args.out, 'facts.json'), 'facts.json')
    if not build_facts['app_modules']:
        print('Resolved versions: no application module, nothing ships')
        return write({'status': 'no_app', 'source': source})

    raw_dir = os.path.join(args.out, 'resolved-raw')
    log_path = raw_dir + '.log'
    gradlew = os.path.join(root, 'gradlew.bat' if os.name == 'nt' else 'gradlew')
    print(f'Resolving the release runtime classpath of {len(build_facts["app_modules"])} application '
          f'module(s): seconds when Gradle has the libraries cached, minutes when it downloads them; '
          f'stops after {RESOLVE_TIMEOUT // 60} minutes without stopping the audit. '
          f'Progress: tail -f {log_path}', flush=True)
    code, output, seconds = facts_mod.gradle(root, gradlew, TASK, raw_dir, facts_mod.java_home(),
                                             log_path, RESOLVE_TIMEOUT)
    if code is None:
        print(f'Resolved versions: not checked — Gradle took longer than {RESOLVE_TIMEOUT // 60} minutes; '
              f'log: {log_path}')
        return write({'status': 'timeout', 'source': source, 'seconds': seconds, 'log': log_path})
    if code != 0:
        why = facts_mod.failure_lines(output)
        print('Resolved versions: not checked — Gradle failed:\n  ' + '\n  '.join(why) + f'\nlog: {log_path}')
        return write({'status': 'failed', 'source': source, 'seconds': seconds, 'log': log_path,
                      'reason': ' '.join(why)[:500]})
    result = dict(read_raw(raw_dir), status='ok', source=source, seconds=seconds)
    result['lines'] = [line(g) for g in grouped(result['mismatches'])]
    print(f"Resolved versions ({seconds} s; {', '.join(result['configurations']) or 'no release classpath'}): "
          f"{len(result['lines'])} libraries ship another version than the build asks for")
    for text in result['lines'][:SHOWN]:
        print('  SHIPS ANOTHER VERSION: ' + text)
    if len(result['lines']) > SHOWN:
        print(f"  … and {len(result['lines']) - SHOWN} more in {target}")
    for e in result['errors']:
        print('  error: ' + e)
    return write(result)


if __name__ == '__main__':
    sys.exit(main())
