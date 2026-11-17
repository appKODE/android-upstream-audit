"""common.py: JSON, decisions, the repository root and config.json."""
import json
import os
import tempfile
import unittest

import fixtures  # noqa: F401  (puts the scripts on sys.path)
import common  # noqa: E402
import releases  # noqa: E402
from fixtures import CONFIG, KEY, FakeRepo  # noqa: E402


class Common(unittest.TestCase):
    def test_broken_json_names_file_and_line(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            # A missing comma: every Python reports it at the same place. A trailing comma is
            # reported on line 2 by Python 3.13+ and on line 3 by older ones.
            f.write('{\n "a": 1\n "b": 2\n}')
        try:
            with self.assertRaises(SystemExit) as e:
                common.load_json(f.name, 'the edits file')
            self.assertIn('line 3, column 2', str(e.exception))  # the missing comma
            self.assertIn(f.name, str(e.exception))
        finally:
            os.unlink(f.name)

    def test_decisions_latest_date_wins_and_bad_lines_are_kept(self):
        repo = FakeRepo()
        try:
            repo.write('upstream-audit/decisions.txt',
                       f'checked  2026-10-01 {KEY} — first\n'
                       f'declined 2026-10-05 {KEY} — second\n'
                       f'checked 2026-10-06 {KEY} - plain hyphen\n')
            decisions, unparseable = common.read_decisions(repo.root)
            self.assertEqual(decisions[KEY]['status'], 'declined')
            self.assertEqual(len(unparseable), 1)
        finally:
            repo.close()

    def test_root_is_the_outer_kotlin_dsl_build_before_init(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, '.git'))
            os.makedirs(os.path.join(d, 'build-logic', 'src'))
            for rel in ('settings.gradle.kts', 'build-logic/settings.gradle.kts'):
                open(os.path.join(d, rel), 'w').close()
            # No upstream-audit/ yet: --init runs here, and the included build is not the root.
            self.assertEqual(common.repo_root(os.path.join(d, 'build-logic', 'src')), d)

    def test_no_settings_script_stops_with_a_sentence(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, '.git'))
            with self.assertRaises(SystemExit) as e:
                common.repo_root(d)
            self.assertIn('no settings.gradle or settings.gradle.kts', str(e.exception))

class Config(unittest.TestCase):
    def test_errors_are_reported_together(self):
        self.assertEqual(common.config_errors(CONFIG), [])
        errors = common.config_errors({'schema': 2, 'base_branch': '', 'releases': ['not a repo'],
                                       'vendored': {'lib/x': {'watch': False}}, 'extra': 1})
        text = '\n'.join(errors)
        self.assertIn('unknown field "extra"', text)
        self.assertIn('"base_branch" must be a branch name', text)
        self.assertIn('"releases" must be a list of "owner/repo"', text)
        self.assertIn('vendored "lib/x"', text)

    def test_missing_config_stops_a_run(self):
        repo = FakeRepo()
        try:
            os.remove(os.path.join(repo.root, 'upstream-audit/config.json'))
            code, out = repo.slice({}, '--dry-run')
        finally:
            repo.close()
        self.assertEqual(code, 1, out)
        self.assertIn('run /android-upstream-audit --init first', out)

    def gaps(self, config):
        repo = FakeRepo()
        try:
            return releases.config_gaps(repo.root, config, ['libs/m2/org/example/forked-processor'])
        finally:
            repo.close()

    def test_unconfigured_and_stale_vendored(self):
        self.assertEqual(self.gaps(CONFIG), [])
        text = '\n'.join(self.gaps(dict(CONFIG, vendored={'libs/gone': {'github': 'a/b'}})))
        self.assertIn('UNCONFIGURED VENDORED: libs/m2/org/example/forked-processor', text)
        self.assertIn('STALE CONFIG: vendored libs/gone', text)

    def test_config_of_another_schema_stops_with_what_to_do(self):
        repo = FakeRepo()
        try:
            # Schema 0 has no migration: a breaking update.
            for schema, expected in ((common.CONFIG_SCHEMA + 1, 'update the skill'), (0, 'breaking update')):
                with self.subTest(schema=schema):
                    repo.write_json('upstream-audit/config.json', dict(CONFIG, schema=schema, skill_version='9.9.9'))
                    with self.assertRaises(SystemExit) as e:
                        common.load_config(repo.root)
                    self.assertIn(expected, str(e.exception))
            # --init still takes an earlier config as its suggestions.
            self.assertEqual(common.load_config(repo.root, for_init=True)['schema'], 0)
            # Schema 1 had no resolved_versions: it is migrated with the old behaviour, off.
            old = {k: v for k, v in CONFIG.items() if k != 'resolved_versions'}
            repo.write_json('upstream-audit/config.json', dict(old, schema=1))
            config = common.load_config(repo.root)
            self.assertEqual((config['schema'], config['resolved_versions'], config['migrated_from']), (2, False, 1))
            # A slice from a newer skill is refused; an earlier one is read as it is.
            path = os.path.join(repo.root, 'upstream-audit/runs/2026-10-10-1000.json')
            prev = common.load_slice(path)
            repo.write_json('upstream-audit/runs/2026-10-10-1000.json', dict(prev, schema=common.SLICE_SCHEMA + 1))
            with self.assertRaises(SystemExit) as e:
                common.load_slice(path)
            self.assertIn('update the skill', str(e.exception))
        finally:
            repo.close()

    def test_skill_version_from_plugin_json_else_dev(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'plugin.json')
            self.assertEqual(common.skill_version(path), 'dev')  # no file: a copy in .claude/skills/
            for text, expected in (('{"version": "1.4.0"}', '1.4.0'), ('{"name": "x"}', 'dev'), ('[1]', 'dev')):
                with self.subTest(text=text):
                    with open(path, 'w') as f:
                        f.write(text)
                    self.assertEqual(common.skill_version(path), expected)
        # Without an argument it reads the plugin's manifest three folders above scripts/:
        # the version there inside the plugin, "dev" in a copy under .claude/skills/.
        manifest = os.path.join(os.path.dirname(os.path.realpath(common.__file__)),
                                '..', '..', '..', '.claude-plugin', 'plugin.json')
        if os.path.exists(manifest):
            with open(manifest, encoding='utf-8') as f:
                expected = json.load(f)['version']
        else:
            expected = 'dev'
        self.assertEqual(common.skill_version(), expected)

    def test_included_input_that_does_not_exist(self):
        text = '\n'.join(self.gaps(dict(CONFIG, inputs={'include': ['nope.gradle'], 'exclude': []})))
        self.assertIn('STALE CONFIG: inputs.include nope.gradle — no such file', text)


if __name__ == '__main__':
    unittest.main()
