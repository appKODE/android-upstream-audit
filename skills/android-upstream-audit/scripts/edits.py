"""Checks the shape of a run's edits file before slice.py uses it.

Everything the run writes by hand passes through here, so a typo fails loudly instead of being
ignored: an unknown field, a key that names no finding, a value outside its list, a date in the
wrong format. `check` returns every problem at once, as plain sentences.
"""
import datetime
import re

TOP_LEVEL = ('verified', 'carried', 'reopen', 'closed', 'findings', 'new', 'quality', 'dropped',
             'verified_good', 'verified_good_drop', 'inputs_read')
FINDING_FIELDS = ('key', 'subject', 'title', 'status', 'areas', 'section', 'severity', 'deadline',
                  'manual_check', 'what_to_check', 'anchors', 'sources', 'recommendation',
                  'blast_radius', 'effort', 'api_stability', 'last_verified')
OVERRIDE_ONLY = ('sources_add', 'anchors_add')
QUALITY_FIELDS = ('sources_ok', 'sources_failed', 'failed_urls', 'coverage_gaps')
COMPUTED = ('feed_gaps', 'stale_feeds', 'watch_gaps', 'config_gaps', 'failed_calls', 'inputs_read',
            'facts_changes', 'facts_problems')
SEVERITIES = ('high', 'medium', 'low')
EFFORTS = ('one-line', 'local refactor', 'cross-module', 'spike')
STABILITIES = ('stable', 'experimental', 'alpha', 'preview', None)
DROP_REASONS = ('not_applicable', 'no_repo_anchor', 'could_not_verify')
DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
DEADLINE = re.compile(r'^\d{4}-\d{2}(-\d{2})?$')


def _is_date(v, month_only_ok=False):
    """A real calendar date as YYYY-MM-DD; with `month_only_ok`, also YYYY-MM, checked as its
    first day. The shape alone lets 2026-02-31 through, and slice.py would crash on it."""
    if not (isinstance(v, str) and (DEADLINE if month_only_ok else DATE).match(v)):
        return False
    try:
        datetime.date.fromisoformat(v if len(v) == 10 else v + '-01')
    except ValueError:
        return False
    return True


def _is_text(v):
    return isinstance(v, str) and v.strip() != ''


def _key_list(edits, field, allowed, errors, what):
    value = edits.get(field, [])
    if not isinstance(value, list):
        errors.append(f'"{field}" must be a list of keys')
        return
    for key in value:
        if key not in allowed:
            errors.append(f'"{field}": {key!r} is not {what}')


def _anchors(where, anchors, errors):
    if not isinstance(anchors, list):
        errors.append(f'{where}: anchors must be a list')
        return
    for a in anchors:
        if not (isinstance(a, dict) and _is_text(a.get('file')) and isinstance(a.get('line'), int)
                and _is_text(a.get('excerpt'))):
            errors.append(f'{where}: an anchor needs "file" (text), "line" (number) and "excerpt" (text): {a!r}')


def _sources(where, sources, errors):
    """Rule 2: every source this run adds carries a URL, a publication date and a quote."""
    if not isinstance(sources, list):
        errors.append(f'{where}: sources must be a list')
        return
    for s in sources:
        if not isinstance(s, dict):
            errors.append(f'{where}: a source must be an object: {s!r}')
            continue
        for field in s:
            if field not in ('url', 'published', 'read', 'claim', 'unverified'):
                errors.append(f'{where}: source {s.get("url")!r} has unknown field "{field}"')
        if 'unverified' in s and not _is_text(s['unverified']):
            errors.append(f'{where}: source {s.get("url")!r}: "unverified" must say why the page could '
                          'not be saved with pages.py')
        if not _is_text(s.get('url')) or not _is_text(s.get('claim')):
            errors.append(f'{where}: a source needs "url" and a verbatim "claim": {s!r}')
        # A page that states no publication date gives the date it was read instead — never a
        # made-up publication date.
        dated = [f for f in ('published', 'read') if f in s]
        if len(dated) != 1:
            errors.append(f'{where}: source {s.get("url")!r} needs exactly one of "published" (the date the '
                          'source states) or "read" (when it states none) (rule 2)')
        elif not _is_date(s[dated[0]]):
            errors.append(f'{where}: source {s.get("url")!r}: "{dated[0]}" must be a real date, YYYY-MM-DD')


def _finding_fields(where, f, errors, allowed):
    for field in f:
        if field not in allowed:
            errors.append(f'{where}: unknown field "{field}"')
    if 'severity' in f and f['severity'] not in SEVERITIES:
        errors.append(f'{where}: severity {f["severity"]!r} — one of {SEVERITIES}')
    if 'effort' in f and f['effort'] not in EFFORTS:
        errors.append(f'{where}: effort {f["effort"]!r} — one of {EFFORTS}')
    if 'api_stability' in f and f['api_stability'] not in STABILITIES:
        errors.append(f'{where}: api_stability {f["api_stability"]!r} — one of {STABILITIES}')
    if f.get('deadline') is not None and not _is_date(f['deadline'], month_only_ok=True):
        errors.append(f'{where}: deadline {f["deadline"]!r} — a real YYYY-MM or YYYY-MM-DD, or null (rule 12)')
    if 'manual_check' in f and not isinstance(f['manual_check'], bool):
        errors.append(f'{where}: manual_check must be true or false')
    if 'last_verified' in f and not _is_date(f['last_verified']):
        errors.append(f'{where}: last_verified must be a real date, YYYY-MM-DD')
    if 'areas' in f and not isinstance(f['areas'], list):
        errors.append(f'{where}: areas must be a list')
    for field in ('anchors', 'anchors_add'):
        if field in f:
            _anchors(where, f[field], errors)
    for field in ('sources', 'sources_add'):
        if field in f:
            _sources(where, f[field], errors)


def check(edits, carried_keys, hidden_keys, good_keys=frozenset()):
    """`carried_keys` are the keys this run can edit (the previous slice's findings plus the ones
    a decision hides); `hidden_keys` are the ones a decision hides, which only `reopen` takes;
    `good_keys` are the previous slice's Verified good entries, which only `verified_good_drop`
    takes."""
    if not isinstance(edits, dict):
        return ['the edits file must hold one JSON object']
    errors = []
    for field in edits:
        if field not in TOP_LEVEL:
            errors.append(f'unknown top-level field "{field}" — allowed: {", ".join(TOP_LEVEL)}')

    new = edits.get('new', [])
    if not isinstance(new, list):
        errors.append('"new" must be a list of findings')
        new = []
    new_keys = set()
    for n in new:
        if not isinstance(n, dict) or not _is_text(n.get('key')):
            errors.append(f'"new": every finding needs a "key": {n!r}'[:300])
            continue
        if n['key'] in carried_keys:
            errors.append(f'"new": {n["key"]} already exists — change it under "findings" instead')
        new_keys.add(n['key'])
        _finding_fields(f'new {n["key"]}', n, errors, FINDING_FIELDS)
        if 'sources' not in n:
            errors.append(f'new {n["key"]}: no "sources"')

    verified = edits.get('verified', [])
    if verified != 'all':
        _key_list(edits, 'verified', carried_keys | new_keys, errors, 'a finding of this run')
    _key_list(edits, 'carried', carried_keys, errors, 'a finding of the previous slice')
    _key_list(edits, 'reopen', hidden_keys, errors, 'a finding hidden by a decision')
    _key_list(edits, 'verified_good_drop', good_keys, errors, 'a Verified good entry of the previous slice')

    findings = edits.get('findings', {})
    if not isinstance(findings, dict):
        errors.append('"findings" must be an object: {"<key>": {fields to change}}')
        findings = {}
    for key, change in findings.items():
        if key not in carried_keys:
            errors.append(f'"findings": {key!r} is not a finding of the previous slice — check the key, '
                          'or pass a new finding under "new"')
        if not isinstance(change, dict):
            errors.append(f'"findings" {key}: must be an object')
            continue
        if 'key' in change:
            errors.append(f'"findings" {key}: a key never changes — remove "key"')
        if 'sources' in change and 'sources_add' in change:
            errors.append(f'"findings" {key}: "sources" replaces the whole list and "sources_add" appends '
                          'to it — use one; to correct a wrong source, pass "sources" without it')
        _finding_fields(f'findings {key}', change, errors, FINDING_FIELDS + OVERRIDE_ONLY)

    for c in edits.get('closed', []):
        if not (isinstance(c, dict) and _is_text(c.get('key')) and _is_text(c.get('reason'))):
            errors.append(f'"closed": each entry needs "key" and "reason": {c!r}')
    for d in edits.get('dropped', []):
        if not (isinstance(d, dict) and _is_text(d.get('title'))):
            errors.append(f'"dropped": each entry needs a "title": {d!r}')
        elif d.get('reason') not in DROP_REASONS:
            errors.append(f'"dropped" {d["title"][:60]!r}: reason {d.get("reason")!r} — one of {DROP_REASONS}')
    for v in edits.get('verified_good', []):
        if not (isinstance(v, dict) and _is_text(v.get('key')) and _is_text(v.get('title'))):
            errors.append(f'"verified_good": each entry needs "key" and "title": {v!r}')

    inputs_read = edits.get('inputs_read', [])
    if not (isinstance(inputs_read, list) and all(_is_text(p) for p in inputs_read)):
        errors.append('"inputs_read" must be a list of the build-input paths this run read in full')

    quality = edits.get('quality', {})
    if not isinstance(quality, dict):
        errors.append('"quality" must be an object')
        quality = {}
    for field, value in quality.items():
        if field in COMPUTED:
            errors.append(f'"quality" {field}: slice.py takes it from the scripts\' own output — remove it')
        elif field not in QUALITY_FIELDS:
            errors.append(f'"quality": unknown field "{field}" — allowed: {", ".join(QUALITY_FIELDS)}')
        elif field in ('sources_ok', 'sources_failed') and not isinstance(value, int):
            errors.append(f'"quality" {field} must be a number')
        elif field not in ('sources_ok', 'sources_failed') and not isinstance(value, list):
            errors.append(f'"quality" {field} must be a list')
    return errors
