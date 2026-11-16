"""pages.py: page text and the check of quotes against it."""
import json
import os
import tempfile
import unittest

import fixtures  # noqa: F401  (puts the scripts on sys.path)
import common  # noqa: E402
import pages  # noqa: E402
from fixtures import URL  # noqa: E402


class Pages(unittest.TestCase):
    def test_an_api_answer_becomes_text_a_quote_is_found_in(self):
        # A GitHub release as api.github.com returns it: the body is one JSON string with \\r\\n.
        raw = json.dumps({'tag_name': '2.3.13', 'published_at': '2026-10-30T10:00:00Z', 'draft': False,
                          'body': '## Defect in release\r\n\r\nThis release has a regression described in '
                                  '[issue 3286](https://github.com/google/ksp/issues/3286) which may cause issues.'})
        self.assertFalse(pages.quote_found('This release has a regression', raw))
        text = pages.json_text(raw)
        self.assertIn('tag_name: 2.3.13', text)
        self.assertIn('body:\n## Defect in release\n', text)
        self.assertTrue(pages.quote_found('This release has a regression described in [issue 3286]'
                                          '(https://github.com/google/ksp/issues/3286) which may cause issues.', text))
        self.assertIsNone(pages.json_text('<p>not json</p>'))

    def test_table_row_is_one_line(self):
        text = pages.to_text('<html><body><p>Intro</p><table><tr><td>2.4.20</td><td>9.7.0</td></tr>'
                             '</table><script>var x = 1;</script></body></html>')
        self.assertIn('2.4.20 | 9.7.0', text.split('\n'))
        self.assertNotIn('var x', text)

    def test_quote_is_found_only_when_the_page_says_it(self):
        # (quote, page text, found)
        cases = [
            # Punctuation and line breaks do not matter.
            ('Starting August 31, 2026: New apps must target',
             'Starting August 31 2026:\nNew apps must target Android 16 (API level 36) or higher.', True),
            # A cut marked with … skips text in between, but the parts keep their order.
            ('KGP 2.4.20 … AGP 9.3.1', 'KGP 2.4.20 | Gradle 9.7.0 | AGP 9.3.1', True),
            ('AGP 9.3.1 … KGP 2.4.20', 'KGP 2.4.20 | AGP 9.3.1', False),
            # Numbers, comparisons and a trailing plus are the facts: changing one is a different claim.
            ('8.5.2–9.4.0', '2.4.20 | 7.6.3–9.7.0 | 8.5.2–9.3.1', False),
            ('API level >= 37', 'API level <= 37', False),
            ('API level 37', 'API level > 37', False),
            ('minSdk 26+', 'minSdk 26', False),
            ('minSdk 26', 'minSdk 26+ only', False),
            # A typed >= or <= matches the Unicode sign on the page.
            ('API level >= 37', 'API level ≥ 37', True),
            ('Gradle <= 9.6', 'Gradle ≤ 9.6 | AGP 9.3.1', True),
        ]
        for quote, text, found in cases:
            with self.subTest(quote=quote, text=text):
                self.assertEqual(pages.quote_found(quote, text), found)

    def test_saved_page_is_not_downloaded_again(self):
        with tempfile.TemporaryDirectory() as out:
            os.makedirs(os.path.join(out, 'pages'))
            with open(os.path.join(out, 'pages', 'a.txt'), 'w') as f:
                f.write('saved')
            with open(os.path.join(out, 'pages', 'index.json'), 'w') as f:
                json.dump({URL: {'status': 'ok', 'file': 'a.txt'}}, f)
            original, common.curl = common.curl, lambda *a, **k: self.fail('downloaded again')
            try:
                path, fresh = pages.get(out, URL)
            finally:
                common.curl = original
            self.assertFalse(fresh)
            self.assertEqual(pages.page_text(out, URL), 'saved')


if __name__ == '__main__':
    unittest.main()
