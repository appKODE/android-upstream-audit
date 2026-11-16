"""resolved.py: what the release build ships against what the build asks for."""
import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

import fixtures  # noqa: F401  (puts the scripts on sys.path)
import resolved  # noqa: E402
from fixtures import CONFIG, FakeRepo  # noqa: E402


def mismatch(module, asked, ships, reason='requested; constraint; between versions 1.11.0-rc01, 1.10.0 and 1.9.0'):
    return {'module': module, 'asked': asked, 'resolved': ships, 'configuration': 'prodReleaseRuntimeClasspath',
            'reason': reason}


class Resolved(unittest.TestCase):
    def setUp(self):
        self.repo = FakeRepo()
        self.addCleanup(self.repo.close)

    def run_script(self, *args):
        out = io.StringIO()
        with mock.patch.object(sys, 'argv', ['resolved.py', '--out', self.repo.scratch, *args]), \
                contextlib.redirect_stdout(out), mock.patch('os.getcwd', return_value=self.repo.root):
            code = resolved.main()
        with open(os.path.join(self.repo.scratch, 'resolved.json')) as f:
            return code, out.getvalue(), json.load(f)

    def test_the_tasks_files_become_one_line_per_library_group(self):
        bom = '1.10.0-alpha04 (androidx.compose:compose-bom-alpha)'
        raw = os.path.join(self.repo.scratch, 'raw')
        os.makedirs(raw)
        for project, configuration in ((':app', 'prodReleaseRuntimeClasspath'), (':catalog', 'releaseRuntimeClasspath')):
            with open(os.path.join(raw, f'resolved-{project[1:]}.json'), 'w') as f:
                json.dump({'project': project, 'configurations': [configuration], 'resolved': 300, 'errors': [],
                           'mismatches': [mismatch('androidx.compose.ui:ui', [bom], '1.11.0-rc01'),
                                          mismatch('androidx.compose.ui:ui-android', [bom], '1.11.0-rc01'),
                                          mismatch('androidx.compose.ui:ui-text', [bom], '1.11.0-rc01'),
                                          mismatch('androidx.core:core-ktx', ['1.15.0'], '1.16.0',
                                                   'requested; core is in atomic group androidx.core')]}, f)
        data = resolved.read_raw(raw)
        self.assertEqual(len(data['mismatches']), 4)  # once per module, from both application modules
        self.assertEqual([resolved.line(g) for g in resolved.grouped(data['mismatches'])], [
            f'androidx.compose.ui (2 artifacts: ui, ui-text): asks {bom} → ships 1.11.0-rc01 — '
            'the highest of 3 versions asked by the build and its libraries',
            'androidx.core (core-ktx): asks 1.15.0 → ships 1.16.0'])

    def test_off_by_config_by_flag_or_by_a_config_from_before_the_setting(self):
        code, out, data = self.run_script()
        self.assertEqual((code, data), (0, {'status': 'off', 'source': 'config.json'}))
        self.assertIn('Resolved versions: off (config.json)', out)
        self.repo.write_json('upstream-audit/config.json', dict(CONFIG, resolved_versions=True))
        self.assertEqual(self.run_script('--mode', 'off')[2]['source'], '--mode off')
        old = {k: v for k, v in CONFIG.items() if k != 'resolved_versions'}
        self.repo.write_json('upstream-audit/config.json', dict(old, schema=1))
        self.assertIn('run --init to choose', self.run_script()[2]['source'])


if __name__ == '__main__':
    unittest.main()
