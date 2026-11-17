#!/usr/bin/env python3
"""Builds the run slice from the previous slice plus the run's edits, and validates it.

    python3 slice.py --started 2026-10-08T12:55:10Z --model MODEL --edits EDITS.json [--dry-run]

What it does without being told:
  * carries every finding of the previous slice forward as `unchanged`;
  * rewrites each anchor's line to where its excerpt is now;
  * hides findings with a `declined` or a live `checked` decision, and reports decisions
    with an unknown key;
  * records the audited commit, the commit of the base branch (config.json) it is based on,
    and the distance to that branch;
  * carries the `verified_good` entries of the previous slice forward;
  * computes the window, the digest still-open block and the trend, shown and upkeep lines;
  * takes the feed gaps from feeds.py's feeds.json, the GitHub client and the unchecked
    dependencies from releases.py's releases.json, checks config.json against the build facts,
    and prints the report's header lines;
  * keeps a snapshot of facts.py's build facts and prints what changed since the previous
    slice; when that slice kept none, refuses the slice until every key file is listed under
    `inputs_read`;
  * prints the report entries of sections 1–6, ready to paste: one line per unchanged
    finding, the full entry of each new or changed one (sources with their quotes,
    recommendation, blast radius, effort), days to each deadline.

What the run supplies in EDITS.json (every field optional):
  {
    "verified": ["<key>", ...] | "all",      upstream fact re-checked today -> last_verified = today
    "carried":  ["<key>", ...],              older than the freshness limit, knowingly not re-checked
    "reopen":   ["<key>", ...],              hidden by a decision, but must be reported again
    "closed":   [{"key": "...", "reason": "..."}],
    "findings": {"<key>": {<fields to overwrite>, "sources_add": [...], "anchors_add": [...]}},
    "new":      [{<a complete finding>}],
    "quality":  {"sources_ok": 0, "sources_failed": 0, "failed_urls": [], "coverage_gaps": []},
    "dropped":  [{"title": "...", "reason": "..."}],
    "verified_good": [{"key": "...", "title": "..."}],    added to the carried ones; same key replaces
    "verified_good_drop": ["<key>", ...]                  carried entries that no longer hold
  }

The script writes the slice and the report's header and finding sections, every finding in full,
and refuses to write either while one breaks a rule of SKILL.md.
"""
import argparse
import json
import os
import subprocess
import sys

import common
import decisions as decisions_mod
import edits as edits_mod
import facts as facts_mod
import pages as pages_mod
import releases as releases_mod

REQUIRED = ('key', 'subject', 'title', 'status', 'last_verified', 'severity',
            'anchors', 'sources', 'blast_radius', 'effort')
STATUSES = ('new', 'changed', 'unchanged')
AREAS = ('kotlin', 'build', 'google-play', 'platform', 'compose', 'deps', 'security')
# The report section a finding is listed under, in report order.
SECTIONS = (('deadlines', 'Deadlines'), ('requires-work', 'Requires work'),
            ('worth-adopting', 'Worth adopting'), ('design', 'Needs design sign-off'),
            ('blocked-debt', 'Blocked debt'))


def days_left(deadline, today):
    """Days to a deadline given as YYYY-MM or YYYY-MM-DD; a month counts to its first day and
    is marked approximate (rule 12)."""
    exact = len(deadline) == 10
    target = common.datetime.date.fromisoformat(deadline if exact else deadline + '-01')
    return ('' if exact else '~') + str((target - today).days)


def report_entry(f, today):
    anchors = ', '.join(f"`{a['file']}:{a['line']}`" for a in f['anchors'])
    key_line = f"key: `{f['key']}` · areas: {', '.join(f['areas'])}"
    marks, check = [], []
    if f.get('deadline'):
        left = days_left(f['deadline'], today)
        if left.lstrip('~').startswith('-'):
            # Past its date the finding stays until a person says what happened: a run cannot see
            # an exemption filed in Play Console, so it never closes one on the date alone.
            marks.append(f"**overdue {left.replace('-', '')} days**")
            check = ["The deadline has passed. If it is done (shipped, or an exemption filed), add to "
                     "`upstream-audit/decisions.txt`:",
                     f"`done <YYYY-MM-DD> {f['key']} — <what was done>`",
                     'If not, the requirement is in force: see the recommendation.']
        else:
            marks.append(f"**{left} days**")
    if f.get('manual_check'):
        marks.append('**manual check**')
    if f.get('manual_check'):
        # Rule 14: a manual check ends with what to look at and the line that answers it.
        check += [f"What to check: {f['what_to_check']} To answer, add to `upstream-audit/decisions.txt`:",
                  f"`checked <YYYY-MM-DD> {f['key']} — <what you saw>`"]
    sources = []
    for s in f['sources']:
        mark = f" — **unverified quote:** {s['unverified']}" if s.get('unverified') else ''
        when = (s['published'] if s.get('published') else
                f"read {s['read']}, no publication date" if s.get('read') else 'date unknown')
        sources += [f"Source: {s['url']} ({when}){mark}", f"> {s['claim']}"]
    facts = [f"Severity: {f['severity']}", f"Effort: {f['effort']}"]
    if f.get('api_stability'):
        facts.append(f"API stability: {f['api_stability']}")
    return '\n'.join([f"### {f['subject']}: {f['title']} — **{f['status'].upper()}**", key_line, '',
                      ' · '.join(['Anchors: ' + anchors, *marks]), '', *sources, '',
                      f"**Recommendation:** {f['recommendation']}", '',
                      f"Blast radius: {f['blast_radius']}", ' · '.join(facts), *check, ''])


def upkeep_line(out):
    """The digest line that says the run did not see everything; 'none' when it did."""
    q, parts = out['quality'], []
    if q.get('config_gaps'):
        parts.append(f"config.json needs {len(q['config_gaps'])} updates (run --init)")
    if q.get('facts_problems'):
        parts.append(f"{len(q['facts_problems'])} build-facts problems")
    if q.get('unverified_claims'):
        parts.append(f"{len(q['unverified_claims'])} quotes not checked against their page")
    for name in q.get('failed_feeds', []):
        parts.append(f'feed failed: {name}')
    for g in q.get('feed_gaps', []):
        parts.append(f"feed gap in {g['feed']} ({g['days']} days)")
    for s in q.get('stale_feeds', []):
        parts.append(f"{s['feed']} feed silent for {s['days']} days")
    if q.get('sources_failed'):
        parts.append(f"{q['sources_failed']} sources failed")
    if q.get('github_client') == 'curl':
        parts.append('GitHub queried with curl, unauthenticated (60 requests an hour) — install and authenticate gh')
    if q.get('dependencies_not_checked'):
        parts.append(f"{len(q['dependencies_not_checked'])} dependencies not checked")
    if (q.get('resolved_versions') or {}).get('status') in ('failed', 'timeout'):
        parts.append('shipped versions not resolved')
    if out['run'].get('commits_behind_base'):
        parts.append(f"branch {out['run']['commits_behind_base']} commits behind {out['run']['base_branch']}")
    return f"🔧 *Audit upkeep:* {' · '.join(parts)} — see Coverage" if parts else 'none'


def still_open_block(findings):
    """The digest block listing high-severity findings that no other block would show this time;
    'none' when there are none. Deadlines are left out because 🚨 relists them every run."""
    subjects = [f['subject'] for f in findings
                if f['status'] == 'unchanged' and f['severity'] == 'high'
                and not (f.get('deadline') and not f.get('manual_check'))]
    return ('\n'.join(['📌 *Still open*'] + [f'• *{s}* — <consequence>' for s in subjects])
            if subjects else 'none')


def window(prev, run_date):
    """The days this run looked at: from the previous run, or FIRST_RUN_DAYS back on a first run."""
    to = common.datetime.date.fromisoformat(run_date)
    start = (common.datetime.date.fromisoformat(prev['run']['started'][:10]) if prev
             else to - common.datetime.timedelta(days=common.FIRST_RUN_DAYS))
    return {'from': start.isoformat(), 'to': run_date, 'days': (to - start).days, 'first_run': not prev}


def day(iso, digest, year=False):
    """"13 Aug" for the digest, the ISO date for the report."""
    if not digest:
        return iso
    d = common.datetime.date.fromisoformat(iso)
    # English month names whatever the locale: the digest is in English.
    month = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()[d.month - 1]
    return f'{d.day} {month}' + (f' {d.year}' if year else '')


def span(start, end, digest):
    """"13 Aug → 11 Nov (90 days)"; the year only when the span crosses New Year."""
    s, e = (common.datetime.date.fromisoformat(d) for d in (start, end))
    year = s.year != e.year
    return f'{day(start, digest, year)} → {day(end, digest, year)} ({(e - s).days} days)'


def versions_depth(q, digest):
    """How deep the version check went: what the build asks for only, or also what Gradle ships
    in the release build (resolved.py). None for a slice from before resolved.py."""
    r = q.get('resolved_versions')
    if not r:
        return None
    state = r['status']
    if state == 'ok':
        text = ('as the build asks for them and as the release build ships them'
                + ('' if digest else f" ({len(r.get('lines', []))} libraries ship another version)"))
    elif state == 'no_app':
        text = 'as the build asks for them; no application module ships them'
    elif state == 'off':
        text = 'as the build asks for them only — resolving what ships is off' + ('' if digest else f" ({r['source']})")
    else:
        text = ('as the build asks for them only — resolving what ships '
                + ('took too long' if state == 'timeout' else 'failed') + ', see Coverage')
    return f'🔎 Versions: {text}' if digest else f'Versions: {text}'


def checked(out, digest):
    """What this run actually saw, for the report header and under the digest title.

    The window is what the run asked for, not what it saw: a feed holds only its latest posts, so
    on a first run (90 days) a blog may reach back only a month or two. Versions and releases
    come with their dates and cover the whole window. Without a gap, one line; with one, a line
    per feed that fell short, the rest, and the window asked for, set apart."""
    q, w = out['quality'], out['run']['window']
    short = []
    for g in q['feed_gaps']:
        # The gap ends where the feed's oldest entry is: from there on, the feed was seen.
        short.append(f"{g['feed']}: {span(g['to'][:10], w['to'], digest)}")
    for name in q.get('failed_feeds', []):
        short.append(f'{name}: not seen (download failed)')
    for s in q['stale_feeds']:
        short.append(f"{s['feed']}: silent since {day(s['newest'][:10], digest)} — may have moved")
    whole = span(w['from'], w['to'], digest)
    if digest:
        depth = versions_depth(q, digest=True)
        if not short:
            return '\n'.join([f'🗓️ Checked: {whole}'] + ([depth] if depth else []))
        return '\n'.join(['🗓️ Checked:'] + [f'• {line}' for line in short]
                         + [f'• Versions, releases and the other feeds: {whole}', f'_Window asked: {whole}_']
                         + ([depth] if depth else []))
    run = 'first run' if w['first_run'] else f"previous run: {out['run']['prior_run']}"
    if not short:
        return f'Checked: {whole} · {run}'
    # Blank lines around the italic line: Markdown would join it to the list and the next line.
    return '\n'.join(['Checked:'] + [f'- {line}' for line in short]
                     + [f'- Versions, releases and the other feeds: {whole}', '',
                        f'_Window asked: {whole} · {run}_', ''])


def report_header(out):
    """The lines that open the report: what was looked at, and what this run could not see."""
    q, run = out['quality'], out['run']
    looked = checked(out, digest=False)
    reported, dropped = len(out['findings']), len(out['dropped'])
    hidden = len(q.get('hidden_new_findings', []))
    behind, base = run.get('commits_behind_base'), f"origin/{run['base_branch']}"
    tree = (f'current with {base}' if behind == 0 else
            f'{behind} commits behind {base}' if behind else f'distance to {base} unknown')
    gaps = '; '.join([f'{name} — download failed' for name in q.get('failed_feeds', [])]
                     + [f"{g['feed']} {g['from']} → {g['to']} ({g['days']} days)" for g in q['feed_gaps']]
                     + [f"{s['feed']} silent since {s['newest']} — may have moved" for s in q['stale_feeds']])
    config = (f"needs update — {len(q['config_gaps'])} entries (see Coverage)" if q['config_gaps'] else 'up to date')
    return '\n'.join([
        looked,
        f"Candidates considered: {reported + hidden + dropped} · reported: {reported} · "
        + (f'hidden by a decision: {hidden} · ' if hidden else '')
        + f"dropped: {dropped} · "
        f"sources fetched: {q['sources_ok'] + q['sources_failed']} ({q['sources_failed']} failed)",
        *([versions_depth(q, digest=False)] if versions_depth(q, digest=False) else []),
        f'Audited tree: {tree}',
        f"Feed gaps: {gaps or 'none'}",
        f'config.json: {config}',
        f"Skill: {run.get('skill_version', 'unknown')}"])


def report_text(out, today):
    """The report up to its Fetch log: title, header, every finding in full, what this run closed,
    Verified good, Considered and dropped. The run adds Fetch log, Coverage and the digest."""
    started = out['run']['started']
    lines = [f"# Upstream audit — {started[:10]} {started[11:16]} UTC", '', report_header(out)]
    for section, heading in SECTIONS:
        entries = [f for f in out['findings'] if f.get('section') == section]
        if not entries:
            continue
        lines += ['', f'## {heading}', '']
        # New and changed findings lead their section.
        for f in sorted(entries, key=lambda f: f['status'] == 'unchanged'):
            lines.append(report_entry(f, today))
    if out['closed']:
        lines += ['', '## Closed this run', '']
        for c in out['closed']:
            name = f"**{c['subject']}** — {c['title']} · " if c.get('subject') else ''
            lines.append(f"- {name}key: `{c['key']}` · {c['reason']}")
    lines += ['', '## Verified good', '']
    for v in out['verified_good']:
        lines.append(f"- {v['title']} · key: `{v['key']}`")
    for d in out['checked']:
        lines.append(f"- **Checked by a person on {d['date']}:** key: `{d['key']}` · reason: \"{d['reason']}\"")
    for d in out['suppressed']:
        lines.append(f"- **Declined on {d['date']}:** key: `{d['key']}` · reason: \"{d['reason']}\"")
    lines += ['', '## Considered and dropped', '']
    for d in out['dropped']:
        lines.append(f"- {d['title']} — {d['reason']}")
    return '\n'.join(lines).rstrip('\n') + '\n'


def print_for_coverage(out):
    """What the run copies into its own sections, Coverage and Fetch log."""
    if out['quality']['config_gaps']:
        print('\n(for Coverage) config.json needs update — run --init:')
        for g in out['quality']['config_gaps']:
            print('- ' + g)
    if out['quality']['dependencies_not_checked']:
        print('\n(for Coverage) dependencies whose versions were not checked:')
        for line in out['quality']['dependencies_not_checked']:
            print('- ' + line)
    if out['quality']['failed_calls']:
        print('\n(for Fetch log) calls releases.py could not make:')
        for line in out['quality']['failed_calls']:
            print('- ' + line)
    r = out['quality'].get('resolved_versions') or {}
    if r.get('status') == 'ok' and r.get('lines'):
        print(f"\n(for Coverage) the release build ships another version than the build asks for "
              f"({', '.join(r.get('configurations') or [])}):")
        for line in r['lines']:
            print('- ' + line)
    elif r.get('status') in ('failed', 'timeout'):
        print(f"\n(for Coverage) shipped versions not resolved: {r['status']}"
              + (f" — {r['reason']}" if r.get('reason') else '') + '; the run used the versions the build asks for')
    elif r.get('status') == 'off':
        print(f"\n(for Coverage) shipped versions not resolved: off ({r.get('source')}) — a version raised by "
              'a BOM or by another library is not seen')
    print('\n(for Coverage) ' + facts_changes_line(out))
    for line in out['quality']['facts_problems']:
        print(f'- {line}')
    if out['quality']['inputs_read']:
        print(f"(for Coverage) Read in full: {len(out['quality']['inputs_read'])} files")


def facts_changes_line(out):
    changes = out['quality']['facts_changes']
    if changes is None:
        return 'Build facts: first recorded by this run — every key file was read in full'
    return f"Build facts since the previous run: {'; '.join(changes) or 'no change'}"


def history_lookup(root, key, newest_path):
    """Finds a finding by key in earlier slices, newest first; slices before schema 5 did not
    keep a hidden finding inside the `checked` / `suppressed` entry."""
    import glob
    for path in sorted(glob.glob(os.path.join(root, 'upstream-audit', 'runs', '*.json')), reverse=True):
        if newest_path and os.path.abspath(path) == os.path.abspath(newest_path):
            continue
        for f in common.load_slice(path).get('findings', []):
            if f.get('key') == key:
                f.setdefault('status', 'unchanged')
                return f
    return None


def relocate_anchors(root, f, errors):
    """Moves each anchor of a finding to the line its excerpt is on now; an excerpt the file no
    longer contains, or a file that is gone, is an error (rule 3: no anchor, no report)."""
    for a in f['anchors']:
        status, hits = common.locate(root, a)
        if not hits:
            errors.append(f'{f["key"]}: anchor {status} — {a["file"]} does not contain {a["excerpt"]!r}; '
                          'close the finding or give it an anchor the file contains')
        else:
            a['line'] = common.nearest(hits, a['line'])


def git(root, *args):
    r = subprocess.run(['git', '-C', root, *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--started', required=True, help='run start, UTC, e.g. 2026-10-08T12:55:10Z')
    p.add_argument('--model', required=True)
    p.add_argument('--edits', required=True)
    p.add_argument('--slice', help='previous slice (default: newest)')
    p.add_argument('--scratch', required=True,
                   help='the --out directory of feeds.py and pages.py: feed gaps and saved pages')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--show-report', action='store_true',
                   help='also print the report text it writes (the tests read it; a run does not need it)')
    args = p.parse_args()

    root = common.repo_root()
    stamp = args.started[:16].replace('T', '-').replace(':', '')
    report = stamp + '.md'
    run_date = args.started[:10]
    edits = common.load_json(args.edits, 'the edits file')
    prev_path = args.slice or common.newest_slice(root)
    prev = common.load_slice(prev_path) if prev_path else None
    errors, notes = [], []

    new_list = edits.get('new', []) if isinstance(edits, dict) else []
    # Only text keys: a missing or malformed one is reported by the edits check below, not a crash here.
    new_keys = ([n['key'] for n in new_list if isinstance(n, dict) and isinstance(n.get('key'), str)]
                if isinstance(new_list, list) else [])
    decided, unparseable = decisions_mod.evaluate(root, prev, new_keys)
    hidden = {d['key']: d for d in decided if d['state'] == 'applied'}
    expired = {d['key']: d for d in decided if d['state'] == 'expired'}
    unknown = [d for d in decided if d['state'] == 'unknown_key']

    # The edits are checked before anything uses them: a typo must stop the run, not vanish.
    parked_keys = {item['key'] for section in ('checked', 'suppressed')
                   for item in (prev or {}).get(section, []) or [] if isinstance(item, dict) and item.get('key')}
    problems = edits_mod.check(edits, {f['key'] for f in (prev or {}).get('findings', [])} | parked_keys,
                               set(hidden) | parked_keys,
                               {v.get('key') for v in (prev or {}).get('verified_good', []) or []})
    if problems:
        print(f'NOT WRITTEN — the edits file {args.edits} has problems:')
        for e in problems:
            print('  - ' + e)
        return 1

    # Every quote this run adds must be on the page pages.py saved; a source the run could only
    # read through WebFetch says so in "unverified" and is counted below, with the carried ones.
    this_run_sources = [s for change in edits.get('findings', {}).values()
                        for s in change.get('sources', []) + change.get('sources_add', [])]
    this_run_sources += [s for n in edits.get('new', []) for s in n.get('sources', [])]
    for s in this_run_sources:
        if s.get('unverified'):
            continue
        text = pages_mod.page_text(args.scratch, s['url'])
        if text is None:
            errors.append(f"source {s['url']}: not saved by pages.py this run — run "
                          f"`pages.py get --out {args.scratch} {s['url']}`, "
                          'or mark the source "unverified" with the reason')
        elif not pages_mod.quote_found(s['claim'], text):
            errors.append(f"source {s['url']}: the claim is not on the saved page — copy it from "
                          f"`pages.py grep`: {s['claim'][:120]!r}")

    closed = {c['key']: c for c in edits.get('closed', [])}
    overrides = edits.get('findings', {})
    verified = edits.get('verified', [])
    carried = set(edits.get('carried', []))

    findings = []
    prev_findings = prev['findings'] if prev else []
    fallback = prev['run']['started'][:10] if prev else run_date
    reopen = set(edits.get('reopen', []))
    # Kept in the slice: a reopen must outlive this run, or the next run hides the finding again.
    reopened = {d['key']: d['date'] for d in decided if d['state'] == 'reopened'}
    for key in reopen:
        if key in hidden:
            reopened[key] = hidden.pop(key)['date']

    # Findings hidden by a decision are kept whole inside the slice, so that they can come back
    # when the decision expires, is reopened, or its line is removed from decisions.txt.
    parked = {}
    for section in ('checked', 'suppressed'):
        for item in (prev or {}).get(section, []) or []:
            if isinstance(item, dict) and item.get('key'):
                parked[item['key']] = item.get('finding') or history_lookup(root, item['key'], prev_path)
    active_keys = {f['key'] for f in prev_findings}
    pool = list(prev_findings) + [f for k, f in parked.items() if f and k not in active_keys]
    for key, f in parked.items():
        if f is None and key not in hidden:
            errors.append(f'{key}: its decision no longer hides it, but no earlier slice holds the finding; '
                          'pass it under "new" with this key')

    pool_by_key = {f['key']: f for f in pool}
    # `done` in decisions.txt: a person did what the finding asked, outside the code. It closes
    # the finding for good, as a run closes one whose code changed.
    for key, d in list(hidden.items()):
        if d['status'] == 'done' and key in pool_by_key:
            closed.setdefault(key, {'key': key, 'reason': f"done {d['date']} (decisions.txt): {d['reason']}"})
            hidden.pop(key)
    hidden_findings = {}
    for f in pool:
        key = f['key']
        if key in closed:
            continue
        if key in hidden:
            hidden_findings[key] = f
            continue
        returning = key not in active_keys
        f = dict(f)
        if returning:
            notes.append(f'{key}: was hidden by a decision, now reported again')
        f.setdefault('last_verified', fallback)
        f['status'] = 'unchanged'
        edit = dict(overrides.get(key, {}))
        f['sources'] = f.get('sources', []) + edit.pop('sources_add', [])
        extra_anchors = edit.pop('anchors_add', [])
        f.update(edit)
        f['anchors'] = [dict(a) for a in f['anchors']] + extra_anchors
        relocate_anchors(root, f, errors)
        if returning and f['status'] == 'unchanged':
            f['status'] = 'changed'
        if key in expired:
            notes.append(f'{key}: checked decision of {expired[key]["date"]} expired — report it as re-check')
        if verified == 'all' or key in verified:
            f['last_verified'] = run_date
        # Every report holds each finding in full; slices before that named the report that did.
        f.pop('detailed_in', None)
        findings.append(f)

    hidden_new = []
    for n in edits.get('new', []):
        n = dict(n)
        n.setdefault('status', 'new')
        n.setdefault('last_verified', run_date)
        n.setdefault('deadline', None)
        n.setdefault('api_stability', None)
        # Hidden or not, a new finding is stored whole, so its anchors must exist like any other.
        n['anchors'] = [dict(a) for a in n.get('anchors', [])]
        relocate_anchors(root, n, errors)
        if n['key'] in hidden:
            # A person already decided on this key, e.g. on a first run with decisions.txt kept.
            hidden_findings[n['key']] = n
            hidden_new.append(n)
            continue
        if n['key'] in expired:
            notes.append(f"{n['key']}: checked decision of {expired[n['key']]['date']} expired — report it as re-check")
        findings.append(n)

    seen = set()
    # A hidden new finding is checked too: it is stored whole and returns when its decision ends.
    for f in findings + hidden_new:
        key = f.get('key', '<no key>')
        is_hidden = any(f is h for h in hidden_new)
        if key in seen:
            errors.append(f'{key}: duplicate key')
        seen.add(key)
        for field in REQUIRED:
            if f.get(field) in (None, '', []) and field not in ('sources',):
                errors.append(f'{key}: missing {field}')
        if f.get('status') not in STATUSES:
            errors.append(f'{key}: status must be one of {STATUSES}')
        areas = f.get('areas')
        if not areas or not isinstance(areas, list):
            errors.append(f'{key}: no areas — set "areas" for it under "findings" in the edits (one or more of {AREAS})')
        elif [a for a in areas if a not in AREAS]:
            errors.append(f'{key}: unknown area {[a for a in areas if a not in AREAS]} — allowed: {AREAS}')
        if f.get('section') not in [s for s, _ in SECTIONS]:
            errors.append(f'{key}: no report section — set "section" for it under "findings" in the edits '
                          f'(one of {[s for s, _ in SECTIONS]})')
        if f.get('manual_check') and not f.get('what_to_check'):
            errors.append(f'{key}: a manual check with no "what_to_check" — set it for the key under '
                          f'"findings" in the edits: one sentence on where a person looks and what settles it')
        if not f.get('sources') and f.get('status') != 'unchanged':
            errors.append(f'{key}: a new or changed finding needs a source')
        if not f.get('recommendation') and not is_hidden:
            errors.append(f'{key}: needs a "recommendation" — what to do, with cost against benefit '
                          '(rule 7); the report prints every finding in full, so a carried finding '
                          'kept without one is re-verified and given one under "findings"')
        age = common.age_days(f['last_verified'], common.datetime.date.fromisoformat(run_date))
        if age > common.MAX_AGE_DAYS:
            errors.append(f'{key}: last verified {age} days ago — over {common.MAX_AGE_DAYS}, must be re-verified in full')
        elif age > common.FRESH_DAYS and key not in carried:
            errors.append(f'{key}: last verified {age} days ago — re-verify it and list it under "verified", '
                          'or list it under "carried" and name it in Coverage')
    # A finding hidden by a decision is still a finding of the previous slice and can be closed.
    for key in closed:
        if not any(f['key'] == key for f in pool):
            errors.append(f'{key}: closed, but it is not a finding of the previous slice')

    config = common.load_config(root)
    base = config['base_branch']
    behind = git(root, 'rev-list', '--count', f'HEAD..origin/{base}')
    quality = {'sources_ok': 0, 'sources_failed': 0, 'failed_urls': [], 'coverage_gaps': []}
    quality.update(edits.get('quality', {}))
    # Taken from the scripts' own output, never from the edits: the run cannot drop or reword them.
    feeds_path = os.path.join(args.scratch, 'feeds.json')
    if os.path.exists(feeds_path):
        feeds = common.load_json(feeds_path, 'feeds.json')
        quality['failed_feeds'] = feeds.get('failed', [])
        quality['feed_gaps'] = feeds.get('gaps', [])
        quality['stale_feeds'] = feeds.get('stale', [])
    else:
        errors.append(f'{feeds_path} not found — run `feeds.py fetch --out {args.scratch}` this run')
        quality['failed_feeds'], quality['feed_gaps'], quality['stale_feeds'] = [], [], []
    releases_path = os.path.join(args.scratch, 'releases.json')
    if os.path.exists(releases_path):
        releases_out = common.load_json(releases_path, 'releases.json')
        quality['github_client'] = releases_out.get('github_client')
        quality['dependencies_not_checked'] = releases_out.get('not_checked', [])
        quality['failed_calls'] = releases_out.get('failed_calls', [])
    else:
        errors.append(f'{releases_path} not found — run `releases.py --out {args.scratch}` this run')
        quality['github_client'], quality['dependencies_not_checked'] = None, []
        quality['failed_calls'] = []
    resolved_path = os.path.join(args.scratch, 'resolved.json')
    if os.path.exists(resolved_path):
        resolved = common.load_json(resolved_path, 'resolved.json')
        quality['resolved_versions'] = {k: resolved.get(k) for k in
                                        ('status', 'source', 'seconds', 'configurations', 'lines', 'reason')
                                        if resolved.get(k) is not None}
    else:
        errors.append(f'{resolved_path} not found — run `resolved.py --out {args.scratch}` this run '
                      '(it only writes "off" when the setting is off)')
    facts_path = os.path.join(args.scratch, 'facts.json')
    if os.path.exists(facts_path):
        build_facts = common.load_json(facts_path, 'facts.json')
    else:
        errors.append(f'{facts_path} not found — run `facts.py --out {args.scratch}` this run')
        build_facts = None
    snapshot = facts_mod.snapshot(build_facts) if build_facts else None
    quality['config_gaps'] = (releases_mod.config_gaps(root, config, [v['path'] for v in build_facts['vendored']])
                              if build_facts else [])
    quality['facts_changes'] = facts_mod.changes((prev or {}).get('facts'), snapshot) if snapshot else None
    quality['facts_problems'] = (build_facts['version_warnings'] + build_facts['problems']) if build_facts else []
    # With no facts to compare with, every key file is read in full; a skipped one must stop the
    # run, not look like a file that was read and had nothing in it.
    read = edits.get('inputs_read', [])
    if build_facts and quality['facts_changes'] is None:
        missing = [p for p in build_facts['key_files'] if p not in read]
        if missing:
            errors.append(f'first run on build facts: not read in full: {", ".join(missing)} — read each one '
                          'and list it under "inputs_read" in the edits file')
    quality['inputs_read'] = sorted(set(read))
    quality['unknown_decision_keys'] = [d['key'] for d in unknown]
    quality['unparseable_decisions'] = unparseable
    # Counted over every active finding, carried ones too: a quote stays unverified until a run
    # replaces or removes its source, and the warning must last as long.
    quality['unverified_claims'] = [{'key': f['key'], 'url': s['url'], 'why': s['unverified']}
                                    for f in findings for s in f.get('sources', []) if s.get('unverified')]
    quality['hidden_new_findings'] = [n['key'] for n in hidden_new]
    dropped_good = set(edits.get('verified_good_drop', []))
    verified_good = {v['key']: v for v in (prev or {}).get('verified_good', []) or []
                     if v['key'] not in dropped_good}
    verified_good.update({v['key']: v for v in edits.get('verified_good', [])})
    out = {
        'schema': common.SLICE_SCHEMA, 'audit': 'upstream',
        'run': {
            'started': args.started, 'model': args.model, 'skill_version': common.skill_version(),
            'prior_run': ('runs/' + os.path.basename(prev_path)) if prev_path else None,
            'audited_commit': git(root, 'rev-parse', 'HEAD'),
            'base_branch': base,
            'base_commit': git(root, 'merge-base', 'HEAD', f'origin/{base}'),
            'commits_behind_base': int(behind) if behind and behind.isdigit() else None,
            'window': window(prev, run_date),
        },
        'quality': quality,
        'facts': snapshot,
        'findings': findings,
        'dropped': edits.get('dropped', []),
        'verified_good': list(verified_good.values()),
        'closed': [dict(c, **{k: pool_by_key[c['key']][k] for k in ('subject', 'title')
                              if c['key'] in pool_by_key})
                   for c in closed.values()],
        'checked': [{'key': d['key'], 'date': d['date'], 'reason': d['reason'],
                     'finding': hidden_findings.get(d['key'])}
                    for d in hidden.values() if d['status'] == 'checked' and d['key'] not in closed],
        'suppressed': [{'key': d['key'], 'date': d['date'], 'reason': d['reason'],
                        'finding': hidden_findings.get(d['key'])}
                       for d in hidden.values() if d['status'] == 'declined' and d['key'] not in closed],
        'reopened': [{'key': k, 'date': date} for k, date in sorted(reopened.items())],
    }

    if errors:
        print('NOT WRITTEN — fix these first:')
        for e in errors:
            print('  - ' + e)
        return 1

    counts = {s: sum(f['status'] == s for f in findings) for s in STATUSES}
    was = sum(1 for pf in prev_findings if pf['key'] not in hidden)
    for f in findings:
        anchors = ', '.join(f"{a['file'].split('/')[-1]}:{a['line']}" for a in f['anchors'])
        print(f"{f['status'][:3]}  {f['subject'][:38]:38}  {','.join(f['areas']):18}  {f['severity']:6}  {anchors}")
    for n in notes:
        print('note: ' + n)
    for d in unknown:
        print(f"unknown decision key: {d['key']} (closest: {d.get('closest') or 'none'}) — report it in Coverage")
    for line in unparseable:
        print(f'unparseable decision line: {line} — quote it in Coverage')
    print(f"\nstatus: {counts}")
    if prev:
        print(f"trend line: Active {len(findings)} (was {was}) · new {counts['new']} · closed {len(closed)}"
              f" · checked {len(out['checked'])} · declined {len(out['suppressed'])}")
    else:
        print('trend line: first run')
    print(f'checked block:\n{checked(out, digest=True)}')
    print(f'still-open block:\n{still_open_block(findings)}')
    print(f'shown line: Shown <X> of {len(findings)} · full report: upstream-audit/{report}')
    print(f'upkeep line: {upkeep_line(out)}')
    print(f"audited commit {out['run']['audited_commit']}, base {base} at {out['run']['base_commit']}, "
          f"behind {base}: {out['run']['commits_behind_base']}")
    print_for_coverage(out)
    target = os.path.join(root, 'upstream-audit', 'runs', stamp + '.json')
    report_path = os.path.join(root, 'upstream-audit', report)
    text = report_text(out, common.datetime.date.fromisoformat(run_date))
    if args.show_report:
        print('\n===== report =====\n' + text + '===== end of report =====')
    if args.dry_run:
        print(f'dry run — would write {target} and {report_path} '
              f"({len(findings)} findings in full, {len(text.splitlines())} lines)")
        return 0
    for path in (target, report_path):
        if os.path.exists(path):
            print(f'NOT WRITTEN — {path} already exists; a run never overwrites an earlier slice or report')
            return 1
    # Git keeps no empty directory: on a first run upstream-audit/runs/ does not exist yet.
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write('\n')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(text)
    print(f'written {target}')
    print(f'written {report_path} up to "Considered and dropped" — add Fetch log, Coverage and the '
          'digest below it; do not retype what it holds')
    return 0


if __name__ == '__main__':
    sys.exit(main())
