#!/usr/bin/env python3
"""Reads release data through APIs: deps.dev, Maven metadata, GitHub releases and commits.

    python3 releases.py --out SCRATCH [--since ISO]
    python3 releases.py --body OWNER/REPO@TAG

1. Every library and plugin of every version catalog, and every dependency Gradle says the
   build declares outside them — both from facts.json, which facts.py writes into the same
   scratch directory first — through deps.dev: our version and the newest stable and newest
   overall, with their publication dates. A package deps.dev does not know falls back to
   maven-metadata.xml, which has versions but no dates. A version with a suffix is compared
   with the newer builds of the same variant. An artifact that stopped is named: a `-ktx`
   artifact its base artifact moved on without (MOVED ON WITHOUT IT), and any artifact with
   no build for two years (NO BUILD FOR TWO YEARS, NEW when it crossed that age this window).
2. GitHub releases (never the rendered page — its dates are wrong) of the toolchain the
   project uses, of every pin with a suffix or a pre-release (its repository from deps.dev),
   and of the repositories under "releases" in config.json.
3. The head commit of every repository under "commits" in config.json and of every vendored
   artifact's upstream: projects that do not tag, and the upstream of forks.
4. config.json against the project: a vendored artifact it does not name
   (UNCONFIGURED VENDORED), and an entry for a path the build no longer uses (STALE CONFIG).

GitHub is asked through `gh` when it is installed, otherwise through curl, unauthenticated
(60 requests an hour). `--out` writes releases.json for slice.py.
`--body` prints the notes of one release. Anything published on or after `--since`
(default: the previous run's start) is marked NEW.
"""
import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse

import common

# A stage only as a whole token, so "sources", "force", "devtools" or "arm64" are not one;
# a milestone is "m" with digits right after it (M3).
PRE_RELEASE = re.compile(r'(?:^|(?<=[-._\d]))(alpha|beta|rc|dev|snapshot|eap|m(?=\d))(?=\d|[-._]|$)', re.I)
# Order of pre-release stages within one release number; a milestone (M1, M2) ranks with alpha.
STAGE_RANK = {'dev': 0, 'snapshot': 0, 'eap': 0, 'alpha': 1, 'beta': 2, 'rc': 3}
STABLE_RANK = 4
DEPS_DEV = 'https://api.deps.dev/v3/systems/maven/packages/'
# Watched whenever the project uses them: they decide what every other upgrade needs.
TOOLCHAIN = {
    'JetBrains/kotlin': ('org.jetbrains.kotlin',),
    'google/ksp': ('com.google.devtools.ksp',),
    'Kotlin/kotlinx.coroutines': ('org.jetbrains.kotlinx:kotlinx-coroutines',),
    'Kotlin/kotlinx.serialization': ('org.jetbrains.kotlinx:kotlinx-serialization',),
}
FALLBACK_REPOS = ['https://repo1.maven.org/maven2', 'https://dl.google.com/dl/android/maven2',
                  'https://plugins.gradle.org/m2']
DORMANT_DAYS = 730


def github_client():
    return 'gh' if shutil.which('gh') else 'curl'


def github(path, client):
    if client == 'gh':
        r = subprocess.run(['gh', 'api', path], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip().split('\n')[0])
        return json.loads(r.stdout)
    headers = ['Accept: application/vnd.github+json']
    return json.loads(common.curl('https://api.github.com/' + path, headers=headers))


def http(url):
    return common.curl(url)


def load_facts(scratch):
    """facts.json that facts.py wrote into the same scratch directory this run."""
    path = os.path.join(scratch, 'facts.json')
    if not os.path.isfile(path):
        raise SystemExit(f'{path} not found — run `facts.py --out {scratch}` first: the dependencies come from Gradle')
    return common.load_json(path, 'facts.json')


def locate_alias(root, files, section, alias):
    """`path:line` of a catalog entry; Gradle normalizes `-` and `_` in an alias to `.`."""
    pattern = re.compile(r'^\s*' + r'[-_.]'.join(re.escape(part) for part in alias.split('.')) + r'\s*=')
    for path in files:
        current = None
        for i, line in enumerate(common.read_lines(root, path) or [], 1):
            header = re.match(r'^\s*\[([\w-]+)\]', line)
            if header:
                current = header.group(1)
            elif current == section and pattern.match(line):
                return f'{path}:{i}'
    return None


def locate_text(root, files, text, also=None):
    """`path:line` of the first line in `files` that contains `text` (and `also`, when given)."""
    for path in files:
        for i, line in enumerate(common.read_lines(root, path) or [], 1):
            if text in line and (also is None or also in line):
                return f'{path}:{i}'
    return None


def plugin_id(module):
    """The plugin id of a plugin marker, `<id>:<id>.gradle.plugin`; None for a library."""
    group, _, name = module.partition(':')
    return group if name == f'{group}.gradle.plugin' else None


def facts_entries(root, facts):
    """Every library and plugin of the catalogs and every dependency Gradle says the build
    declares outside them, each with the place a person would change it."""
    catalog_files = facts.get('catalog_files') or []
    entries, in_catalog = [], set()
    for cat in facts.get('catalogs') or []:
        for lib in cat['libraries']:
            in_catalog.add((lib['module'], lib['version']))
            entries.append({'kind': 'library', 'coord': lib['module'], 'version': lib['version'],
                            'where': locate_alias(root, catalog_files, 'libraries', lib['alias'])
                            or f"catalog {cat['name']}, {lib['alias']}"})
        for plugin in cat['plugins']:
            in_catalog.add((f"{plugin['id']}:{plugin['id']}.gradle.plugin", plugin['version']))
            entries.append({'kind': 'plugin', 'coord': f"{plugin['id']}:{plugin['id']}.gradle.plugin",
                            'version': plugin['version'],
                            'where': locate_alias(root, catalog_files, 'plugins', plugin['alias'])
                            or f"catalog {cat['name']}, plugins {plugin['alias']}"})
    for d in facts.get('dependencies') or []:
        # A build file that writes the coordinate itself pins it there, apart from the catalog:
        # "com.android.tools.build:gradle:9.2.1" in an included build is a pin of its own. A plugin
        # marker comes from a plugins block: `id 'com.gradle.develocity' version '4.5.0'`.
        pid = plugin_id(d['module'])
        literal = [w for w in (locate_text(root, [f], pid, 'version') if pid else locate_text(root, [f], d['module'])
                               for f in d.get('files') or []) if w]
        if not literal and (d['module'], d['version']) in in_catalog:
            continue
        for where in literal or [f"declared in {', '.join(d['examples'])}"]:
            entries.append({'kind': 'plugin' if pid else 'library', 'coord': d['module'],
                            'version': d['version'], 'where': where})
    return entries


def is_pre_release(version):
    return '-' in version and bool(PRE_RELEASE.search(version.split('-', 1)[1]))


def is_variant(version):
    return '-' in version and not is_pre_release(version)


def release_key(version):
    """Numeric release part, then the pre-release stage and its number, a stable release
    last: 1.0.0-alpha01 < 1.0.0-alpha02 < 1.0.0-beta01 < 1.0.0-rc01 < 1.0.0."""
    m = re.match(r'(\d+(?:\.\d+)*)', version)
    numbers = tuple(int(n) for n in m.group(1).split('.')) if m else ()
    if not is_pre_release(version):
        return numbers, STABLE_RANK, 0
    suffix = version.split('-', 1)[1]
    stage = PRE_RELEASE.search(suffix)
    build = re.search(r'\d+', suffix[stage.start():])
    return numbers, STAGE_RANK.get(stage.group(1).lower(), 1), int(build.group()) if build else 0


def same_variant(version):
    # "-fork-1" must also match "-fork-2": a trailing build number is not part of the variant
    suffix = version[version.index('-'):]
    return re.compile('.*' + re.sub(r'\d+$', r'\\d+', re.escape(suffix)) + '$')


def variant_key(version):
    """Release number, then the trailing build number: 3.1.0-fork-2 is newer than 3.1.0-fork-1."""
    build = re.search(r'(\d+)$', version)
    return release_key(version)[0], int(build.group(1)) if build else 0


def deps_dev(coord):
    """Published versions in publication order as [(version, 'YYYY-MM-DD')], or None when
    deps.dev does not know the package."""
    try:
        data = json.loads(http(DEPS_DEV + urllib.parse.quote(coord, safe='')))
    except RuntimeError as e:
        if '404' in str(e):
            return None
        raise
    return sorted(((v['versionKey']['version'], (v.get('publishedAt') or '')[:10])
                   for v in data.get('versions', [])), key=lambda v: v[1])


def deps_dev_repo(coord, version):
    """The GitHub owner/repo deps.dev links to a version, or None when it links none or does
    not know the version. Any other error is raised: a failed call is not a missing link."""
    url = f"{DEPS_DEV}{urllib.parse.quote(coord, safe='')}/versions/{urllib.parse.quote(version, safe='')}"
    try:
        links = json.loads(http(url)).get('links', [])
    except RuntimeError as e:
        if '404' in str(e):
            return None
        raise
    for link in links:
        m = re.match(r'https?://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?$', link.get('url', ''))
        if m and link.get('label') == 'SOURCE_REPO':
            return m.group(1)
    return None


def pin_repositories(pins, published):
    """The source repository of every pin, as (linked [(entry, repo or None)], calls_ok,
    failures); a pin whose call failed is in failures only."""
    def ask(e):
        version = (published.get(e['coord']) or [(e['version'], '')])[-1][0]
        try:
            return e, deps_dev_repo(e['coord'], version), None
        except Exception as err:
            return e, None, f"deps.dev links {e['coord']}: {err}"
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        answers = list(pool.map(ask, pins))
    linked = [(e, repo) for e, repo, failure in answers if not failure]
    return linked, len(linked), [failure for _, _, failure in answers if failure]


def maven_metadata(coord, repos):
    """Published versions from the first repository that has the artifact, without dates, as
    (versions, repository, errors). A 404 means the repository does not have it; any other
    error is kept, so a repository that could not be read is not taken for one without it."""
    group, name = coord.split(':')[:2]
    errors = []
    for base in repos:
        try:
            xml = http(f"{base}/{group.replace('.', '/')}/{name}/maven-metadata.xml")
        except RuntimeError as e:
            if '404' not in str(e):
                errors.append(f'{base}: {e}')
            continue
        return [(v, '') for v in re.findall(r'<version>([^<]+)</version>', xml)], base, errors
    return None, None, errors


def fetch_versions(coords, repos, private):
    """Versions of every coordinate: deps.dev first, then maven-metadata.xml of the public
    repositories. Returns (published, sources, calls_ok, failures); a failure names the call."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {coord: pool.submit(deps_dev, coord) for coord in coords}
    published, sources, ok, failures = {}, {}, 0, []
    for coord, future in futures.items():
        try:
            published[coord] = future.result()
            ok += 1
            if published[coord] is not None:
                sources[coord] = 'deps.dev'
        except Exception as e:
            published[coord] = None
            sources[coord] = f'FAILED — {e}'
            failures.append(f'deps.dev {coord}: {e}')
    for coord in coords:
        if published[coord] is None and not sources.get(coord, '').startswith('FAILED'):
            versions_found, base, errors = maven_metadata(coord, repos)
            if versions_found:
                ok += 1
                published[coord], sources[coord] = versions_found, f'maven-metadata.xml at {base}'
            else:
                if errors:
                    sources[coord] = ('not on deps.dev; could not read maven-metadata.xml from '
                                      + '; '.join(errors) + ' — run again later')
                else:
                    sources[coord] = 'not on deps.dev nor in the repositories without credentials'
                sources[coord] += f" — not asked, need credentials: {', '.join(private)}" if private else ''
                failures.append(f'maven-metadata.xml {coord}: {sources[coord]}')
    return published, sources, ok, failures


def stopped_artifacts(coords, published, today, since):
    """Artifacts that have stopped, as (moved, dormant). `moved`: a `-ktx` artifact whose base
    artifact kept releasing builds it does not have — the base absorbed it. `dormant`: any other
    artifact whose newest version is older than DORMANT_DAYS, marked NEW when it crossed that
    age since `since`, so a run looks only at the newly stopped ones. `published` holds the base
    artifacts too."""
    moved, dormant = [], []
    for coord in coords:
        dated = [(v, d) for v, d in published.get(coord) or [] if d]
        if not dated:
            continue
        # Dormant means nothing was published for two years, so the date that counts is the
        # newest build of any kind: leakcanary's stable line ends at 2.14 (2024) while its
        # 3.0 alphas go on. The version shown is the newest by number, stable when there is one
        # (protoc's 21.0-rc-1 from 2022 outnumbers its 4.x line). deps.dev dates a few old builds
        # by when it indexed them; such a build can hide a dormant artifact, which is the
        # quieter mistake.
        newest_build = max(d for _, d in dated)
        stable = [(v, d) for v, d in dated if not is_pre_release(v)]
        last_version = max(stable or dated, key=lambda x: (release_key(x[0]), x[1]))[0]
        last_date = newest_build
        if coord.endswith('-ktx'):
            ours = {v for v, _ in dated}
            later = [(v, d) for v, d in published.get(coord[:-len('-ktx')]) or []
                     if d and d > newest_build and v not in ours]
            if later:
                v, d = max(later, key=lambda x: x[1])
                moved.append(f"MOVED ON WITHOUT IT: {coord} has no build after {last_version} ({newest_build}); "
                             f"{coord[:-len('-ktx')]} has {v} ({d}) — its release notes say where the API went")
                continue
        crossed = common.datetime.date.fromisoformat(last_date) + common.datetime.timedelta(days=DORMANT_DAYS)
        if crossed < today:
            dormant.append(f"{coord} {last_version} (last build {last_date}){' NEW' if crossed.isoformat() >= since else ''}")
    return moved, dormant


def repos_moved_on(dormant, client, today):
    """For each artifact without a build for two years, whether its source repository still
    releases: then the project moved on under other coordinates (Coil 2 → io.coil-kt.coil3), and
    no newer version of ours will ever come. Returns (lines, calls ok, failures)."""
    lines, ok, failures = [], 0, []
    for entry in dormant:
        coord, version = entry.split()[:2]
        try:
            repo = deps_dev_repo(coord, version)
            ok += 1
            if not repo:
                continue
            release = github(f'repos/{repo}/releases/latest', client)
            ok += 1
        except RuntimeError as e:
            if '404' in str(e):  # no GitHub release at all: nothing moved on
                continue
            failures.append(f'source repository of {coord}: {e}')
            continue
        date = (release.get('published_at') or '')[:10]
        if date and (today - common.datetime.date.fromisoformat(date)).days < DORMANT_DAYS:
            lines.append(f"UPSTREAM MOVED ON: {coord} stops at {version}, but {repo} released "
                         f"{release.get('tag_name')} ({date}) — likely under other coordinates; its "
                         'release notes or migration guide say what replaces ours')
    return lines, ok, failures


def androidx_pins_line(pins):
    """One line for every AndroidX pre-release or variant pin, instead of a missing-repository
    warning each: AndroidX has no GitHub releases to follow."""
    names = sorted({f"{e['coord']} {e['version']}" for e in pins})
    return (f'ANDROIDX PINS ({len(names)}): no GitHub releases; their release notes are at '
            f"https://developer.android.com/jetpack/androidx/releases/<group> — {', '.join(names)}")


def assess(entry, published, since):
    """One line about a dependency when something newer matters, otherwise None."""
    ours = entry['version']
    dates = dict(published)
    later = [(v, d) for v, d in published if release_key(v) > release_key(ours)]
    # A plain version is compared with plain releases only: "2.4.4-lite" is another variant.
    stable = [(v, d) for v, d in later if '-' not in v]
    notes = []
    # NEW is decided by the versions the line names: a pre-release of a stable dependency is
    # not printed, so it must not mark the line either.
    shown = []
    if ours not in dates:
        notes.append('our version is not listed there (a local or vendored build, or a gap in the index)')
    if is_variant(ours):
        pattern = same_variant(ours)
        # Not taken from `later`: a rebuild of the same release has the same release_key.
        newer = [(v, d) for v, d in published if pattern.match(v) and variant_key(v) > variant_key(ours)]
        if newer:
            notes.append(f"NEWER BUILD OF THE SAME VARIANT: {', '.join(v for v, _ in newer)}")
            later = later + [p for p in newer if p not in later]
            shown += newer
    elif stable:
        v, d = max(stable, key=lambda x: release_key(x[0]))
        notes.append(f"newest stable {v}{f' ({d})' if d else ''}")
        shown.append((v, d))
    if is_pre_release(ours):
        line = [(v, d) for v, d in later if is_pre_release(v)]
        if line:
            v, d = max(line, key=lambda x: release_key(x[0]) + (x[1],))
            notes.append(f"newest pre-release {v}{f' ({d})' if d else ''}")
            shown.append((v, d))
    if not notes:
        return None
    new = any(d and d >= since for _, d in shown)
    ours_date = f' ({dates[ours]})' if dates.get(ours) else ''
    return f"{entry['coord']} {ours}{ours_date} — {'; '.join(notes)}{' NEW' if new else ''}  [{entry['where']}]"


def config_gaps(root, config, vendored):
    """Lines describing where config.json and the project disagree; checked without the network.
    `vendored` are the paths of the vendored artifacts the build uses."""
    gaps = []
    configured = config.get('vendored', {})
    used = list(vendored)
    for path in used:
        if path not in configured:
            gaps.append(f'UNCONFIGURED VENDORED: {path} — run --init to name its upstream repository '
                        'or mark it not watched')
    for path in configured:
        if path not in used:
            gaps.append(f'STALE CONFIG: vendored {path} is no longer used by the build — run --init')
    for field in ('exclude', 'include'):
        for path in config.get('inputs', {}).get(field, []):
            if not os.path.exists(os.path.join(root, path)):
                gaps.append(f'STALE CONFIG: inputs.{field} {path} — no such file')
    return gaps


def window_start(root):
    """Where NEW starts: the previous run, or on a first run the FIRST_RUN_DAYS that feeds.py
    looks back too — from the year 0, every line was NEW and the mark said nothing."""
    prev = common.newest_slice(root)
    if prev:
        return common.load_slice(prev)['run']['started'][:10]
    return (common.today() - common.datetime.timedelta(days=common.FIRST_RUN_DAYS)).isoformat()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', help='scratch directory; releases.json is written there for slice.py')
    p.add_argument('--since')
    p.add_argument('--body', metavar='OWNER/REPO@TAG', help='print the notes of one GitHub release')
    args = p.parse_args()
    client = github_client()

    if args.body:
        repo, _, tag = args.body.partition('@')
        print(github(f'repos/{repo}/releases/tags/{tag}', client).get('body') or '(no release notes)')
        return 0
    if not args.out:
        p.error('--out is required for a run')

    root = common.repo_root()
    since = (args.since or window_start(root))[:10]
    config = common.load_config(root)
    facts = load_facts(args.out)
    entries = facts_entries(root, facts)

    if client == 'gh':
        print('## GitHub API client: gh')
    else:
        print('## GitHub API client: curl — gh is not installed; unauthenticated, 60 requests an hour; '
              'install and authenticate gh for more')

    checked = [e for e in entries if e['version']]
    unique = sorted({e['coord'] for e in checked})
    remote = [r for r in facts['repositories'] if r['url'].startswith(('http://', 'https://'))]
    public = [r['url'].rstrip('/') for r in remote if not r['credentials']]
    private = [r['url'].rstrip('/') for r in remote if r['credentials']]
    repos = list(dict.fromkeys(FALLBACK_REPOS + public))
    published, sources, ok, failures = fetch_versions(unique, repos, private)
    # The base of a -ktx artifact is asked too: AndroidX folds -ktx artifacts back into it.
    bases = sorted({c[:-len('-ktx')] for c in unique if c.endswith('-ktx')} - set(unique))
    base_published, _, base_ok, base_failures = fetch_versions(bases, repos, private)
    ok += base_ok
    # A -ktx artifact without a base artifact is normal; only a call that broke is a failure.
    failures += [f for f in base_failures if f.startswith('deps.dev ')]

    print(f'\n## Dependencies (deps.dev, maven-metadata.xml as fallback; NEW = published on or after {since})')
    up_to_date, not_found = 0, []
    for e in sorted(checked, key=lambda e: e['coord']):
        if not published.get(e['coord']):
            not_found.append(f"{e['coord']} {e['version']} — {sources[e['coord']]}  [{e['where']}]")
            continue
        line = assess(e, published[e['coord']], since)
        if line:
            print(line + ('' if sources[e['coord']] == 'deps.dev' else f" (from {sources[e['coord']]}, no dates)"))
        else:
            up_to_date += 1
    print(f"up to date: {up_to_date} · managed by a BOM or without a version: {len(entries) - len(checked)}")
    for line in not_found:
        print('NOT CHECKED: ' + line)
    moved, dormant = stopped_artifacts(unique, {**base_published, **published}, common.today(), since)
    for line in moved:
        print(line)
    if dormant:
        print(f'NO BUILD FOR TWO YEARS ({len(dormant)}; NEW = crossed two years since {since}):')
        for line in dormant:
            print('  ' + line)
        moved_on, moved_ok, moved_failures = repos_moved_on(dormant, client, common.today())
        ok += moved_ok
        failures += moved_failures
        for line in moved_on:
            print(line)

    watched = {}
    for repo, prefixes in TOOLCHAIN.items():
        if any(e['coord'].startswith(prefixes) for e in entries):
            watched[repo] = 'toolchain'
    pins = [e for e in checked if is_pre_release(e['version']) or is_variant(e['version'])]
    # AndroidX publishes no GitHub releases: its notes are per-group pages, so its pins are not
    # looked up one by one. Their versions were checked above like any other dependency.
    androidx_pins = [e for e in pins if e['coord'].startswith('androidx.')]
    pins = [e for e in pins if not e['coord'].startswith('androidx.')]
    linked, links_ok, link_failures = pin_repositories(pins, published)
    ok += links_ok
    failures += link_failures
    for line in link_failures:
        print(f'FAILED: {line} — its GitHub releases are not asked this run')
    for e, repo in linked:
        if repo:
            watched.setdefault(repo, f"pin {e['coord']} {e['version']}")
        else:
            print(f"PIN WITHOUT REPOSITORY: {e['coord']} {e['version']} — deps.dev links no GitHub repository; "
                  f"add one under releases with --init if a person should follow it  [{e['where']}]")
    if androidx_pins:
        print(androidx_pins_line(androidx_pins))
    for repo in config.get('releases', []):
        watched.setdefault(repo, 'config.json')
    # A vendored artifact's upstream is followed by its head commit: forks rarely tag.
    commits = list(dict.fromkeys(config.get('commits', []) + [
        v['github'] for v in config.get('vendored', {}).values() if 'github' in v]))

    print(f'\n## GitHub releases (NEW = published on or after {since})')
    for repo, why in watched.items():
        try:
            rel = github(f'repos/{repo}/releases?per_page=5', client)
            ok += 1
        except Exception as e:
            failures.append(f'GitHub releases {repo}: {e}')
            print(f'{repo} ({why}): FAILED — {e}')
            continue
        parts = []
        for r in rel:
            date = (r.get('published_at') or '')[:10]
            parts.append(f"{date} {r['tag_name']}{'*' if r.get('prerelease') else ''}{' NEW' if date >= since else ''}")
        print(f"{repo} ({why}): {' | '.join(parts) or 'no releases'}")
    print('(* = marked pre-release on GitHub)')

    print('\n## Head commits')
    for repo in commits:
        try:
            c = github(f'repos/{repo}/commits?per_page=1', client)[0]
            ok += 1
            date = c['commit']['committer']['date'][:10]
            print(f"{repo}: {c['sha'][:7]} {date}{' NEW' if date >= since else ''}")
        except Exception as e:
            failures.append(f'GitHub head commit {repo}: {e}')
            print(f'{repo}: FAILED — {e}')

    print('\n## config.json against the project')
    gaps = config_gaps(root, config, [v['path'] for v in facts['vendored']])
    for g in gaps:
        print(g)
    print(f'config.json: needs update — {len(gaps)} entries' if gaps else 'config.json: up to date')

    print(f'\nfetch log: {ok} calls ok, {len(failures)} failed')
    for line in failures:
        print('  FAILED: ' + line)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, 'releases.json'), 'w', encoding='utf-8') as f:
        json.dump({'github_client': client, 'calls_ok': ok, 'calls_failed': len(failures),
                   'failed_calls': failures, 'not_checked': not_found}, f, indent=2)
    return 0


if __name__ == '__main__':
    sys.exit(main())
