#!/usr/bin/env python3
"""Fetches the four feeds, lists what falls in the window, and reports feed gaps.

    python3 feeds.py fetch --out DIR [--since ISO]

`fetch` saves each feed's raw XML into DIR, then prints per feed: entry count, oldest and
newest entry, whether it reaches back to the window start (a gap otherwise), whether it has
gone silent (STALE FEED: no entry for longer than that feed usually goes quiet — it may have
moved), and every entry inside the window. `--since` defaults to `run.started` of the newest
run slice. Gaps and stale feeds are also written to DIR/feeds.json, where slice.py reads them.

Feed entries carry titles and links, not full posts: quote a post through `pages.py grep`.
"""
import argparse
import datetime
import email.utils
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

import common

ATOM = '{http://www.w3.org/2005/Atom}'
# (name, title, url, format, days without an entry after which the feed counts as silent).
# The Android Developers blog moved off Blogger on 2026-10-15; the old feed stopped there.
FEEDS = [
    ('adb', 'Android Developers blog', 'https://developer.android.com/static/blog/atom.xml', 'atom', 30),
    ('gblog', 'Google blog, Android', 'https://blog.google/products/android/rss/', 'rss', 60),
    ('kblog', 'JetBrains Kotlin blog', 'https://blog.jetbrains.com/kotlin/feed/', 'rss', 30),
    ('androidx', 'AndroidX release notes', 'https://developer.android.com/feeds/androidx-release-notes.xml', 'atom', 30),
]
PRE_RELEASE = re.compile(r'-(alpha|beta|rc|dev|M)\w*', re.I)


def download(url, path):
    common.curl(url, path)
    return os.path.getsize(path)


def parse(path, kind, date_key='published'):
    """Returns [(utc 'YYYY-MM-DDTHH:MM', title, link, body)]."""
    root = ET.parse(path).getroot()
    out = []
    if kind == 'atom':
        for e in root.findall(ATOM + 'entry'):
            when = (e.findtext(ATOM + date_key) or e.findtext(ATOM + 'updated') or '')
            dt = datetime.datetime.fromisoformat(when.replace('Z', '+00:00')).astimezone(datetime.timezone.utc)
            links = [x.get('href') for x in e.findall(ATOM + 'link') if x.get('rel') in ('alternate', None)]
            out.append((dt.strftime('%Y-%m-%dT%H:%M'), (e.findtext(ATOM + 'title') or '').strip(),
                        links[0] if links else '', e.findtext(ATOM + 'content') or ''))
    else:
        for i in root.iter('item'):
            dt = email.utils.parsedate_to_datetime(i.findtext('pubDate')).astimezone(datetime.timezone.utc)
            out.append((dt.strftime('%Y-%m-%dT%H:%M'), (i.findtext('title') or '').strip(),
                        i.findtext('link') or '', i.findtext('description') or ''))
    return out


def cmd_fetch(args):
    since = args.since
    if not since:
        prev = common.newest_slice(common.repo_root())
        if prev:
            since = common.load_slice(prev)['run']['started']
        else:
            since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=common.FIRST_RUN_DAYS)).isoformat()
    since = since.replace('Z', '')[:16]
    os.makedirs(args.out, exist_ok=True)
    print(f'window start: {since}Z')
    ok, failed = 0, []
    gaps, stale = [], []
    now = datetime.datetime.now(datetime.timezone.utc).date()
    for name, title, url, kind, silent_days in FEEDS:
        path = os.path.join(args.out, name + '.xml')
        try:
            size = download(url, path)
            entries = parse(path, kind, 'updated' if name == 'androidx' else 'published')
            if not entries:
                raise RuntimeError('the feed has no entries')
        except Exception as e:  # a failed feed is a fetch-log entry, not a crash
            failed.append(title)
            print(f'\n## {title}: FAILED — {e}\n   {url}')
            continue
        ok += 1
        oldest = min(e[0] for e in entries)
        newest = max(e[0] for e in entries)
        print(f'\n## {title}: ok, {len(entries)} entries, {size} bytes, {oldest[:10]} .. {newest}Z')
        quiet = (now - datetime.date.fromisoformat(newest[:10])).days
        if quiet > silent_days:
            # A feed that stops updating still parses; only its age shows it has moved or died.
            stale.append({'feed': title, 'newest': newest[:10], 'days': quiet, 'url': url})
            print(f'   STALE FEED: newest entry {newest[:10]}, {quiet} days ago (limit {silent_days}) — '
                  'the feed may have moved; find its new address')
        if oldest > since:
            days = (datetime.date.fromisoformat(oldest[:10]) - datetime.date.fromisoformat(since[:10])).days
            gaps.append((title, since[:10], oldest[:10], days))
            print(f'   GAP: {since[:10]} -> {oldest[:10]} not covered ({days} days)')
        inside = sorted((e for e in entries if e[0] >= since), reverse=True)
        if not inside:
            print('   nothing in the window')
        for when, etitle, link, body in inside:
            if name == 'androidx':
                names = [html.unescape(t).strip() for t in re.findall(r'<a href="[^"]+"[^>]*>([^<]+)</a>', body)]
                compose_stable = [n for n in names if n.lower().startswith('compose ')
                                  and not PRE_RELEASE.search(n)
                                  and re.match(r'Compose (Animation|Foundation|Material3?|Runtime|Ui) Version', n)]
                print(f'   {when[:10]} | {"; ".join(names)}')
                if compose_stable:
                    print(f'      STABLE COMPOSE RELEASE in window: {"; ".join(compose_stable)}'
                          ' -> read all five Compose release-note pages')
            else:
                print(f'   {when[:10]} | {etitle} | {link}')
    print('\nFeed gaps: ' + ('none' if not gaps else ''))
    for title, start, end, days in gaps:
        print(f'  {title}: {start} -> {end} not covered ({days} days)')
    print('Stale feeds: ' + ('none' if not stale else ''))
    for s in stale:
        print(f"  {s['feed']}: newest entry {s['newest']} ({s['days']} days) — {s['url']}")
    print(f'fetch log: {ok} feeds ok, {len(failed)} failed')
    # slice.py reads failed feeds, gaps and stale feeds from here, so they never pass through
    # the run's hands.
    with open(os.path.join(args.out, 'feeds.json'), 'w', encoding='utf-8') as f:
        json.dump({'window_start': since + 'Z', 'feeds_ok': ok, 'feeds_failed': len(failed),
                   'failed': failed,
                   'gaps': [{'feed': t, 'from': s, 'to': e, 'days': d} for t, s, e, d in gaps],
                   'stale': stale}, f, indent=1)
    return 0


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    f = sub.add_parser('fetch')
    f.add_argument('--out', required=True)
    f.add_argument('--since')
    args = p.parse_args()
    return cmd_fetch(args)


if __name__ == '__main__':
    sys.exit(main())
