"""edits.py: the edits file is checked before slice.py uses it."""
import unittest

import fixtures  # noqa: F401  (puts the scripts on sys.path)
import edits  # noqa: E402
from fixtures import KEY, URL  # noqa: E402

SOURCE = {'url': URL, 'published': '2026-10-19', 'claim': 'x'}
UNDATED = {'url': URL, 'read': '2026-10-19', 'claim': 'x'}


def check(data):
    return edits.check(data, {KEY}, set(), {'good/one'})


def finding(**fields):
    return {'findings': {KEY: fields}}


class EditsCheck(unittest.TestCase):
    def test_valid_edits_pass(self):
        for data in [
            {'verified': [KEY], 'findings': {KEY: {'severity': 'low'}},
             'quality': {'sources_ok': 3, 'sources_failed': 0}},
            finding(deadline='2028-02-29'),  # a leap day
            finding(deadline='2027-08'),  # a month is as precise as some sources get
            finding(sources_add=[UNDATED]),  # a page that states no date
        ]:
            with self.subTest(data=data):
                self.assertEqual(check(data), [])

    def test_each_mistake_is_named(self):
        # (edits, the phrases the errors must contain — one per expected error)
        cases = [
            ({'verifed': [KEY]}, ['unknown top-level field "verifed"']),
            ({'findings': {KEY + 'x': {'status': 'changed'}}}, ['is not a finding of the previous slice']),
            (finding(deadline='August 2027'), ["deadline 'August 2027'"]),
            (finding(deadline='2026-02-31'), ["deadline '2026-02-31'"]),
            (finding(deadline='2026-13'), ["deadline '2026-13'"]),
            (finding(last_verified='2026-02-30'), ['last_verified must be a real date']),
            (finding(sources_add=[dict(SOURCE, published='2026-04-31')]), ['"published" must be a real date']),
            (finding(severity='critical', effort='small'), ["severity 'critical'", "effort 'small'"]),
            (finding(sources_add=[dict(UNDATED, published='2026-10-01')]), ['needs exactly one of "published"']),
            (finding(sources_add=[{'url': URL, 'claim': 'x'}]), ['needs exactly one of "published"']),
            (finding(sources=[SOURCE], sources_add=[SOURCE]), ['use one']),
            ({'quality': {'feed_gaps': [], 'watch_gaps': []}}, ['feed_gaps', 'watch_gaps']),
            ({'verified_good_drop': ['good/two']}, ["'good/two' is not a Verified good entry"]),
        ]
        for data, phrases in cases:
            with self.subTest(data=data):
                errors = check(data)
                self.assertEqual(len(errors), len(phrases), errors)
                for error, phrase in zip(errors, phrases):
                    self.assertIn(phrase, error)


if __name__ == '__main__':
    unittest.main()
