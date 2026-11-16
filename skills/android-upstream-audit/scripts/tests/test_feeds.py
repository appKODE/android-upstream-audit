"""feeds.py: silent feeds and feed gaps."""
import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest

import fixtures  # noqa: F401  (puts the scripts on sys.path)
import feeds  # noqa: E402


class Feeds(unittest.TestCase):
    ATOM = ('<feed xmlns="http://www.w3.org/2005/Atom">'
            '<entry><title>Old</title><published>2026-06-01T00:00:00Z</published><link href="u1"/></entry>'
            '<entry><title>Newer</title><published>2026-07-01T00:00:00Z</published><link href="u2"/></entry>'
            '</feed>')

    def fetch(self, silent_days, since):
        with tempfile.TemporaryDirectory() as out:
            original_feeds, original_download = feeds.FEEDS, feeds.download

            def fake_download(url, path):
                with open(path, 'w') as f:
                    f.write(self.ATOM)
                return len(self.ATOM)

            feeds.FEEDS = [('adb', 'Test blog', 'https://example.test/feed', 'atom', silent_days)]
            feeds.download = fake_download
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    feeds.cmd_fetch(argparse.Namespace(out=out, since=since))
            finally:
                feeds.FEEDS, feeds.download = original_feeds, original_download
            with open(os.path.join(out, 'feeds.json')) as f:
                return json.load(f)

    def test_silent_feed_is_reported(self):
        result = self.fetch(silent_days=30, since='2026-06-15T00:00:00Z')
        self.assertEqual([s['feed'] for s in result['stale']], ['Test blog'])

    def test_feed_that_does_not_reach_the_window_start_is_a_gap(self):
        result = self.fetch(silent_days=100000, since='2026-05-01T00:00:00Z')
        self.assertEqual(result['gaps'][0]['to'], '2026-06-01')
        self.assertEqual(result['stale'], [])


if __name__ == '__main__':
    unittest.main()
