"""init.py: what --init suggests and what it refuses."""
import os
import subprocess
import sys
import unittest

import fixtures  # noqa: F401  (puts the scripts on sys.path)
import init  # noqa: E402
from fixtures import CONFIG, KEY_FILES, SCRIPTS, FakeRepo, build_facts  # noqa: E402


class Init(unittest.TestCase):
    def setUp(self):
        self.repo = FakeRepo()
        os.remove(os.path.join(self.repo.root, 'upstream-audit/config.json'))

    def tearDown(self):
        self.repo.close()

    def test_plan_without_config_lists_the_vendored_artifacts_unanswered(self):
        result = init.plan(self.repo.root, build_facts())
        self.assertIsNone(result['suggestions_from'])
        s = result['suggested']
        self.assertEqual((s['releases'], s['commits']), ([], []))
        self.assertEqual(s['vendored'], {'libs/m2/org/example/forked-processor': None})

    def test_plan_prints_the_errors_behind_a_problem(self):
        # The facts of a plan are gone after it; the errors must reach its output.
        errors = [f':m{i} files of x: EvalIssueException: SDK location not found' for i in range(12)]
        self.repo.write_json('facts.json', build_facts(
            errors=errors, problems=['12 values the init script could not read (see "errors")']))
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, 'init.py'), 'plan', '--facts', 'facts.json'],
                           cwd=self.repo.root, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('PROBLEM: 12 values the init script could not read', r.stdout)
        self.assertIn('  error: :m0 files of x: EvalIssueException: SDK location not found', r.stdout)
        self.assertNotIn(':m10 files', r.stdout)
        self.assertIn('  … and 2 more', r.stdout)

    def test_plan_suggests_the_current_config(self):
        # Nobody chose yet: shipped versions are suggested on.
        self.assertTrue(init.plan(self.repo.root, build_facts())['suggested']['resolved_versions'])
        self.repo.write_json('upstream-audit/config.json', CONFIG)
        result = init.plan(self.repo.root, build_facts())
        self.assertFalse(result['suggested']['resolved_versions'])
        self.assertEqual(result['suggestions_from'], 'config.json')
        self.assertEqual(result['key_files'], KEY_FILES)
        self.assertEqual(result['suggested']['vendored'], CONFIG['vendored'])

    def test_write_refuses_what_does_not_match_the_project(self):
        answers = dict(CONFIG, base_branch='no-such-branch',
                       vendored={'libs/missing': {'github': 'a/b'}}, inputs={'include': ['x.gradle'], 'exclude': []})
        errors = '\n'.join(init.check(self.repo.root, answers))
        self.assertIn('base_branch "no-such-branch"', errors)
        self.assertIn('vendored "libs/missing": no such path', errors)
        self.assertIn('inputs.include "x.gradle": no such file', errors)


if __name__ == '__main__':
    unittest.main()
