#!/usr/bin/env python3
"""Asks Gradle for the build facts the audit needs, through an init script; nothing is compiled.

    python3 facts.py --out SCRATCH                 run Gradle, write SCRATCH/facts.json
    python3 facts.py --raw DIR --out SCRATCH       only turn raw init-script output into facts.json

`audit-facts.init.gradle` next to this file configures the build — the root one and every
included build — and writes one raw JSON per build. This script reduces them to what a run
reasons about: the Gradle, AGP and Kotlin versions; the application modules with their SDK
levels, JVM targets and manifests; the SDK levels of all Android modules; every declared
dependency once, with where it is declared; the version catalogs; the vendored jar and AAR
folders and local Maven artifacts; the repositories; the plugin sources of included builds;
the Gradle properties.

Against the facts the newest slice kept it prints what changed — a version, a dependency, an
application module, an SDK level, a flag, a vendored artifact, a repository, a plugin source —
or, when that slice kept none, the key files to read in full (`key_files`).

It also checks itself: a value the init script could not read, an included build Gradle did
not configure, an Android module without compileSdk, and a Gradle, AGP or Kotlin major
version newer than the ones this skill was tested with are printed and kept in facts.json.

Gradle that cannot configure the build stops the run with the error and what usually fixes
it. The JDK is JAVA_HOME; without it the script stops and names Android Studio's JDK if one
is installed.
"""
import argparse
import collections
import glob
import itertools
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import common

HERE = os.path.dirname(os.path.abspath(__file__))
INIT_SCRIPT = os.path.join(HERE, 'audit-facts.init.gradle')
TASK = 'upstreamAuditFacts'
# Seconds. Below the 10 minutes a Claude Code shell command may run, so that the script stops
# with the log path instead of being killed; a first run downloads every plugin of the build.
GRADLE_TIMEOUT = 540
# The major versions this skill was tested with; a newer one is reported, not refused.
TESTED_MAJOR = {'gradle': 9, 'agp': 9, 'kgp': 2}
STUDIO_JDKS = [
    '/Applications/Android Studio.app/Contents/jbr/Contents/Home',
    os.path.expanduser('~/Applications/Android Studio.app/Contents/jbr/Contents/Home'),
    '/opt/android-studio/jbr',
    os.path.expanduser('~/android-studio/jbr'),
    r'C:\Program Files\Android\Android Studio\jbr',
]
# Where Android Studio installs the SDK by default.
STUDIO_SDKS = [
    os.path.expanduser('~/Library/Android/sdk'),
    os.path.expanduser('~/Android/Sdk'),
    os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Android', 'Sdk'),
]
# What a failed configuration usually means, by a phrase of Gradle's error.
HINTS = [
    ('SDK location not found', 'set ANDROID_HOME to the Android SDK, or add sdk.dir to local.properties; '
                               'no SDK was found where Android Studio installs it'),
    ('Unsupported class file major version', 'the JDK in JAVA_HOME does not suit this Gradle version'),
    ('Could not resolve', 'Gradle needs the network once to download the plugins and dependencies of the build'),
    ('Could not GET', 'Gradle needs the network once to download the plugins and dependencies of the build'),
    ('Could not find or load main class', 'JAVA_HOME does not point to a JDK'),
]
CATALOG_FROM = re.compile(r'''\bfrom\s*\(\s*files\s*\(\s*(['"])([^'"]+)\1''')
SETTINGS_NAMES = ('settings.gradle', 'settings.gradle.kts')


def java_home():
    """JAVA_HOME, or a stop that names Android Studio's JDK when one is installed."""
    home = os.environ.get('JAVA_HOME')
    if home:
        return home
    found = next((p for p in STUDIO_JDKS if os.path.isdir(p)), None)
    raise SystemExit('JAVA_HOME is not set; the audit runs Gradle and needs a JDK. '
                     + (f'Android Studio has one: export JAVA_HOME="{found}"' if found
                        else 'Point it to a JDK 17 or newer, for example the one inside Android Studio (jbr).'))


def android_sdk(project_dir):
    """The SDK to give Gradle as ANDROID_HOME, or None when the project already has one.

    A fresh clone or a git worktree has no local.properties (Android Studio writes it), and then
    AGP cannot find the SDK: the build fails, or every module loses values. Only whether the file
    exists is checked; its contents are Gradle's to read."""
    if os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT'):
        return None
    if os.path.isfile(os.path.join(project_dir, 'local.properties')):
        return None
    return next((p for p in STUDIO_SDKS if os.path.isdir(p)), None)


def failure_lines(output):
    """Gradle's own account of a failure — where and what went wrong — without the stack trace,
    which stays in the log; the last lines of output when Gradle gave no such account."""
    lines = [line for line in output.strip().split('\n') if line.strip() and not line.startswith('WARNING:')]
    starts = [i for i, line in enumerate(lines) if line.startswith(('* Where:', '* What went wrong:'))]
    if not starts:
        return lines[-15:]
    end = next((i for i in range(starts[0], len(lines))
                if lines[i].startswith(('* Try:', '* Exception is:', '* Get more help'))), len(lines))
    return lines[starts[0]:end][:30]


def gradle(project_dir, gradlew, task, raw_dir, home, log_path, timeout):
    """Runs `task` with the init script, its output going to `log_path` while it runs.
    Returns (exit code, or None on timeout; output; seconds)."""
    os.makedirs(raw_dir, exist_ok=True)
    # --stacktrace costs nothing on success and puts the stack of a failure into the log.
    # The configuration cache is off: on a cache hit Gradle skips configuration and the init
    # script writes nothing. Isolated projects go off with it, for this run only: Gradle refuses
    # to drop the cache while they are on (nowinandroid sets them in gradle.properties), and the
    # init script reads every project from the root, which they forbid.
    cmd = [gradlew, '-p', project_dir, task, '-q', '--stacktrace', '--no-configuration-cache',
           '-Dorg.gradle.isolated-projects=false', '-I', INIT_SCRIPT, f'-DupstreamAudit.out={raw_dir}']
    env = dict(os.environ, JAVA_HOME=home)
    sdk = android_sdk(project_dir)
    if sdk:
        env['ANDROID_HOME'] = sdk
        print(f'No ANDROID_HOME and no local.properties: using the Android SDK at {sdk}', flush=True)
    shown = [os.path.basename(gradlew)] + cmd[3:-3] + ['-I', os.path.basename(INIT_SCRIPT)]
    print(f"Running Gradle in {project_dir}: {' '.join(shown)}", flush=True)
    started = time.monotonic()
    with open(log_path, 'w', encoding='utf-8') as log:
        proc = subprocess.Popen(cmd, cwd=project_dir, stdout=log, stderr=subprocess.STDOUT, env=env)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            code = None
    with open(log_path, encoding='utf-8', errors='replace') as f:
        output = f.read()
    return code, output, round(time.monotonic() - started)


def run_gradle(project_dir, gradlew, raw_dir, home, log_path=None):
    """Runs the init script; stops with Gradle's error and a hint when the build does not configure.

    Gradle's output goes to `log_path` (default: next to `raw_dir`) while it runs, so that
    `tail -f` on it shows how far the configuration got: the init script writes a line when a
    build starts and every 50 configured projects."""
    log_path = log_path or os.path.normpath(raw_dir) + '.log'
    print('Configuration only. About 20 s, minutes on a first run that downloads the plugins. '
          f'Progress: tail -f {log_path}', flush=True)
    code, output, elapsed = gradle(project_dir, gradlew, TASK, raw_dir, home, log_path, GRADLE_TIMEOUT)
    if code is None:
        raise SystemExit(f'Gradle did not finish configuring in {GRADLE_TIMEOUT // 60} minutes, so the '
                         f'audit stops. Its output so far: {log_path}')
    if code != 0:
        hints = [hint for phrase, hint in HINTS if phrase in output]
        raise SystemExit('Gradle could not configure the build, so the audit stops.\n'
                         + '\n'.join(failure_lines(output))
                         + ''.join(f'\nLikely fix: {h}' for h in hints)
                         + f'\nFull output, with the stack trace: {log_path}')
    print(f'Gradle configured the build in {elapsed} s; its output: {log_path}', flush=True)
    return output


def major(version):
    m = re.match(r'(\d+)', version or '')
    return int(m.group(1)) if m else None


def catalog_files(root, builds):
    """The catalog files, for anchors: Gradle gives their content but not their path."""
    found = sorted(glob.glob(os.path.join(root, 'gradle', '*.versions.toml')))
    for build in builds:
        for name in SETTINGS_NAMES:
            settings = os.path.join(build, name)
            if os.path.isfile(settings):
                with open(settings, encoding='utf-8', errors='replace') as f:
                    text = f.read()
                for m in CATALOG_FROM.finditer(text):
                    path = os.path.normpath(os.path.join(build, m.group(2)))
                    if os.path.isfile(path) and path not in found:
                        found.append(path)
    return found


def variant_sdk(android):
    """minSdk and targetSdk of every flavor combination: a variant takes each value from the
    first flavor, in dimension order, that sets it, else from defaultConfig. [] without flavors.
    A value set in androidComponents.beforeVariants is not seen."""
    flavors = android.get('productFlavors') or []
    if not flavors:
        return []
    dimensions = android.get('flavorDimensions') or []
    if not dimensions:  # one dimension needs no name
        groups = [flavors]
    else:
        groups = [[f for f in flavors if f.get('dimension') == d] for d in dimensions]
        groups = [g for g in groups if g]
    variants = []
    for combo in itertools.product(*groups):
        name = combo[0]['name'] + ''.join(f['name'][:1].upper() + f['name'][1:] for f in combo[1:])
        pick = lambda field: next((f[field] for f in combo if f.get(field)), android.get(field))  # noqa: E731
        variants.append({'variant': name, 'minSdk': pick('minSdk'), 'targetSdk': pick('targetSdk')})
    return variants


def variants_line(variants, shown=6):
    """`minSdk 26, targetSdk 35: prod, prodPaid; minSdk 26, targetSdk 36: demo` — grouped by value."""
    groups = collections.OrderedDict()
    for v in sorted(variants, key=lambda v: (int(v['targetSdk'] or 0), int(v['minSdk'] or 0), v['variant'])):
        groups.setdefault((v['minSdk'], v['targetSdk']), []).append(v['variant'])
    parts = []
    for (min_sdk, target_sdk), names in groups.items():
        more = f' and {len(names) - shown} more' if len(names) > shown else ''
        parts.append(f"minSdk {min_sdk}, targetSdk {target_sdk}: {', '.join(names[:shown])}{more}")
    return '; '.join(parts)


def normalize(raw_dir, project_dir, config=None):
    """facts.json from the raw files of the init script. `inputs.exclude` of config.json leaves
    a module (its folder or build script), a vendored path or a catalog file out of everything;
    `inputs.include` adds a file to the key files."""
    top = os.path.realpath(project_dir)
    inputs = (config or {}).get('inputs') or {}
    excluded = set(inputs.get('exclude') or [])

    def rel(path):
        return os.path.relpath(os.path.realpath(path), top).replace(os.sep, '/') if path else None

    def is_excluded(path):
        return path is not None and any(path == e or path.startswith(e.rstrip('/') + '/') for e in excluded)

    raw = [common.load_json(p, 'raw Gradle facts') for p in sorted(glob.glob(os.path.join(raw_dir, 'facts-*.json')))]
    for b in raw:
        b['projects'] = [p for p in b['projects']
                         if not (p['path'] != ':' and (is_excluded(rel(p['dir'])) or is_excluded(rel(p.get('buildFile')))))]
        catalog_names = {os.path.basename(e)[:-len('.versions.toml')] for e in excluded if e.endswith('.versions.toml')}
        b['catalogs'] = [c for c in b.get('catalogs') or [] if c['name'] not in catalog_names]
    root = next((b for b in raw if b['build'] == 'root'), None)
    if root is None:
        raise SystemExit(f'no facts of the root build in {raw_dir} — the init script did not run')
    builds = [b for b in raw if b is not root]
    problems, errors = [], []
    for b in raw:
        errors += [f"{b['build']}: {e}" for e in b.get('errors') or []]
        errors += [f"{b['build']} settings: {e}" for e in (b.get('settings') or {}).get('errors') or []]
    configured = {b['build'] for b in builds}
    for inc in root.get('includedBuilds') or []:
        if inc['name'] not in configured:
            problems.append(f"included build {inc['name']} was not configured — its facts are missing")

    projects = [dict(p, build=b['build']) for b in raw for p in b['projects']]

    def where(p):
        """A project as the report names it: `:app`, or `build-conventions:` in an included build."""
        return p['path'] if p['build'] == 'root' else f"{p['build']}{p['path']}"

    android = [p for p in projects if p.get('android')]
    apps = []
    for p in android:
        if 'com.android.application' not in (p.get('plugins') or []):
            continue
        a = p['android']
        apps.append({'project': where(p), 'dir': rel(p['dir']), 'build_file': rel(p.get('buildFile')),
                     'manifest': rel(a.get('manifest')), 'namespace': a.get('namespace'),
                     'compileSdk': a.get('compileSdk'), 'minSdk': a.get('minSdk'), 'targetSdk': a.get('targetSdk'),
                     'javaTarget': a.get('javaTarget'),
                     'jvmTarget': a.get('kotlinOptionsJvmTarget') or p.get('kotlinJvmTarget'),
                     'variants': variant_sdk(a)})
    sdk_levels = {}
    for field in ('compileSdk', 'minSdk', 'targetSdk'):
        counts = collections.Counter(p['android'].get(field) or 'not set' for p in android)
        common_value = counts.most_common(1)[0][0] if counts else None
        sdk_levels[field] = {'counts': dict(counts),
                             'other_than_most': sorted(where(p) for p in android
                                                       if (p['android'].get(field) or 'not set') != common_value)[:20]}
    for p in android:
        if not p['android'].get('compileSdk'):
            problems.append(f'{where(p)}: an Android module without compileSdk')
    deviating = {m for level in sdk_levels.values() for m in level['other_than_most']}
    module_build_files = {where(p): rel(p['buildFile']) for p in android if where(p) in deviating and p.get('buildFile')}

    versions = {'gradle': root.get('gradleVersion'),
                'agp': root.get('agpVersion') or next((b.get('agpVersion') for b in builds if b.get('agpVersion')), None),
                'kgp': root.get('kgpVersion') or next((b.get('kgpVersion') for b in builds if b.get('kgpVersion')), None)}
    catalog_modules = {lib['module'] for c in root.get('catalogs') or [] for lib in c['libraries']}
    places, declared_by_people = collections.defaultdict(set), set()
    builds_of = collections.defaultdict(set)  # every build that declares it; `examples` stops at three
    for p in projects:
        for d in p.get('dependencies') or []:
            key = (d['module'], d.get('version'))
            places[key].add(where(p))
            builds_of[key].add(p['build'])
            # Raw files written before the "declared" flag existed count every dependency as declared.
            if d.get('declared', True):
                declared_by_people.add(key)
    build_file_of = {where(p): (p['build'], rel(p['buildFile'])) for p in projects if p.get('buildFile')}
    for b in raw:
        settings = b.get('settings') or {}
        # buildscript { classpath "…" } of a settings script, named after the build's settings.
        for d in settings.get('dependencies') or []:
            at = f"{b['build']} settings"
            places[(d['module'], d.get('version'))].add(at)
            builds_of[(d['module'], d.get('version'))].add(b['build'])
            declared_by_people.add((d['module'], d.get('version')))
            if settings.get('settingsFile'):
                build_file_of[at] = (b['build'], rel(settings['settingsFile']))

    def files_of(at):
        """Up to three build files per build: a pin written out in an included build must not be
        cut off by the hundreds of root modules that declare the same dependency."""
        per_build = collections.defaultdict(set)
        for w in at:
            if w in build_file_of:
                build, path = build_file_of[w]
                per_build[build].add(path)
        return sorted(f for files in per_build.values() for f in sorted(files)[:3])
    dependencies, plugin_added = [], []
    for (module, version), at in sorted(places.items(), key=lambda x: (x[0][0], x[0][1] or '')):
        group = module.split(':')[0]
        # Not in a catalog, and versioned exactly like the plugin that brings it: databinding with
        # AGP, the Kotlin compiler plugins with KGP. They move when the plugin moves.
        moves_with = (module not in catalog_modules and version is not None and (
            (version == versions['agp'] and group.startswith(('com.android', 'androidx.databinding')))
            or (version == versions['kgp'] and group == 'org.jetbrains.kotlin')))
        entry = {'module': module, 'version': version, 'declared_in': len(at), 'examples': sorted(at)[:3],
                 'builds': sorted(builds_of[(module, version)])}
        if (module, version) in declared_by_people and not moves_with:
            entry['files'] = files_of(at)
            dependencies.append(entry)
        else:
            plugin_added.append(entry)

    vendored = collections.OrderedDict()
    for p in projects:
        for f in p.get('localFiles') or []:
            entry = vendored.setdefault(rel(os.path.dirname(f)), {'path': rel(os.path.dirname(f)),
                                                                  'reason': 'jar or AAR files', 'files': [],
                                                                  'used_by': []})
            if os.path.basename(f) not in entry['files']:
                entry['files'].append(os.path.basename(f))
            if where(p) not in entry['used_by']:
                entry['used_by'].append(where(p))

    repositories = collections.OrderedDict()
    for b in raw:
        settings = b.get('settings') or {}
        sources = [(r, f"{b['build']} settings") for r in (settings.get('pluginRepositories') or [])
                   + (settings.get('repositories') or [])]
        sources += [(r, where(dict(p, build=b['build']))) for p in b['projects'] for r in p.get('repositories') or []]
        for r, at in sources:
            url = r.get('url') or ', '.join(r.get('dirs') or []) or r.get('name')
            entry = repositories.setdefault(url, {'url': url, 'credentials': False, 'where': at})
            entry['credentials'] = entry['credentials'] or bool(r.get('credentials'))
            for d in r.get('dirs') or []:
                vendored.setdefault(rel(d), {'path': rel(d), 'reason': 'flatDir repository', 'files': [], 'used_by': []})
    # Each artifact of a local Maven repository is vendored on its own: it has its own upstream.
    for url in list(repositories):
        if not url.startswith('file:'):
            continue
        # A URL, not a path: "file:/work/My%20App/libs/m2/" is the folder "My App".
        base = urllib.request.url2pathname(urllib.parse.urlparse(url).path)
        if not os.path.isdir(base):
            problems.append(f'local Maven repository {url} not found at {base} — its artifacts are not checked')
            continue
        for meta in sorted(glob.glob(os.path.join(base, '**', 'maven-metadata.xml'), recursive=True)):
            path = rel(os.path.dirname(meta))
            vendored.setdefault(path, {'path': path, 'reason': f'local Maven repository {rel(base)}',
                                       'files': [], 'used_by': []})

    plugin_sources = sorted(rel(s) for p in projects for s in p.get('pluginSources') or [])
    build_dirs = [b['dir'] for b in raw]
    build_files = sorted({rel(p['buildFile']) for p in projects if p.get('buildFile')}
                         | {rel((b.get('settings') or {}).get('settingsFile')) for b in raw
                            if (b.get('settings') or {}).get('settingsFile')})
    for fixed in ('gradle.properties', 'gradle/wrapper/gradle-wrapper.properties'):
        if os.path.isfile(os.path.join(top, fixed)):
            build_files.append(fixed)

    warnings = [f'{name.upper()} {v} is newer than the {TESTED_MAJOR[name]}.x this skill was tested with — '
                f'the facts may be incomplete' for name, v in versions.items()
                if major(v) is not None and major(v) > TESTED_MAJOR[name]]
    if errors:
        problems.insert(0, f'{len(errors)} values the init script could not read (see "errors")')

    result = {
        'schema': 1,
        'versions': versions,
        'kind': 'application' if apps else 'library',
        # An included build can pin its own Kotlin or AGP, apart from the root build.
        'builds': [{'name': b['build'], 'dir': rel(b['dir']), 'agp': b.get('agpVersion'), 'kgp': b.get('kgpVersion')}
                   for b in raw],
        'properties': root.get('properties') or {},
        'app_modules': apps,
        'android_modules': len(android),
        'sdk_levels': sdk_levels,
        'catalogs': root.get('catalogs') or [],
        'catalog_files': [f for f in (rel(p) for p in catalog_files(top, build_dirs)) if not is_excluded(f)],
        'dependencies': dependencies,
        'plugin_added': plugin_added,
        'vendored': [v for v in vendored.values() if not is_excluded(v['path'])],
        'repositories': list(repositories.values()),
        'plugin_sources': plugin_sources,
        'build_files': build_files,
        'module_build_files': module_build_files,
        'version_warnings': warnings,
        'problems': problems,
        'errors': errors,
    }
    result['key_files'] = sorted(dict.fromkeys(
        [f for f in key_files(result) if not is_excluded(f)]
        + [f for f in inputs.get('include') or [] if os.path.isfile(os.path.join(top, f))]))
    return result


def key_files(facts):
    """The files a run reads in full when there is nothing to compare with: the catalogs, the
    root scripts and properties, every included build's scripts and plugin sources, the
    application modules' build scripts and manifests, and the build script of every module
    whose SDK level differs from the rest (it sets its own)."""
    builds = {b['name']: b['dir'] for b in facts['builds']}
    files = list(facts.get('catalog_files') or [])
    for path in facts.get('build_files') or []:
        folder = os.path.dirname(path)
        in_build = any(folder == (d if d != '.' else '') for d in builds.values())
        if in_build or path in ('gradle.properties', 'gradle/wrapper/gradle-wrapper.properties'):
            files.append(path)
    files += facts.get('plugin_sources') or []
    for app in facts.get('app_modules') or []:
        files += [p for p in (app.get('build_file'), app.get('manifest')) if p]
    others = {m for level in (facts.get('sdk_levels') or {}).values() for m in level['other_than_most']}
    files += [f for m, f in (facts.get('module_build_files') or {}).items() if m in others]
    return sorted(dict.fromkeys(files))


def dependency_versions(dependencies):
    """`module@build` → every version that build declares, "1.0, 2.0": two modules of one build
    can pin different versions, and a change to either must show. One version stays a plain
    "1.0", as in the slices written before."""
    by_key = collections.defaultdict(set)
    for d in dependencies:
        for build in d.get('builds') or [d['examples'][0].split(':')[0] or 'root']:
            by_key[f"{d['module']}@{build}"].add(d['version'])
    # A version from a BOM is None, as in the older slices.
    return {k: next(iter(v)) if len(v) == 1 else ', '.join(sorted(x or 'none' for x in v))
            for k, v in sorted(by_key.items())}


def snapshot(facts):
    """What a slice keeps of the facts, small enough to commit, for the next run to compare."""
    return {
        'versions': facts['versions'],
        'builds': {b['name']: {'agp': b.get('agp'), 'kgp': b.get('kgp')} for b in facts['builds']},
        'app_modules': {a['project']: dict({k: a.get(k) for k in ('compileSdk', 'minSdk', 'targetSdk', 'javaTarget',
                                                                  'jvmTarget', 'manifest')},
                                           # Only with flavors: older slices have no such key.
                                           **({'variants': variants_line(a['variants'], shown=1000)}
                                              if a.get('variants') else {}))
                        for a in facts['app_modules']},
        'sdk_levels': {field: level['counts'] for field, level in facts['sdk_levels'].items()},
        'properties': facts['properties'],
        'dependencies': dependency_versions(facts['dependencies']),
        'vendored': sorted(v['path'] for v in facts['vendored']),
        'repositories': {r['url']: r['credentials'] for r in facts['repositories']},
        'plugin_sources': facts['plugin_sources'],
    }


def changes(previous, current):
    """What moved between two snapshots, one line each; None when the previous slice kept none."""
    if not previous:
        return None
    lines = []

    def compare(label, before, now, show=lambda k: k):
        for key in sorted(set(before) | set(now), key=str):
            if key not in before:
                lines.append(f'{label} added: {show(key)}' + (f' = {now[key]}' if not isinstance(now, list) else ''))
            elif key not in now:
                lines.append(f'{label} removed: {show(key)}')
            elif not isinstance(now, list) and before[key] != now[key]:
                lines.append(f'{label} changed: {show(key)} {before[key]} → {now[key]}')

    as_map = lambda items: items if isinstance(items, dict) else {i: True for i in items}  # noqa: E731
    compare('version', previous.get('versions') or {}, current['versions'])
    compare('build', previous.get('builds') or {}, current['builds'])
    for name in sorted(set(previous.get('app_modules') or {}) | set(current['app_modules'])):
        before, now = (previous.get('app_modules') or {}).get(name), current['app_modules'].get(name)
        if before is None or now is None:
            lines.append(f"application module {'added' if before is None else 'removed'}: {name}")
        else:
            compare(f'application module {name}', before, now)
    for field in current['sdk_levels']:
        before = (previous.get('sdk_levels') or {}).get(field) or {}
        if before != current['sdk_levels'][field]:
            lines.append(f'{field} across Android modules: {before} → {current["sdk_levels"][field]}')
    compare('property', previous.get('properties') or {}, current['properties'])
    compare('dependency', previous.get('dependencies') or {}, current['dependencies'], lambda k: k.replace('@', ' in '))
    compare('vendored', as_map(previous.get('vendored') or []), as_map(current['vendored']))
    compare('repository', previous.get('repositories') or {}, current['repositories'])
    compare('plugin source', as_map(previous.get('plugin_sources') or []), as_map(current['plugin_sources']))
    return [line.replace(' = True', '') for line in lines]


def collect(project_dir, raw_dir, gradlew=None, home=None, config=None):
    gradlew = gradlew or os.path.join(project_dir, 'gradlew.bat' if os.name == 'nt' else 'gradlew')
    if not os.path.isfile(gradlew):
        raise SystemExit(f'no Gradle wrapper at {gradlew}; the audit runs the project\'s own gradlew')
    run_gradle(project_dir, gradlew, raw_dir, home or java_home())
    return normalize(raw_dir, project_dir, config)


def summary(facts):
    v = facts['versions']
    lines = [f"Gradle {v['gradle']} · AGP {v['agp'] or 'none'} · Kotlin Gradle plugin {v['kgp'] or 'none'}"]
    for a in facts['app_modules']:
        lines.append(f"application module {a['project']}: compileSdk {a['compileSdk']}, minSdk {a['minSdk']}, "
                     f"targetSdk {a['targetSdk']}, Java {a['javaTarget']}, jvmTarget {a['jvmTarget']}, "
                     f"manifest {a['manifest']}")
        if a.get('variants'):
            lines.append(f"  by variant (product flavors override defaultConfig): {variants_line(a['variants'])}")
    if not facts['app_modules']:
        lines.append('no application module — a library project: the Google Play and targetSdk checks do not apply')
    lines.append(f"Android modules: {facts['android_modules']} · dependencies: {len(facts['dependencies'])} "
                 f"(+{len(facts['plugin_added'])} added by plugins) · "
                 f"vendored: {len(facts['vendored'])} · repositories: {len(facts['repositories'])} · "
                 f"builds: {', '.join(b['name'] for b in facts['builds'])}")
    lines += [f'VERSION WARNING: {w}' for w in facts['version_warnings']]
    lines += [f'PROBLEM: {p}' for p in facts['problems']]
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', required=True, help='scratch directory; facts.json is written there')
    p.add_argument('--raw', help='raw init-script output to normalize instead of running Gradle')
    args = p.parse_args()
    root = common.repo_root()
    # Optional: --init runs this script before config.json exists.
    config = common.load_config(root, required=False)
    raw_dir = args.raw or os.path.join(args.out, 'gradle-facts')
    facts = normalize(raw_dir, root, config) if args.raw else collect(root, raw_dir, config=config)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, 'facts.json'), 'w', encoding='utf-8') as f:
        json.dump(facts, f, indent=2)
        f.write('\n')
    print(summary(facts))
    prev_path = common.newest_slice(root)
    diff = changes((common.load_slice(prev_path) if prev_path else {}).get('facts'), snapshot(facts))
    print('\n## Since the previous run')
    if diff is None:
        print('FIRST FACTS RUN: the previous slice kept no facts, so nothing can be compared. Read each of '
              'these files in full and list it under "inputs_read" in the edits file:')
        print('\n'.join(facts['key_files']))
    else:
        print('\n'.join(diff) or 'no change in the build facts')
    print(f"\nwritten {os.path.join(args.out, 'facts.json')}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
