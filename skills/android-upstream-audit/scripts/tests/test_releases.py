"""releases.py: dependency entries, version comparison, failed calls."""
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import fixtures  # noqa: F401  (puts the scripts on sys.path)
import common  # noqa: E402
import releases  # noqa: E402


class Releases(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = self.dir.name

    def tearDown(self):
        self.dir.cleanup()

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)

    def test_entries_from_facts_with_the_line_to_change(self):
        self.write('gradle/libs.versions.toml', '\n'.join([
            '[versions]', 'okhttp = "5.5.0"',
            '[libraries]',
            'square-okhttp = { module = "com.squareup.okhttp3:okhttp", version.ref = "okhttp" }',
            'firebase_bom = { module = "com.google.firebase:firebase-bom", version = "34.9.0" }',
            '[plugins]',
            'android-application = { id = "com.android.application", version = "9.2.1" }', '']))
        self.write('rules/build.gradle', 'plugins {\n  id "com.android.application"\n'
                                         '  id "org.jetbrains.kotlin.jvm" version "2.3.0"\n}\n'
                                         'dependencies {\n  compileOnly "dev.detekt:detekt-api:2.0.0-alpha.6"\n'
                                         '  implementation "com.squareup.okhttp3:okhttp:5.5.0"\n}\n')
        self.write('build.gradle', "plugins {\n  alias(libs.plugins.android.application) apply false\n}\n")
        facts_data = {
            'catalog_files': ['gradle/libs.versions.toml'],
            'catalogs': [{'name': 'libs', 'versions': {}, 'libraries': [
                {'alias': 'square.okhttp', 'module': 'com.squareup.okhttp3:okhttp', 'version': '5.5.0'},
                {'alias': 'firebase.bom', 'module': 'com.google.firebase:firebase-bom', 'version': '34.9.0'}],
                'plugins': [{'alias': 'android.application', 'id': 'com.android.application', 'version': '9.2.1'}]}],
            'dependencies': [
                # Through the catalog in :app, written out in rules/build.gradle: two places to change.
                {'module': 'com.squareup.okhttp3:okhttp', 'version': '5.5.0', 'examples': [':app', 'rules:'],
                 'files': ['app/build.gradle', 'rules/build.gradle']},
                {'module': 'dev.detekt:detekt-api', 'version': '2.0.0-alpha.6', 'examples': ['rules:'],
                 'files': ['rules/build.gradle']},
                {'module': 'com.example:nowhere', 'version': '1.0', 'examples': [':lib'], 'files': []},
                # Plugin markers of plugins blocks: through the catalog in the root build file (the
                # catalog entry covers it), with a version of its own in rules/build.gradle.
                {'module': 'com.android.application:com.android.application.gradle.plugin', 'version': '9.2.1',
                 'examples': [':'], 'files': ['build.gradle']},
                {'module': 'org.jetbrains.kotlin.jvm:org.jetbrains.kotlin.jvm.gradle.plugin', 'version': '2.3.0',
                 'examples': ['rules:'], 'files': ['rules/build.gradle']},
            ],
        }
        got = sorted((e['coord'], e['where']) for e in releases.facts_entries(self.root, facts_data))
        self.assertEqual(got, [
            ('com.android.application:com.android.application.gradle.plugin', 'gradle/libs.versions.toml:7'),
            ('com.example:nowhere', 'declared in :lib'),
            ('com.google.firebase:firebase-bom', 'gradle/libs.versions.toml:5'),
            ('com.squareup.okhttp3:okhttp', 'gradle/libs.versions.toml:4'),
            ('com.squareup.okhttp3:okhttp', 'rules/build.gradle:7'),
            ('dev.detekt:detekt-api', 'rules/build.gradle:6'),
            ('org.jetbrains.kotlin.jvm:org.jetbrains.kotlin.jvm.gradle.plugin', 'rules/build.gradle:3'),
        ])

    def test_releases_needs_the_facts_of_this_run(self):
        with self.assertRaises(SystemExit) as e:
            releases.load_facts(self.root)
        self.assertIn('run `facts.py --out', str(e.exception))

    def test_assess_names_what_is_newer_and_marks_new(self):
        # (our version, published versions with dates, the line; None when nothing newer matters).
        # NEW means published on or after 2026-10-20.
        cases = [
            ('1.1.0', [('1.1.0', '2026-01-01'), ('1.2.0', '2026-10-21')],
             'g:a 1.1.0 (2026-01-01) — newest stable 1.2.0 (2026-10-21) NEW  [x:1]'),
            # NEW only by the versions the line shows: 2026-11-09, compose runtime 1.12.2 was marked
            # NEW by a 1.13.0-beta02 the line does not print.
            ('1.10.0', [('1.10.0', '2026-01-12'), ('1.12.2', '2026-10-15'), ('1.13.0-beta02', '2026-11-05')],
             'g:a 1.10.0 (2026-01-12) — newest stable 1.12.2 (2026-10-15)  [x:1]'),
            # Compared by release number, not by date: a later backport is not newer.
            ('2.0.0', [('2.0.0', '2026-01-01'), ('1.9.9', '2026-09-01')], None),
            # Another variant of a plain version is not a newer stable.
            ('2.4.2', [('2.4.2', ''), ('2.4.4-lite', '')], None),
            # A pre-release pin follows its pre-release line, not the older stable.
            ('1.2.0-alpha05', [('1.2.0-alpha05', '2024-02-14'), ('1.1.0', '2021-01-01'), ('1.4.0-alpha02', '2024-08-14')],
             'g:a 1.2.0-alpha05 (2024-02-14) — newest pre-release 1.4.0-alpha02 (2024-08-14)  [x:1]'),
            ('1.0.0-alpha01', [('1.0.0-alpha01', '2026-01-01'), ('1.0.0-beta01', '2026-10-21')],
             'g:a 1.0.0-alpha01 (2026-01-01) — newest pre-release 1.0.0-beta01 (2026-10-21) NEW  [x:1]'),
            ('3.0-alpha-8', [('3.0-alpha-8', '2024-06-04'), ('3.0-alpha-9', '2026-06-25')],
             'g:a 3.0-alpha-8 (2024-06-04) — newest pre-release 3.0-alpha-9 (2026-06-25)  [x:1]'),
            # An rc is past a beta of the same number, whatever the dates.
            ('1.0.0-rc01', [('1.0.0-beta02', '2026-11-01'), ('1.0.0-rc01', '2026-10-01')], None),
            # A variant pin: a newer build of the same variant, not the plain release.
            ('0.7.1-0.6.x-compat', [('0.7.1-0.6.x-compat', '2025-07-07'), ('0.8.0', '2026-05-07'),
                                    ('0.8.0-0.6.x-compat', '2026-05-07')],
             'g:a 0.7.1-0.6.x-compat (2025-07-07) — NEWER BUILD OF THE SAME VARIANT: 0.8.0-0.6.x-compat  [x:1]'),
            # A rebuild of the same release has the same release number.
            ('3.1.0-fork-1', [('3.1.0-fork-1', '2026-01-01'), ('3.1.0-fork-2', '2026-10-21'), ('3.1.0', '2026-01-01')],
             'g:a 3.1.0-fork-1 (2026-01-01) — NEWER BUILD OF THE SAME VARIANT: 3.1.0-fork-2 NEW  [x:1]'),
            ('3.1.0-fork-2', [('3.1.0-fork-1', '2026-01-01'), ('3.1.0-fork-2', '2026-10-21')], None),
        ]
        for ours, published, line in cases:
            with self.subTest(ours=ours):
                entry = {'coord': 'g:a', 'version': ours, 'where': 'x:1'}
                self.assertEqual(releases.assess(entry, published, '2026-10-20'), line)

    def test_androidx_pins_are_one_line_not_a_missing_repository_each(self):
        pins = [{'coord': 'androidx.biometric:biometric-ktx', 'version': '1.2.0-alpha05'},
                {'coord': 'androidx.test:runner', 'version': '1.7.0-rc01'},
                {'coord': 'androidx.test:runner', 'version': '1.7.0-rc01'}]  # declared in two places
        self.assertEqual(releases.androidx_pins_line(pins),
                         'ANDROIDX PINS (2): no GitHub releases; their release notes are at '
                         'https://developer.android.com/jetpack/androidx/releases/<group> — '
                         'androidx.biometric:biometric-ktx 1.2.0-alpha05, androidx.test:runner 1.7.0-rc01')

    def test_pre_release_stages_and_their_order(self):
        pre = ['1.2.0-alpha05', '2.0.0-RC1', '1.0.0-M3', '3.0-alpha-9', '2.0.0-alpha.6', '1.0-dev01',
               '1.0-SNAPSHOT', '0.8.0-rc02-0.6.x-compat']
        variants = ['1.0-sources', '1.0-force', '2.0-devtools', '1.2.0-macos-arm64', '3.1.0-fork-1',
                    '0.7.1-0.6.x-compat', '33.0-jre']
        self.assertEqual([v for v in pre if not releases.is_pre_release(v)], [])
        self.assertEqual([v for v in variants if releases.is_pre_release(v)], [])
        order = ['1.0.0-dev01', '1.0.0-alpha01', '1.0.0-alpha02', '1.0.0-beta01', '1.0.0-rc01', '1.0.0']
        self.assertEqual(sorted(reversed(order), key=releases.release_key), order)

    def test_deps_dev_unknown_package_falls_back(self):
        original = releases.http
        releases.http = lambda url: (_ for _ in ()).throw(RuntimeError('The requested URL returned error: 404'))
        try:
            self.assertIsNone(releases.deps_dev('com.example:private'))
        finally:
            releases.http = original

    def test_stopped_artifacts(self):
        published = {
            # biometric-ktx stopped; biometric went on with builds the -ktx artifact never got
            'androidx.biometric:biometric-ktx': [('1.2.0-alpha05', '2024-02-14'), ('1.4.0-alpha01', '2024-05-29'),
                                                 ('1.4.0-alpha02', '2024-08-07')],
            'androidx.biometric:biometric': [('1.4.0-alpha02', '2024-08-07'), ('1.4.0-beta01', '2026-10-01')],
            # core-ktx is released with core: no line
            'androidx.core:core-ktx': [('1.19.1', '2026-09-23')],
            'androidx.core:core': [('1.19.1', '2026-09-23')],
            'org.example:dormant-lib': [('4.7.1', '2018-06-28'), ('5.0.1', '2021-08-13')],
            # deps.dev dates an old build by when it indexed it (1.0 in 2024): the newest version
            # shown is 2.2, and the 2024 date still counts as its last build
            'com.android.installreferrer:installreferrer': [('2.2', '2020-11-10'), ('1.0', '2024-02-14')],
            'g:just-crossed': [('1.0-alpha-1', '2024-01-09'), ('1.0-alpha-8', '2024-11-01')],
            # A 2022 release candidate outnumbers the living 4.x line: not dormant
            'com.google.protobuf:protoc': [('21.0-rc-1', '2022-05-11'), ('4.29.2', '2024-12-18'),
                                           ('4.36.2', '2026-09-17')],
            # The stable line ends in 2024, the 3.0 alphas go on: not dormant
            'com.squareup.leakcanary:leakcanary-android': [('2.14', '2024-04-18'), ('3.0-alpha-8', '2024-06-04'),
                                                           ('3.0-alpha-9', '2026-06-24')],
        }
        moved, dormant = releases.stopped_artifacts(
            ['androidx.biometric:biometric-ktx', 'androidx.core:core-ktx', 'org.example:dormant-lib',
             'com.android.installreferrer:installreferrer', 'g:just-crossed', 'com.google.protobuf:protoc',
             'com.squareup.leakcanary:leakcanary-android'],
            published, common.datetime.date(2026, 11, 4), '2026-10-27')
        self.assertEqual(moved, ['MOVED ON WITHOUT IT: androidx.biometric:biometric-ktx has no build after '
                                 '1.4.0-alpha02 (2024-08-07); androidx.biometric:biometric has 1.4.0-beta01 '
                                 '(2026-10-01) — its release notes say where the API went'])
        self.assertEqual(dormant, ['org.example:dormant-lib 5.0.1 (last build 2021-08-13)',
                                   'com.android.installreferrer:installreferrer 2.2 (last build 2024-02-14)',
                                   'g:just-crossed 1.0-alpha-8 (last build 2024-11-01) NEW'])

    def test_new_starts_at_the_previous_run_or_the_first_run_window(self):
        repo = fixtures.FakeRepo()
        try:
            self.assertEqual(releases.window_start(repo.root), '2026-10-10')
            os.remove(os.path.join(repo.root, 'upstream-audit/runs/2026-10-10-1000.json'))
            first = common.today() - common.datetime.timedelta(days=common.FIRST_RUN_DAYS)
            self.assertEqual(releases.window_start(repo.root), first.isoformat())
        finally:
            repo.close()

    def test_a_dormant_artifact_whose_repository_still_releases_moved_on(self):
        # Coil 2 stopped at 2.7.0 because Coil 3 moved to io.coil-kt.coil3: same repository.
        repos = {'io.coil-kt:coil': 'coil-kt/coil', 'org.example:dead': 'example/dead',
                 'org.example:no-repo': None, 'org.example:broken': 'example/broken'}
        latest = {'coil-kt/coil': {'tag_name': '3.6.3', 'published_at': '2026-09-18T10:00:00Z'},
                  'example/dead': {'tag_name': 'v1.0', 'published_at': '2021-01-01T00:00:00Z'}}

        def github(path, client):
            repo = path[len('repos/'):-len('/releases/latest')]
            if repo == 'example/broken':
                raise RuntimeError('HTTP 502')
            return latest[repo]
        old_repo, old_github = releases.deps_dev_repo, releases.github
        releases.deps_dev_repo = lambda coord, version: repos[coord]
        releases.github = github
        try:
            lines, ok, failures = releases.repos_moved_on(
                ['io.coil-kt:coil 2.7.0 (2024-07-17)', 'org.example:dead 1.0 (2021-01-01)',
                 'org.example:no-repo 1.0 (2020-01-01)', 'org.example:broken 1.0 (2020-01-01) NEW'],
                'gh', common.datetime.date(2026, 11, 13))
        finally:
            releases.deps_dev_repo, releases.github = old_repo, old_github
        self.assertEqual(lines, ['UPSTREAM MOVED ON: io.coil-kt:coil stops at 2.7.0, but coil-kt/coil released 3.6.3 '
                                 '(2026-09-18) — likely under other coordinates; its release notes or migration '
                                 'guide say what replaces ours'])
        self.assertEqual(failures, ['source repository of org.example:broken: HTTP 502'])
        self.assertEqual(ok, 6)

    def test_a_failed_link_lookup_is_a_failure_not_a_missing_repository(self):
        def http(url):
            if 'broken' in url:
                raise RuntimeError('curl: (28) Operation timed out')
            if 'unlinked' in url:
                raise RuntimeError('The requested URL returned error: 404')
            return json.dumps({'links': [{'label': 'SOURCE_REPO', 'url': 'https://github.com/o/r'}]})
        pins = [{'coord': f'g:{name}', 'version': '1.0-alpha01'} for name in ('broken', 'unlinked', 'linked')]
        original = releases.http
        releases.http = http
        try:
            linked, ok, failures = releases.pin_repositories(pins, {})
        finally:
            releases.http = original
        self.assertEqual([(e['coord'], repo) for e, repo in linked], [('g:unlinked', None), ('g:linked', 'o/r')])
        self.assertEqual(ok, 2)
        self.assertEqual(failures, ['deps.dev links g:broken: curl: (28) Operation timed out'])

    def test_an_unreachable_repository_is_not_a_missing_artifact(self):
        def http(url):
            if 'down.example' in url:
                raise RuntimeError('curl: (28) Operation timed out')
            raise RuntimeError('The requested URL returned error: 404')
        originals = releases.http, releases.deps_dev
        releases.http, releases.deps_dev = http, lambda coord: None
        try:
            _, _, _, down = releases.fetch_versions(['g:a'], ['https://down.example', 'https://empty.example'], [])
            _, _, _, empty = releases.fetch_versions(['g:a'], ['https://empty.example'], [])
        finally:
            releases.http, releases.deps_dev = originals
        self.assertEqual(down, ['maven-metadata.xml g:a: not on deps.dev; could not read maven-metadata.xml from '
                                'https://down.example: curl: (28) Operation timed out — run again later'])
        self.assertEqual(empty, ['maven-metadata.xml g:a: not on deps.dev nor in the repositories without credentials'])

    def test_every_failed_call_is_named(self):
        def deps_dev(coord):
            if coord == 'g:broken':
                raise RuntimeError('curl: (28) timed out')
            return None if coord == 'g:private' else [('1.0', '2026-01-01')]
        originals = releases.deps_dev, releases.maven_metadata
        releases.deps_dev, releases.maven_metadata = deps_dev, lambda coord, repos: (None, None, [])
        try:
            published, sources, ok, failures = releases.fetch_versions(
                ['g:broken', 'g:fine', 'g:private'], ['https://repo.example'], ['https://secret.example'])
        finally:
            releases.deps_dev, releases.maven_metadata = originals
        self.assertEqual(ok, 2)
        self.assertEqual(failures, [
            'deps.dev g:broken: curl: (28) timed out',
            'maven-metadata.xml g:private: not on deps.dev nor in the repositories without credentials'
            ' — not asked, need credentials: https://secret.example'])

    def test_github_through_curl_without_gh(self):
        calls = []
        original = common.curl
        common.curl = lambda url, path=None, headers=None: calls.append((url, headers)) or '[]'
        try:
            self.assertEqual(releases.github('repos/google/ksp/releases?per_page=5', 'curl'), [])
        finally:
            common.curl = original
        self.assertEqual(calls[0][0], 'https://api.github.com/repos/google/ksp/releases?per_page=5')
        self.assertIn('Accept: application/vnd.github+json', calls[0][1])

    def test_gh_is_used_only_when_it_is_logged_in(self):
        calls = []

        def auth_status(returncode):
            def run(cmd, **kwargs):
                calls.append((cmd, kwargs))
                if returncode is None:
                    raise subprocess.TimeoutExpired(cmd, kwargs.get('timeout'))
                return subprocess.CompletedProcess(cmd, returncode)
            return run

        with mock.patch.object(releases.shutil, 'which', return_value=None):
            client, why = releases.github_client()
        self.assertEqual((client, calls), ('curl', []))
        self.assertIn('gh is not installed', why)

        for returncode, expected, reason in [(0, 'gh', None), (1, 'curl', 'gh is installed but not logged in'),
                                             (None, 'curl', '`gh auth status` failed')]:
            with self.subTest(returncode=returncode):
                calls.clear()
                with mock.patch.object(releases.shutil, 'which', return_value='/usr/bin/gh'), \
                        mock.patch.object(releases.subprocess, 'run', auth_status(returncode)):
                    client, why = releases.github_client()
                self.assertEqual(client, expected)
                if reason:
                    self.assertIn(reason, why)
                    self.assertIn('60 requests an hour', why)
                else:
                    self.assertIsNone(why)
                cmd, kwargs = calls[0]
                self.assertEqual(cmd, ['gh', 'auth', 'status', '--hostname', 'github.com'])
                # Its output names the account and a masked token: never read, never printed.
                self.assertEqual((kwargs['stdout'], kwargs['stderr']), (subprocess.DEVNULL, subprocess.DEVNULL))

    def test_curl_sends_no_authorization_even_with_github_token(self):
        calls = []
        fake_curl = lambda url, path=None, headers=None: calls.append(headers) or '[]'  # noqa: E731
        with mock.patch.object(common, 'curl', fake_curl), \
                mock.patch.dict(os.environ, {'GITHUB_TOKEN': 'test-token'}):
            releases.github('repos/google/ksp/releases?per_page=5', 'curl')
        self.assertEqual(calls, [['Accept: application/vnd.github+json']])


if __name__ == '__main__':
    unittest.main()
