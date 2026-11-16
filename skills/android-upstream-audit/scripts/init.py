#!/usr/bin/env python3
"""Prepares and writes upstream-audit/config.json for `/android-upstream-audit --init`.

    python3 init.py plan --out SCRATCH [--json] [--facts FACTS.json]
    python3 init.py write --from ANSWERS.json

`plan` prints what the questions of --init are about, each with a suggested answer: the base
branch (origin/HEAD), the vendored artifacts Gradle's facts list, the GitHub repositories to
follow, and the key files. Suggestions come from the current config.json; without one, they
are empty. It runs facts.py's Gradle init script (about 20 s, needs JAVA_HOME) unless
`--facts` names a facts.json already written; a build that does not configure stops it.

`write` checks the answers — a config.json-shaped object — against the project and writes
them. It refuses an unknown field, a malformed repository, a branch origin does not have, and a
vendored path or an included file that does not exist.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

import common
import facts as facts_mod

ERRORS_SHOWN = 10


def git(root, *args):
    r = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def remote_branches(root):
    """The conventional base branches origin has; a project can have hundreds of others."""
    out = git(root, 'branch', '-r', '--format=%(refname:short)') or ''
    names = {b[len('origin/'):] for b in out.split('\n') if b.startswith('origin/')}
    return [b for b in ('main', 'master', 'develop', 'dev', 'trunk') if b in names]


def default_branch(root):
    head = git(root, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD')
    return head[len('origin/'):] if head and head.startswith('origin/') else None


def plan(root, build_facts=None, out=None):
    config = common.load_config(root, required=False, for_init=True)
    source = 'config.json' if config else None
    base = config or {}
    if build_facts is None and out:
        # A known folder: the log path can be named before Gradle starts, and the facts stay.
        build_facts = facts_mod.collect(root, os.path.join(out, 'gradle-facts'), config=config)
    elif build_facts is None:
        with tempfile.TemporaryDirectory() as raw:
            build_facts = facts_mod.collect(root, raw, config=config)
    detected = default_branch(root)
    suggested = {
        'schema': common.CONFIG_SCHEMA,
        'base_branch': (config or {}).get('base_branch') or detected,
        'releases': base.get('releases', []),
        'commits': base.get('commits', []),
        'vendored': {v['path']: base.get('vendored', {}).get(v['path']) for v in build_facts['vendored']},
        'inputs': (config or {}).get('inputs', {'include': [], 'exclude': []}),
        # On when nobody chose yet: a config from before the setting was migrated with it off.
        'resolved_versions': (config['resolved_versions'] if config and 'resolved_versions' in config
                              and config.get('migrated_from') is None else True),
    }
    return {'suggestions_from': source, 'origin_head': detected, 'branches': remote_branches(root),
            'suggested': suggested,
            'vendored': [dict(v, suggested=suggested['vendored'][v['path']]) for v in build_facts['vendored']],
            'key_files': build_facts['key_files'],
            'problems': build_facts['version_warnings'] + build_facts['problems'],
            'errors': build_facts.get('errors', []),
            'stale_vendored': sorted(set(base.get('vendored', {})) - set(suggested['vendored']))}


def check(root, answers):
    errors = common.config_errors(answers)
    if errors:
        return errors
    branch = answers['base_branch']
    if git(root, 'rev-parse', '--verify', '--quiet', f'refs/remotes/origin/{branch}') is None:
        errors.append(f'base_branch "{branch}": origin has no such branch (run `git fetch origin` if it is new)')
    for path in answers.get('vendored', {}):
        if not os.path.exists(os.path.join(root, path)):
            errors.append(f'vendored "{path}": no such path')
    for path in answers.get('inputs', {}).get('include', []):
        if not os.path.isfile(os.path.join(root, path)):
            errors.append(f'inputs.include "{path}": no such file')
    return errors


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    sub_plan = sub.add_parser('plan')
    sub_plan.add_argument('--json', action='store_true')
    sub_plan.add_argument('--facts', help='a facts.json facts.py already wrote, instead of running Gradle')
    sub_plan.add_argument('--out', help="scratch directory: Gradle's log goes to OUT/gradle-facts.log, "
                                        'the raw facts to OUT/gradle-facts/')
    sub_write = sub.add_parser('write')
    sub_write.add_argument('--from', dest='source', required=True)
    args = p.parse_args()
    root = common.repo_root()

    if args.cmd == 'write':
        answers = common.load_json(args.source, 'answers')
        if isinstance(answers, dict):
            answers['skill_version'] = common.skill_version()
        errors = check(root, answers)
        if errors:
            print('NOT WRITTEN — fix these first:')
            for e in errors:
                print('  - ' + e)
            return 1
        path = os.path.join(root, common.CONFIG)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(answers, f, ensure_ascii=False, indent=2)
            f.write('\n')
        print(f'written {path} — commit it; the audit reads it on every run')
        return 0

    result = plan(root, common.load_json(args.facts, 'facts.json') if args.facts else None, args.out)
    if args.json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0
    print(f"Suggestions from: {result['suggestions_from'] or 'nothing — no config.json yet'}")
    print(f"\n## Base branch: origin/HEAD = {result['origin_head'] or 'not set'}; "
          f"suggested {result['suggested']['base_branch'] or 'none — ask'}")
    print(f"conventional branches on origin: {', '.join(result['branches']) or 'none'}")
    print(f"\n## Vendored artifacts ({len(result['vendored'])})")
    for v in result['vendored']:
        s = v['suggested']
        answer = (f"github {s['github']}" if s and 'github' in s else
                  f"not watched: {s['reason']}" if s else 'no answer yet')
        print(f"{v['path']} — {v['reason']} — {answer}")
    for path in result['stale_vendored']:
        print(f'{path} — named before, no longer used by the build; it will be dropped')
    print('\n## GitHub repositories followed besides the toolchain and the pins')
    print(f"releases: {', '.join(result['suggested']['releases']) or 'none'}")
    print(f"commits: {', '.join(result['suggested']['commits']) or 'none'}")
    inputs = result['suggested']['inputs']
    print(f"\n## Key files (read in full on a first run): {len(result['key_files'])}; "
          f"include {inputs['include'] or 'none'}, exclude {inputs['exclude'] or 'none'}")
    print(f"\n## Shipped versions (resolved_versions): suggested {'on' if result['suggested']['resolved_versions'] else 'off'}")
    print('On: every run starts Gradle a second time (task upstreamAuditResolved, nothing compiled) to resolve the '
          'release runtime classpath of each application module, and lists the libraries that ship another '
          'version than the build asks for (a BOM or another library raised it). Seconds when Gradle has the '
          'libraries cached; minutes when it downloads their metadata (a fresh machine, a CI job without the '
          'Gradle cache); stops after 5 minutes without stopping the audit. Off: such a version is not seen. '
          'A run can override it: --resolved-versions on|off.')
    for line in result['problems']:
        print(f'PROBLEM: {line}')
    # The facts live in a temporary folder that is gone after plan; the errors are printed here.
    for line in result['errors'][:ERRORS_SHOWN]:
        print(f'  error: {line}')
    if len(result['errors']) > ERRORS_SHOWN:
        print(f"  … and {len(result['errors']) - ERRORS_SHOWN} more")
    return 0


if __name__ == '__main__':
    sys.exit(main())
