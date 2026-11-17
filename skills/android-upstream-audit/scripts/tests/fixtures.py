"""What the android-upstream-audit tests share: a fake repository for slice.py, a facts.json
and a finding as the scripts write them.

Run every test from the repository root:

    python3 -m unittest discover skills/android-upstream-audit/scripts/tests

Standard library only, no network, about five seconds. After changing
audit-facts.init.gradle, also run them with RUN_GRADLE_TESTS=1, JAVA_HOME set and Gradle on PATH
(or GRADLE=<path to gradle>): one test runs Gradle on a tiny build.
"""
import json
import os
import subprocess
import sys
import tempfile

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

KEY = 'build/libs.versions.toml/ksp-test'
URL = 'https://example.test/matrix'
CATALOG = '[versions]\nksp = "2.3.9"\nforkedProcessor = "1.2.0-fork-1"\nbiometric = "1.2.0-alpha05"\n'
# A source whose quote is on the page every FakeRepo saves.
SETTINGS = "dependencyResolutionManagement { repositories { maven { url uri('libs/m2') } } }\n"
CONFIG = {'schema': 2, 'base_branch': 'dev', 'releases': ['google/ksp'], 'commits': [],
          'vendored': {'libs/m2/org/example/forked-processor': {'github': 'example/forked-processor'}},
          'inputs': {'include': [], 'exclude': []}, 'resolved_versions': False}
KEY_FILES = ['gradle/libs.versions.toml', 'settings.gradle']


def build_facts(**changes):
    """A facts.json as facts.py writes it, for a project like FakeRepo's."""
    data = {
        'schema': 1, 'versions': {'gradle': '9.5.0', 'agp': '9.2.1', 'kgp': '2.3.0'}, 'kind': 'application',
        'builds': [{'name': 'root', 'dir': '.', 'agp': '9.2.1', 'kgp': '2.3.0'}],
        'properties': {'android.builtInKotlin': 'false'},
        'app_modules': [{'project': ':app', 'compileSdk': '37', 'minSdk': '26', 'targetSdk': '36',
                         'javaTarget': '17', 'jvmTarget': '17', 'manifest': 'app/src/main/AndroidManifest.xml'}],
        'android_modules': 1, 'sdk_levels': {'compileSdk': {'counts': {'37': 1}, 'other_than_most': []}},
        'catalogs': [], 'catalog_files': ['gradle/libs.versions.toml'],
        'dependencies': [{'module': 'com.google.devtools.ksp:symbol-processing-gradle-plugin', 'version': '2.3.9',
                          'declared_in': 1, 'examples': [':app'], 'files': []}],
        'plugin_added': [], 'vendored': [{'path': 'libs/m2/org/example/forked-processor', 'reason': 'local Maven',
                                          'files': [], 'used_by': []}],
        'repositories': [], 'plugin_sources': [], 'build_files': KEY_FILES, 'module_build_files': {},
        'version_warnings': [], 'problems': [], 'errors': [], 'key_files': KEY_FILES,
    }
    data.update(changes)
    return data


SAVED_SOURCE = {'url': URL, 'read': '2026-10-12', 'claim': '2.4.20–2.4.21 | 7.6.3–9.7.0'}


def finding(**changes):
    f = {'key': KEY, 'subject': 'KSP', 'title': 'KSP must move', 'status': 'unchanged',
         'severity': 'high', 'deadline': None, 'section': 'requires-work', 'areas': ['kotlin'],
         'anchors': [{'file': 'gradle/libs.versions.toml', 'line': 2, 'excerpt': 'ksp = "2.3.9"'}],
         'sources': [{'url': URL, 'published': '2026-10-01', 'claim': 'old quote'}],
         'blast_radius': 'none', 'effort': 'one-line', 'api_stability': None,
         'last_verified': '2026-10-10', 'recommendation': 'Move KSP with Kotlin.'}
    f.update(changes)
    return f


class FakeRepo:
    """A temporary repository with one previous slice, ready for slice.py."""

    def __init__(self, findings=None, page_text='2.4.20–2.4.21 | 7.6.3–9.7.0 | 8.5.2–9.3.1\n'):
        self.dir = tempfile.TemporaryDirectory()
        self.root = self.dir.name
        self.scratch = os.path.join(self.root, 'scratch')
        self.write('settings.gradle', SETTINGS)
        self.write('gradle/libs.versions.toml', CATALOG)
        self.write('libs/m2/org/example/forked-processor/maven-metadata.xml', '<metadata/>')
        self.write('upstream-audit/decisions.txt', '# none\n')
        self.write_json('upstream-audit/runs/2026-10-10-1000.json', {
            'schema': 5, 'audit': 'upstream',
            'run': {'started': '2026-10-10T10:00:00Z', 'dev_base': None},
            'quality': {}, 'findings': findings if findings is not None else [finding()],
            'dropped': [], 'verified_good': [{'key': 'good/one', 'title': 'Something checked'}],
            'closed': [], 'checked': [], 'suppressed': []})
        self.write_json('upstream-audit/config.json', CONFIG)
        os.makedirs(self.scratch)
        with open(os.path.join(self.scratch, 'feeds.json'), 'w') as f:
            json.dump({'gaps': [], 'stale': []}, f)
        self.releases_json({'github_client': 'gh', 'not_checked': []})
        self.facts_json(build_facts())
        self.resolved_json({'status': 'off', 'source': 'config.json'})
        os.makedirs(os.path.join(self.scratch, 'pages'))
        with open(os.path.join(self.scratch, 'pages', 'p1.txt'), 'w') as f:
            f.write(page_text)
        with open(os.path.join(self.scratch, 'pages', 'index.json'), 'w') as f:
            json.dump({URL: {'status': 'ok', 'file': 'p1.txt'}}, f)

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)

    def write_json(self, rel, data):
        self.write(rel, json.dumps(data))

    def git(self, *args):
        return subprocess.run(['git', '-C', self.root, '-c', 'user.name=t', '-c', 'user.email=t@t',
                               '-c', 'commit.gpgsign=false', *args],
                              capture_output=True, text=True, check=True).stdout.strip()

    def releases_json(self, data):
        with open(os.path.join(self.scratch, 'releases.json'), 'w') as f:
            json.dump(data, f)

    def resolved_json(self, data):
        with open(os.path.join(self.scratch, 'resolved.json'), 'w') as f:
            json.dump(data, f)

    def facts_json(self, data):
        with open(os.path.join(self.scratch, 'facts.json'), 'w') as f:
            json.dump(data, f)

    def inputs(self):
        return KEY_FILES

    def slice(self, edits_data, *extra, started='2026-10-12T10:00:00Z'):
        if isinstance(edits_data, dict) and 'inputs_read' not in edits_data:
            # A run that read everything, unless a test says otherwise.
            edits_data = dict(edits_data, inputs_read=self.inputs())
        path = os.path.join(self.root, 'edits.json')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(edits_data if isinstance(edits_data, str) else json.dumps(edits_data))
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, 'slice.py'), '--started', started,
                            '--model', 'test', '--edits', path, '--scratch', self.scratch, '--show-report', *extra],
                           cwd=self.root, capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def close(self):
        self.dir.cleanup()
