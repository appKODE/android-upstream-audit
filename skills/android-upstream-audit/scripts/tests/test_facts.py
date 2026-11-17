"""facts.py and the init script: raw facts, the JDK, the Gradle run."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.parse
import zipfile

import fixtures  # noqa: F401  (puts the scripts on sys.path)
import common  # noqa: E402
import facts  # noqa: E402
from fixtures import SCRIPTS, build_facts  # noqa: E402


class Facts(unittest.TestCase):
    """facts.py on raw init-script output written by hand: no Gradle needed."""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        # A space in the checkout's path: Gradle gives a local repository as a URL with %20.
        self.top = os.path.join(os.path.realpath(d.name), 'My App')
        self.raw = os.path.join(self.top, 'raw')
        for rel_path in ('gradle/libs.versions.toml', 'gradle.properties', 'libs/m2/org/fork/lib/maven-metadata.xml',
                         'app/libs/sdk.jar', 'lib/wrapper/sdk.aar'):
            path = os.path.join(self.top, rel_path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, 'w').close()

    def path(self, rel_path):
        return os.path.join(self.top, rel_path)

    def write_raw(self, build, data):
        os.makedirs(self.raw, exist_ok=True)
        with open(os.path.join(self.raw, f'facts-{build}.json'), 'w') as f:
            json.dump(dict({'build': build, 'errors': [], 'projects': []}, **data), f)

    def project(self, path, folder, **extra):
        return dict({'path': path, 'dir': self.path(folder), 'buildFile': self.path(f'{folder}/build.gradle'),
                     'plugins': [], 'dependencies': [], 'localFiles': [], 'repositories': []}, **extra)

    def root_build(self, **changes):
        data = {
            'dir': self.top, 'gradleVersion': '9.5.0', 'agpVersion': '9.2.1', 'kgpVersion': '2.3.0',
            'includedBuilds': [{'name': 'build-logic', 'dir': self.path('build-logic')}],
            'properties': {'android.builtInKotlin': 'false'},
            'settings': {'settingsFile': self.path('settings.gradle'), 'errors': [],
                         'pluginRepositories': [{'name': 'Gradle Central Plugin Repository', 'url': 'https://plugins.gradle.org/m2'}],
                         'repositories': [{'name': 'maven', 'url': 'https://secret.example', 'credentials': True},
                                          {'name': 'maven2', 'url': 'file:' + urllib.parse.quote(self.path('libs/m2')) + '/'}]},
            'catalogs': [{'name': 'libs', 'versions': {}, 'libraries': [], 'plugins': []}],
            'projects': [
                self.project(':', '.'),
                self.project(':app', 'app', plugins=['com.android.application'],
                             android={'compileSdk': '37', 'minSdk': '26', 'targetSdk': '36', 'javaTarget': '17',
                                      'kotlinOptionsJvmTarget': '17', 'namespace': 'a.b',
                                      'manifest': self.path('app/src/main/AndroidManifest.xml')},
                             dependencies=[{'module': 'com.squareup.okhttp3:okhttp', 'version': '5.5.0'}],
                             localFiles=[self.path('app/libs/sdk.jar')]),
                self.project(':lib:wrapper', 'lib/wrapper', localFiles=[self.path('lib/wrapper/sdk.aar')]),
                self.project(':feature', 'feature', plugins=['com.android.library'],
                             android={'compileSdk': '37', 'minSdk': '29'},
                             dependencies=[{'module': 'com.squareup.okhttp3:okhttp', 'version': '5.5.0'}]),
            ],
        }
        data.update(changes)
        self.write_raw('root', data)

    def build_logic(self, **changes):
        self.write_raw('build-logic', dict({
            'dir': self.path('build-logic'),
            'projects': [self.project(':', 'build-logic', plugins=['java-gradle-plugin'],
                                      pluginSources=[self.path('build-logic/src/main/kotlin/AppPlugin.kt')])],
        }, **changes))

    def test_raw_facts_reduce_to_what_a_run_reads(self):
        self.root_build()
        self.build_logic()
        f = facts.normalize(self.raw, self.top)
        self.assertEqual(f['versions'], {'gradle': '9.5.0', 'agp': '9.2.1', 'kgp': '2.3.0'})
        self.assertEqual(f['kind'], 'application')
        app = f['app_modules'][0]
        self.assertEqual((app['project'], app['manifest'], app['targetSdk'], app['jvmTarget']),
                         (':app', 'app/src/main/AndroidManifest.xml', '36', '17'))
        self.assertEqual(f['sdk_levels']['minSdk']['counts'], {'26': 1, '29': 1})
        self.assertEqual(f['dependencies'], [{'module': 'com.squareup.okhttp3:okhttp', 'version': '5.5.0',
                                              'declared_in': 2, 'examples': [':app', ':feature'], 'builds': ['root'],
                                              'files': ['app/build.gradle', 'feature/build.gradle']}])
        self.assertEqual([(v['path'], v['used_by']) for v in f['vendored']],
                         [('app/libs', [':app']), ('lib/wrapper', [':lib:wrapper']), ('libs/m2/org/fork/lib', [])])
        self.assertEqual(f['vendored'][-1]['reason'], 'local Maven repository libs/m2')
        self.assertEqual({r['url']: r['credentials'] for r in f['repositories']}['https://secret.example'], True)
        self.assertEqual(f['plugin_sources'], ['build-logic/src/main/kotlin/AppPlugin.kt'])
        self.assertEqual(f['catalog_files'], ['gradle/libs.versions.toml'])
        self.assertIn('gradle.properties', f['build_files'])
        self.assertEqual((f['problems'], f['version_warnings']), ([], []))

    def test_every_flavor_combination_gets_its_own_sdk_levels(self):
        # A flavor overrides defaultConfig; with two dimensions the first that sets a value wins.
        android = {'minSdk': '26', 'targetSdk': '36', 'flavorDimensions': ['env', 'tier'], 'productFlavors': [
            {'name': 'prod', 'dimension': 'env', 'minSdk': None, 'targetSdk': '35'},
            {'name': 'demo', 'dimension': 'env', 'minSdk': None, 'targetSdk': None},
            {'name': 'paid', 'dimension': 'tier', 'minSdk': '28', 'targetSdk': '34'},
            {'name': 'free', 'dimension': 'tier', 'minSdk': None, 'targetSdk': None}]}
        self.assertEqual([(v['variant'], v['minSdk'], v['targetSdk']) for v in facts.variant_sdk(android)], [
            ('prodPaid', '28', '35'), ('prodFree', '26', '35'), ('demoPaid', '28', '34'), ('demoFree', '26', '36')])
        self.root_build(projects=[self.project(':app', 'app', plugins=['com.android.application'], android={
            'compileSdk': '37', 'minSdk': '26', 'targetSdk': '36', 'flavorDimensions': [],
            'productFlavors': [{'name': 'prod', 'dimension': None, 'minSdk': None, 'targetSdk': '35'},
                               {'name': 'demo', 'dimension': None, 'minSdk': None, 'targetSdk': None}]})])
        f = facts.normalize(self.raw, self.top)
        self.assertIn('by variant (product flavors override defaultConfig): '
                      'minSdk 26, targetSdk 35: prod; minSdk 26, targetSdk 36: demo', facts.summary(f))
        self.assertEqual(facts.snapshot(f)['app_modules'][':app']['variants'],
                         'minSdk 26, targetSdk 35: prod; minSdk 26, targetSdk 36: demo')
        # Without flavors the snapshot has no "variants" key, as the slices written before it.
        self.assertNotIn('variants', facts.snapshot(build_facts())['app_modules'][':app'])

    def test_snapshot_keeps_every_version_of_every_declaring_build(self):
        # Two modules of the root build pin g:a apart, and an included build declares it too.
        def snap(app_version):
            self.root_build(projects=[
                self.project(':app', 'app', dependencies=[{'module': 'g:a', 'version': app_version}]),
                self.project(':legacy', 'legacy', dependencies=[{'module': 'g:a', 'version': '2.0'}])])
            self.build_logic(projects=[self.project(':', 'build-logic', dependencies=[
                {'module': 'g:a', 'version': '2.0'}])])
            return facts.snapshot(facts.normalize(self.raw, self.top))
        before, after = snap('1.0'), snap('1.1')
        self.assertEqual({k: v for k, v in before['dependencies'].items() if k.startswith('g:a@')},
                         {'g:a@root': '1.0, 2.0', 'g:a@build-logic': '2.0'})
        self.assertIn('dependency changed: g:a in root 1.0, 2.0 → 1.1, 2.0', facts.changes(before, after))

    def test_dependencies_a_plugin_adds_are_kept_apart(self):
        self.root_build(projects=[self.project(':app', 'app', plugins=['com.android.application'], android={
            'compileSdk': '37'}, dependencies=[
                {'module': 'com.squareup.okhttp3:okhttp', 'version': '5.5.0', 'declared': True},
                # Only in a configuration the Kotlin plugin resolves itself.
                {'module': 'org.jetbrains.kotlin:kotlin-build-tools-impl', 'version': None, 'declared': False},
                # In "api", but versioned exactly like AGP: it moves with AGP.
                {'module': 'androidx.databinding:databinding-runtime', 'version': '9.2.1', 'declared': True},
                {'module': 'org.jetbrains.kotlin:kotlin-parcelize-runtime', 'version': '2.3.0', 'declared': True}])])
        self.build_logic()
        f = facts.normalize(self.raw, self.top)
        self.assertEqual([d['module'] for d in f['dependencies']], ['com.squareup.okhttp3:okhttp'])
        self.assertEqual([d['module'] for d in f['plugin_added']],
                         ['androidx.databinding:databinding-runtime', 'org.jetbrains.kotlin:kotlin-build-tools-impl',
                          'org.jetbrains.kotlin:kotlin-parcelize-runtime'])
        self.assertEqual([(b['name'], b['kgp']) for b in f['builds']], [('build-logic', None), ('root', '2.3.0')])

    def test_an_included_build_keeps_its_build_file_among_many_root_modules(self):
        dep = [{'module': 'org.testng:testng', 'version': '6.10', 'declared': True}]
        self.root_build(projects=[self.project(f':m{i}', f'm{i}', dependencies=dep) for i in range(5)])
        self.build_logic(projects=[self.project(':', 'build-logic', dependencies=dep)])
        files = facts.normalize(self.raw, self.top)['dependencies'][0]['files']
        self.assertEqual(files, ['build-logic/build.gradle', 'm0/build.gradle', 'm1/build.gradle', 'm2/build.gradle'])

    def test_an_excluded_module_or_file_counts_nowhere(self):
        self.root_build()
        self.build_logic()
        f = facts.normalize(self.raw, self.top, {'inputs': {
            'exclude': ['app', 'libs/m2', 'gradle/libs.versions.toml'], 'include': ['gradle.properties', 'none.gradle']}})
        self.assertEqual((f['app_modules'], f['kind']), ([], 'library'))
        self.assertEqual([v['path'] for v in f['vendored']], ['lib/wrapper'])
        self.assertEqual((f['catalogs'], f['catalog_files']), ([], []))
        self.assertEqual(f['dependencies'][0]['examples'], [':feature'])
        self.assertIn('gradle.properties', f['key_files'])
        self.assertNotIn('none.gradle', f['key_files'])

    def test_what_could_not_be_read_is_reported(self):
        self.root_build(gradleVersion='10.1', errors=[':app namespace: MissingPropertyException: x'],
                        settings={'settingsFile': self.path('settings.gradle'), 'errors': [],
                                  'repositories': [{'name': 'maven', 'url': 'file:/no/such/m2/'}]},
                        projects=[self.project(':', '.'),
                                  self.project(':lib', 'lib', plugins=['com.android.library'], android={'minSdk': '26'})])
        f = facts.normalize(self.raw, self.top)
        self.assertEqual(f['kind'], 'library')
        self.assertEqual(f['version_warnings'],
                         ['GRADLE 10.1 is newer than the 9.x this skill was tested with — the facts may be incomplete'])
        self.assertEqual(f['problems'], ['1 values the init script could not read (see "errors")',
                                         'included build build-logic was not configured — its facts are missing',
                                         ':lib: an Android module without compileSdk',
                                         'local Maven repository file:/no/such/m2/ not found at /no/such/m2/ — '
                                         'its artifacts are not checked'])
        self.assertIn('no application module', facts.summary(f))

    def test_android_sdk_only_when_the_project_names_none(self):
        studio = self.path('studio/sdk')
        os.makedirs(studio)
        saved = {k: os.environ.pop(k, None) for k in ('ANDROID_HOME', 'ANDROID_SDK_ROOT')}
        original_sdks = facts.STUDIO_SDKS
        facts.STUDIO_SDKS = [self.path('missing'), studio]
        try:
            # A fresh clone: no variable, no local.properties.
            self.assertEqual(facts.android_sdk(self.top), studio)
            os.environ['ANDROID_HOME'] = '/some/sdk'
            self.assertIsNone(facts.android_sdk(self.top))
            del os.environ['ANDROID_HOME']
            open(self.path('local.properties'), 'w').close()
            self.assertIsNone(facts.android_sdk(self.top))
        finally:
            facts.STUDIO_SDKS = original_sdks
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_java_home_or_a_stop_that_names_studio_jdk(self):
        studio = self.path('studio/jbr')
        os.makedirs(studio)
        original_env, original_jdks = os.environ.get('JAVA_HOME'), facts.STUDIO_JDKS
        facts.STUDIO_JDKS = [self.path('missing'), studio]
        try:
            os.environ.pop('JAVA_HOME', None)
            with self.assertRaises(SystemExit) as e:
                facts.java_home()
            self.assertIn(f'export JAVA_HOME="{studio}"', str(e.exception))
            os.environ['JAVA_HOME'] = '/some/jdk'
            self.assertEqual(facts.java_home(), '/some/jdk')
        finally:
            facts.STUDIO_JDKS = original_jdks
            if original_env is None:
                os.environ.pop('JAVA_HOME', None)
            else:
                os.environ['JAVA_HOME'] = original_env

    def fake_gradlew(self, script):
        path = self.path('gradlew')
        with open(path, 'w') as f:
            f.write('#!/bin/sh\n' + script)
        os.chmod(path, 0o755)
        return path

    def test_gradle_output_goes_to_a_log_while_it_runs(self):
        gradlew = self.fake_gradlew('echo "args: $*"\necho "upstream-audit: root: configured 50 of 120 projects"\n')
        log = self.path('gradle.log')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            facts.run_gradle(self.top, gradlew, self.raw, '/jdk', log)
        with open(log) as f:
            text = f.read()
        self.assertIn('configured 50 of 120 projects', text)
        # Gradle refuses --no-configuration-cache in a project that turns isolated projects on.
        self.assertIn('--no-configuration-cache -Dorg.gradle.isolated-projects=false', text)
        self.assertIn(f'tail -f {log}', out.getvalue())
        self.assertIn(': gradlew upstreamAuditFacts -q --stacktrace', out.getvalue())
        self.assertIn('Configuration only.', out.getvalue())
        self.assertRegex(out.getvalue(), r'Gradle configured the build in \d+ s')

    def test_a_build_that_does_not_configure_stops_with_a_hint(self):
        gradlew = self.fake_gradlew(
            'echo "WARNING: noise"\n'
            'printf "FAILURE: Build failed with an exception.\\n\\n* Where:\\nBuild file app/build.gradle line: 3\\n'
            '\\n* What went wrong:\\nSDK location not found. Define a valid SDK location\\n\\n* Try:\\n> Run with --info\\n'
            '\\n* Exception is:\\norg.gradle.api.GradleException: SDK location not found\\n'
            '\\tat com.android.SdkLocator.locate(SdkLocator.kt:10)\\n" >&2\nexit 1\n')
        log = self.path('gradle.log')
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as e:
            facts.run_gradle(self.top, gradlew, self.raw, '/jdk', log)
        text = str(e.exception)
        self.assertIn('Build file app/build.gradle line: 3', text)
        self.assertIn('SDK location not found. Define a valid SDK location', text)
        self.assertIn('Likely fix: set ANDROID_HOME to the Android SDK', text)
        self.assertNotIn('WARNING', text)
        self.assertNotIn('SdkLocator.locate', text)
        self.assertIn(f'Full output, with the stack trace: {log}', text)
        with open(log) as f:
            self.assertIn('SdkLocator.locate', f.read())

def gradle_launcher():
    """Gradle for the test build: $GRADLE, else the gradlew of the Gradle build that holds this
    skill (its pinned Gradle version), else `gradle` on PATH. None when there is no Gradle."""
    if os.environ.get('GRADLE'):
        return os.environ['GRADLE']
    try:
        gradlew = os.path.join(common.repo_root(SCRIPTS), 'gradlew')
        if os.path.isfile(gradlew):
            return gradlew
    except SystemExit:  # not inside a Gradle build
        pass
    return shutil.which('gradle')


@unittest.skipUnless(os.environ.get('RUN_GRADLE_TESTS'),
                     'runs Gradle (~20 s, needs a JDK and Gradle): RUN_GRADLE_TESTS=1 after changing '
                     'audit-facts.init.gradle')
class GradleFacts(unittest.TestCase):
    """The init script on a real, tiny Gradle build: an included build of convention plugins, a
    catalog, buildscript classpath dependencies, a jar in a fileTree and a module that only wraps
    an AAR."""

    def test_init_script_on_a_tiny_build(self):
        gradlew = gradle_launcher()
        if not gradlew:  # asked to run Gradle: a skip here would pass without testing anything
            self.fail('no Gradle: put gradle on PATH or set GRADLE to its path')
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        top = os.path.realpath(d.name)
        files = {
            'settings.gradle': "pluginManagement { includeBuild 'conventions' }\n"
                               "buildscript { repositories { maven { url uri('repo'); metadataSources { artifact() } } }\n"
                               "  dependencies { classpath 'org.example:settings-tool:2.0' } }\n"
                               "rootProject.name = 'tiny'\n"
                               "include ':app', ':wrapper'\n",
            'gradle/libs.versions.toml': '[versions]\nokhttp = "5.5.0"\n[libraries]\n'
                                         'okhttp = { module = "com.squareup.okhttp3:okhttp", version.ref = "okhttp" }\n',
            'build.gradle': "buildscript { repositories { maven { url uri('repo'); metadataSources { artifact() } } }\n"
                            "  dependencies { classpath 'org.example:tool:1.0' } }\n",
            'app/build.gradle': "plugins { id 'java-library'; id 'demo-convention' }\n"
                                "dependencies { implementation libs.okhttp\n"
                                "  implementation fileTree(dir: 'libs', include: ['*.jar']) }\n",
            'app/libs/sdk.jar': '',
            'wrapper/build.gradle': 'configurations.maybeCreate("default")\nartifacts.add("default", file("sdk.aar"))\n',
            'wrapper/sdk.aar': '',
            'conventions/settings.gradle': "rootProject.name = 'conventions'\n",
            'conventions/build.gradle': "plugins { id 'groovy-gradle-plugin' }\n",
            'conventions/src/main/groovy/demo-convention.gradle': '// applies nothing\n',
        }
        for rel_path, text in files.items():
            path = os.path.join(top, rel_path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write(text)
        # A local repository, so the build script classpath resolves without the network.
        for name, version in (('tool', '1.0'), ('settings-tool', '2.0')):
            jar = os.path.join(top, 'repo', 'org', 'example', name, version, f'{name}-{version}.jar')
            os.makedirs(os.path.dirname(jar))
            zipfile.ZipFile(jar, 'w').close()
        f = facts.collect(top, os.path.join(top, 'raw'), gradlew=gradlew)
        with open(os.path.join(top, 'raw.log')) as log:
            progress = [line for line in log.read().split('\n') if line.startswith('upstream-audit: ')]
        self.assertIn('upstream-audit: configuring build conventions', progress)
        self.assertIn('upstream-audit: root: configured 3 of 3 projects', progress)
        self.assertEqual(f['errors'], [])
        self.assertEqual(sorted(b['name'] for b in f['builds']), ['conventions', 'root'])
        # buildscript { classpath } of a build script and of the settings script, with the file
        # that writes each one: releases.py checks them only when they are listed here.
        self.assertEqual([(d['module'], d['version'], d['files']) for d in f['dependencies']],
                         [('com.squareup.okhttp3:okhttp', '5.5.0', ['app/build.gradle']),
                          ('org.example:settings-tool', '2.0', ['settings.gradle']),
                          ('org.example:tool', '1.0', ['build.gradle'])])
        self.assertEqual([v['path'] for v in f['vendored']], ['app/libs', 'wrapper'])
        self.assertEqual(f['plugin_sources'], ['conventions/src/main/groovy/demo-convention.gradle'])
        self.assertEqual(f['kind'], 'library')


if __name__ == '__main__':
    unittest.main()
