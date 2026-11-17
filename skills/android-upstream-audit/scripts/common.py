"""Shared helpers for the android-upstream-audit scripts. Standard library only."""
from __future__ import annotations

import datetime
import glob
import json
import os
import re
import pathlib
import subprocess

FRESH_DAYS = 7
MAX_AGE_DAYS = 90
FIRST_RUN_DAYS = 90  # how far back a run looks when there is no previous slice
CHECK_EXPIRY_DAYS = 180

# Format versions of the files the skill keeps in the project. Raise one in the commit that
# changes its format; a file of another version is handled by load_config and load_slice.
CONFIG_SCHEMA = 2
# How a config.json of an earlier schema becomes the current one without asking anybody: the new
# field gets the value that keeps the old behaviour. A change that needs a person's answer has no
# entry here, and such a file stops the audit as a breaking update.
CONFIG_MIGRATIONS = {
    1: lambda c: dict(c, schema=2, resolved_versions=False),
}
SLICE_SCHEMA = 7

CONFIG = os.path.join('upstream-audit', 'config.json')
SETTINGS_FILES = ('settings.gradle', 'settings.gradle.kts')
REPO_RE = re.compile(r'^[\w.-]+/[\w.-]+$')
DECISION_RE = re.compile(r'^(checked|declined|done)\s+(\d{4}-\d{2}-\d{2})\s+(\S+)\s+—\s+(.+)$')


def curl(url: str, path: str | None = None, headers: list[str] | None = None) -> str | None:
    """Downloads with curl, not urllib: the system Python may ship without CA certificates.
    Headers go through stdin (`-H @-`), not the command line."""
    cmd = ['curl', '-sSL', '--fail', '--max-time', '90', '-A', 'android-upstream-audit/1.0', url]
    if path:
        cmd += ['-o', path]
    if headers:
        cmd += ['-H', '@-']
    r = subprocess.run(cmd, capture_output=True, input='\n'.join(headers).encode() if headers else None)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode('utf-8', 'replace').strip() or f'curl exit {r.returncode}')
    return None if path else r.stdout.decode('utf-8', 'replace')


def skill_version(plugin_json: str | None = None) -> str:
    """The version in the plugin's plugin.json, three levels above this folder once the skill is
    installed as a plugin; "dev" when the skill is a copy in a project's .claude/skills/."""
    path = pathlib.Path(plugin_json or pathlib.Path(__file__).resolve().parents[3] / '.claude-plugin' / 'plugin.json')
    try:
        version = json.loads(path.read_text(encoding='utf-8')).get('version')
    except (OSError, ValueError, AttributeError):
        return 'dev'
    return version if isinstance(version, str) and version.strip() else 'dev'


def written_by(data: dict) -> str:
    return f"skill {data['skill_version']}" if isinstance(data.get('skill_version'), str) else 'an earlier skill'


def repo_root(start: str | None = None) -> str:
    """Walks up from `start` to the root of the Gradle build: the nearest directory with a
    settings script and `upstream-audit/`. Before --init has made that folder, the outermost
    directory with a settings script up to the git checkout's root — an included build has a
    settings script of its own."""
    d = os.path.abspath(start or os.getcwd())
    outermost = None
    while True:
        if any(os.path.exists(os.path.join(d, name)) for name in SETTINGS_FILES):
            if os.path.isdir(os.path.join(d, 'upstream-audit')):
                return d
            outermost = d
        parent = os.path.dirname(d)
        if parent == d or os.path.exists(os.path.join(d, '.git')):
            break
        d = parent
    if outermost is None:
        raise SystemExit('cannot find the project root: no settings.gradle or settings.gradle.kts '
                         'in this directory or above it — run from the root of a Gradle build')
    return outermost


def newest_slice(root: str) -> str | None:
    files = sorted(glob.glob(os.path.join(root, 'upstream-audit', 'runs', '*.json')))
    return files[-1] if files else None


def load_json(path: str, what: str) -> object:
    """Reads a JSON file, or stops with a sentence that names the file and the problem."""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        raise SystemExit(f'{what} not found: {path}')
    except json.JSONDecodeError as e:
        raise SystemExit(f'{what} is not valid JSON: {path}, line {e.lineno}, column {e.colno}: {e.msg}')


def config_errors(config: object) -> list[str]:
    """Everything wrong with a config.json, all at once."""
    if not isinstance(config, dict):
        return ['config.json must be a JSON object']
    errors = []
    known = {'schema', 'skill_version', 'base_branch', 'releases', 'commits', 'vendored', 'inputs',
             'resolved_versions'}
    errors += [f'unknown field "{k}"' for k in config if k not in known]
    if config.get('schema') != CONFIG_SCHEMA:
        errors.append(f'"schema" must be {CONFIG_SCHEMA}')
    if not isinstance(config.get('resolved_versions'), bool):
        errors.append('"resolved_versions" must be true or false')
    if 'skill_version' in config and not isinstance(config['skill_version'], str):
        errors.append('"skill_version" must be text')
    if not isinstance(config.get('base_branch'), str) or not config.get('base_branch'):
        errors.append('"base_branch" must be a branch name')
    for field in ('releases', 'commits'):
        repos = config.get(field, [])
        if not isinstance(repos, list) or not all(isinstance(r, str) and REPO_RE.match(r) for r in repos):
            errors.append(f'"{field}" must be a list of "owner/repo"')
    vendored = config.get('vendored', {})
    if not isinstance(vendored, dict):
        errors.append('"vendored" must be an object {path: {"github": "owner/repo"} | {"watch": false, "reason": "..."}}')
    else:
        for path, entry in vendored.items():
            ok = isinstance(entry, dict) and (
                (set(entry) == {'github'} and isinstance(entry['github'], str) and REPO_RE.match(entry['github']))
                or (set(entry) == {'watch', 'reason'} and entry['watch'] is False
                    and isinstance(entry['reason'], str) and entry['reason'].strip()))
            if not ok:
                errors.append(f'vendored "{path}": expected {{"github": "owner/repo"}} or '
                              '{"watch": false, "reason": "..."}')
    inputs = config.get('inputs', {})
    if not isinstance(inputs, dict) or set(inputs) - {'include', 'exclude'} or not all(
            isinstance(inputs.get(k, []), list) for k in ('include', 'exclude')):
        errors.append('"inputs" must be {"include": [paths], "exclude": [paths]}')
    return errors


def load_config(root: str, required: bool = True, for_init: bool = False) -> dict | None:
    """Reads upstream-audit/config.json; without it a run stops and asks for --init.
    A file of a later schema stops everything: a newer skill wrote it, and this one would
    misread it. A file of an earlier schema is brought up to date by CONFIG_MIGRATIONS when
    they cover it ("migrated_from" tells the run to say so); otherwise it stops the audit with
    a breaking update (--init asks again), and --init itself takes it as the suggestions."""
    path = os.path.join(root, CONFIG)
    if not os.path.exists(path):
        if required:
            raise SystemExit(f'no {CONFIG} — run /android-upstream-audit --init first, then commit the file')
        return None
    config = load_json(path, 'config.json')
    schema = config.get('schema') if isinstance(config, dict) else None
    if isinstance(schema, int) and schema > CONFIG_SCHEMA:
        raise SystemExit(f'{CONFIG} has schema {schema}, written by {written_by(config)}; this skill '
                         f'({skill_version()}) reads schema {CONFIG_SCHEMA} — update the skill')
    migrated_from = schema
    while isinstance(schema, int) and schema < CONFIG_SCHEMA and schema in CONFIG_MIGRATIONS:
        config = CONFIG_MIGRATIONS[schema](config)
        schema = config['schema']
    if isinstance(schema, int) and schema < CONFIG_SCHEMA:
        if for_init:
            return config
        raise SystemExit(f'breaking update: {CONFIG} has schema {schema}, this skill ({skill_version()}) '
                         f'needs {CONFIG_SCHEMA} — run /android-upstream-audit --init again (it suggests '
                         'the current answers), then commit the file')
    errors = config_errors(config)
    if errors:
        raise SystemExit(f'{CONFIG} is not valid — fix it or run --init again:\n  - ' + '\n  - '.join(errors))
    if migrated_from != schema:
        config['migrated_from'] = migrated_from
    return config


def load_slice(path: str) -> dict:
    data = load_json(path, 'run slice')
    if not isinstance(data, dict) or not isinstance(data.get('findings'), list) or 'run' not in data:
        raise SystemExit(f'run slice {path} has no "run" or "findings" — it is not a slice')
    # An earlier schema is read as it is: the scripts keep reading every older slice.
    schema = data.get('schema')
    if isinstance(schema, int) and schema > SLICE_SCHEMA:
        raise SystemExit(f'run slice {path} has schema {schema}, written by {written_by(data["run"])}; '
                         f'this skill ({skill_version()}) reads schema up to {SLICE_SCHEMA} — update the skill')
    return data


def today() -> datetime.date:
    return datetime.datetime.now(datetime.timezone.utc).date()


def age_days(iso_date: str, now: datetime.date | None = None) -> int:
    return ((now or today()) - datetime.date.fromisoformat(iso_date[:10])).days


def read_lines(root: str, rel: str) -> list[str] | None:
    path = os.path.join(root, rel)
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read().split('\n')


def locate(root: str, anchor: dict) -> tuple[str, list[int]]:
    """Returns (status, lines): OK at the recorded line, MOVED, GONE, or FILE_GONE."""
    lines = read_lines(root, anchor['file'])
    if lines is None:
        return 'FILE_GONE', []
    hits = [i + 1 for i, line in enumerate(lines) if anchor['excerpt'] in line]
    if not hits:
        return 'GONE', []
    return ('OK' if anchor['line'] in hits else 'MOVED'), hits


def nearest(hits: list[int], line: int) -> int:
    return min(hits, key=lambda h: abs(h - line))


def known_keys(slice_: dict) -> set[str]:
    keys = {f['key'] for f in slice_.get('findings', [])}
    for section in ('closed', 'checked', 'suppressed'):
        for item in slice_.get(section, []) or []:
            if isinstance(item, dict) and 'key' in item:
                keys.add(item['key'])
            elif isinstance(item, str):
                keys.add(item)
    return keys


def read_decisions(root: str) -> tuple[dict[str, dict], list[str]]:
    """Returns (decisions by key, unparseable lines). The latest date wins per key."""
    lines = read_lines(root, os.path.join('upstream-audit', 'decisions.txt'))
    decisions, unparseable = {}, []
    for raw in lines or []:
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        m = DECISION_RE.match(line)
        if not m:
            unparseable.append(raw)
            continue
        status, date, key, reason = m.groups()
        if key not in decisions or date >= decisions[key]['date']:
            decisions[key] = {'status': status, 'date': date, 'key': key, 'reason': reason.strip()}
    return decisions, unparseable
