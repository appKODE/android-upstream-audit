"""slice.py on a fake repository, run the way an audit runs it."""
import argparse
import contextlib
import io
import json
import os
import unittest

import fixtures  # noqa: F401  (puts the scripts on sys.path)
import common  # noqa: E402
import facts  # noqa: E402
import feeds  # noqa: E402
import slice as slice_mod  # noqa: E402
from fixtures import (CATALOG, CONFIG, KEY, SAVED_SOURCE, URL, FakeRepo,  # noqa: E402
                      build_facts, finding)


class SliceScript(unittest.TestCase):
    def setUp(self):
        self.repo = FakeRepo()

    def tearDown(self):
        self.repo.close()

    def test_checked_names_the_year_only_across_new_year(self):
        self.assertEqual(slice_mod.span('2026-12-20', '2027-01-05', digest=True), '20 Dec 2026 → 5 Jan 2027 (16 days)')
        self.assertEqual(slice_mod.span('2026-11-04', '2026-11-09', digest=True), '4 Nov → 9 Nov (5 days)')

    def test_checked_separates_what_was_seen_from_the_window_asked(self):
        # A first run asks for 90 days; a blog feed holds only its latest posts.
        out = {'run': {'window': {'from': '2026-08-13', 'to': '2026-11-11', 'days': 90, 'first_run': True},
                       'prior_run': None},
               'quality': {'feed_gaps': [{'feed': 'Android Developers blog', 'from': '2026-08-13',
                                          'to': '2026-09-17', 'days': 35}],
                           'failed_feeds': ['Google blog, Android'], 'stale_feeds': []}}
        self.assertEqual(slice_mod.checked(out, digest=True), '\n'.join([
            '🗓️ Checked:',
            '• Android Developers blog: 17 Sep → 11 Nov (55 days)',
            '• Google blog, Android: not seen (download failed)',
            '• Versions, releases and the other feeds: 13 Aug → 11 Nov (90 days)',
            '_Window asked: 13 Aug → 11 Nov (90 days)_']))
        self.assertEqual(slice_mod.checked(out, digest=False).split('\n')[-2:],
                         ['_Window asked: 2026-08-13 → 2026-11-11 (90 days) · first run_', ''])

    def test_clean_run_prints_header_and_writes_the_slice(self):
        code, out = self.repo.slice({'dropped': [{'title': 'A drop', 'reason': 'not_applicable'}]})
        self.assertEqual(code, 0, out)
        self.assertIn('Candidates considered: 2 · reported: 1 · dropped: 1', out)
        self.assertIn('shown line: Shown <X> of 1 · full report: upstream-audit/2026-10-12-1000.md', out)
        self.assertIn('config.json: up to date', out)
        self.assertIn(f'Skill: {common.skill_version()}', out)
        self.assertIn('Checked: 2026-10-10 → 2026-10-12 (2 days) · previous run: runs/2026-10-10-1000.json', out)
        self.assertIn('checked block:\n🗓️ Checked: 10 Oct → 12 Oct (2 days)\n', out)
        self.assertIn('- A drop — not_applicable', out)
        written = common.load_slice(os.path.join(self.repo.root, 'upstream-audit/runs/2026-10-12-1000.json'))
        self.assertEqual(written['findings'][0]['status'], 'unchanged')
        self.assertNotIn('detailed_in', written['findings'][0])
        # The report file holds every finding in full, an unchanged one too.
        with open(os.path.join(self.repo.root, 'upstream-audit/2026-10-12-1000.md'), encoding='utf-8') as f:
            report = f.read()
        self.assertTrue(report.startswith('# Upstream audit — 2026-10-12 10:00 UTC\n'))
        self.assertIn('### KSP: KSP must move — **UNCHANGED**', report)
        self.assertIn('**Recommendation:** Move KSP with Kotlin.', report)
        self.assertTrue(report.rstrip().endswith('- A drop — not_applicable'))
        self.assertEqual(written['run']['skill_version'], common.skill_version())
        self.assertEqual(written['verified_good'][0]['key'], 'good/one')

    def test_first_facts_run_refuses_a_skipped_key_file(self):
        code, out = self.repo.slice({'inputs_read': ['settings.gradle']}, '--dry-run')
        self.assertEqual(code, 1, out)
        self.assertIn('first run on build facts: not read in full: gradle/libs.versions.toml', out)
        code, out = self.repo.slice({}, '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertIn('Build facts: first recorded by this run — every key file was read in full', out)
        self.assertIn('Read in full: 2 files', out)

    def test_with_facts_in_the_previous_slice_the_changes_are_printed(self):
        path = os.path.join(self.repo.root, 'upstream-audit/runs/2026-10-10-1000.json')
        prev = common.load_slice(path)
        prev['facts'] = facts.snapshot(build_facts(versions={'gradle': '9.5.0', 'agp': '9.2.0', 'kgp': '2.3.0'}))
        self.repo.write_json('upstream-audit/runs/2026-10-10-1000.json', prev)
        code, out = self.repo.slice({'inputs_read': []}, '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertIn('Build facts since the previous run: version changed: agp 9.2.0 → 9.2.1', out)

    def test_a_missing_script_output_is_refused(self):
        for name, message in [('facts.json', 'run `facts.py --out'), ('feeds.json', 'feeds.json not found'),
                              ('releases.json', 'run `releases.py --out')]:
            with self.subTest(name=name):
                repo = FakeRepo()
                try:
                    os.remove(os.path.join(repo.scratch, name))
                    code, out = repo.slice({}, '--dry-run')
                finally:
                    repo.close()
                self.assertEqual(code, 1, out)
                self.assertIn(message, out)

    def test_failed_calls_go_to_the_fetch_log(self):
        self.repo.releases_json({'github_client': 'gh', 'not_checked': [],
                                 'failed_calls': ['GitHub releases a/b: HTTP 502']})
        code, out = self.repo.slice({}, '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertIn('(for Fetch log) calls releases.py could not make:\n- GitHub releases a/b: HTTP 502', out)

    def test_base_branch_comes_from_config(self):
        self.repo.write_json('upstream-audit/config.json', dict(CONFIG, base_branch='main'))
        self.repo.git('init', '-q')
        self.repo.git('add', '-A')
        self.repo.git('commit', '-qm', 'one')
        audited = self.repo.git('rev-parse', 'HEAD')
        self.repo.git('commit', '-q', '--allow-empty', '-m', 'two')
        self.repo.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.repo.git('reset', '-q', '--hard', audited)
        code, out = self.repo.slice({})
        self.assertEqual(code, 0, out)
        self.assertIn('Audited tree: 1 commits behind origin/main', out)
        self.assertIn('branch 1 commits behind main', out)
        run = common.load_slice(os.path.join(self.repo.root, 'upstream-audit/runs/2026-10-12-1000.json'))['run']
        self.assertEqual((run['base_branch'], run['base_commit'], run['commits_behind_base']), ('main', audited, 1))

    def test_first_run_creates_the_runs_folder(self):
        runs = os.path.join(self.repo.root, 'upstream-audit', 'runs')
        for name in os.listdir(runs):
            os.remove(os.path.join(runs, name))
        os.rmdir(runs)  # git keeps no empty folder
        code, out = self.repo.slice({})
        self.assertEqual(code, 0, out)
        self.assertIn('trend line: first run', out)
        # No previous run: the window is the FIRST_RUN_DAYS before this one, not "from nothing".
        self.assertIn('Checked: 2026-07-14 → 2026-10-12 (90 days) · first run', out)
        self.assertIn('checked block:\n🗓️ Checked: 14 Jul → 12 Oct (90 days)\n', out)
        self.assertTrue(os.path.exists(os.path.join(runs, '2026-10-12-1000.json')))

    def test_first_run_applies_decisions_to_new_findings(self):
        runs = os.path.join(self.repo.root, 'upstream-audit', 'runs')
        for name in os.listdir(runs):
            os.remove(os.path.join(runs, name))
        self.repo.write('upstream-audit/decisions.txt',
                        f'declined 2026-10-08 {KEY} — not now\n'
                        'checked  2026-10-08 play/gone/key — nothing to see\n')
        new = finding(status='new', recommendation='R.', sources=[SAVED_SOURCE])
        del new['status'], new['last_verified']
        code, out = self.repo.slice({'new': [new]})
        self.assertEqual(code, 0, out)
        self.assertIn('reported: 0', out)
        self.assertIn('**Declined on 2026-10-08:**', out)
        self.assertIn('unknown decision key: play/gone/key', out)
        self.assertIn('Candidates considered: 1 · reported: 0 · hidden by a decision: 1 · dropped: 0', out)
        written = common.load_slice(os.path.join(runs, '2026-10-12-1000.json'))
        self.assertEqual(written['suppressed'][0]['finding']['key'], KEY)
        self.assertEqual(written['quality']['unknown_decision_keys'], ['play/gone/key'])

    def test_hidden_new_finding_is_still_checked(self):
        self.repo.write('upstream-audit/decisions.txt', 'declined 2026-10-08 build/x/hidden — not now\n')
        broken = finding(key='build/x/hidden', recommendation='R.', sources=[])
        for field in ('status', 'last_verified', 'areas'):
            del broken[field]
        code, out = self.repo.slice({'new': [broken]}, '--dry-run')
        self.assertEqual(code, 1)
        self.assertIn('build/x/hidden: no areas', out)

    def test_new_finding_without_key_is_reported_not_crashed(self):
        self.repo.write('upstream-audit/decisions.txt', 'checked 2026-10-08 play/gone/key — nothing\n')
        code, out = self.repo.slice({'new': [{'subject': 'no key'}]}, '--dry-run')
        self.assertEqual(code, 1)
        self.assertIn('every finding needs a "key"', out)
        self.assertNotIn('Traceback', out)

    def test_new_finding_with_expired_check_is_marked_for_recheck(self):
        self.repo.write('upstream-audit/decisions.txt', 'checked 2026-01-01 build/x/expired — old check\n')
        new = finding(key='build/x/expired', recommendation='R.', sources=[SAVED_SOURCE])
        for field in ('status', 'last_verified'):
            del new[field]
        code, out = self.repo.slice({'new': [new]}, '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertIn('note: build/x/expired: checked decision of 2026-01-01 expired', out)

    def test_reopened_finding_stays_reported_until_a_newer_decision(self):
        def run(started, edits_data):
            code, out = self.repo.slice(edits_data, started=started)
            self.assertEqual(code, 0, out)
            return common.load_slice(os.path.join(
                self.repo.root, 'upstream-audit/runs', started[:16].replace('T', '-').replace(':', '') + '.json'))

        self.repo.write('upstream-audit/decisions.txt', f'checked 2026-10-08 {KEY} — looked fine\n')
        first = run('2026-10-12T10:00:00Z', {})
        self.assertEqual([c['key'] for c in first['checked']], [KEY])

        second = run('2026-10-13T10:00:00Z', {'reopen': [KEY], 'findings': {KEY: {'recommendation': 'R.'}}})
        self.assertEqual([f['key'] for f in second['findings']], [KEY])
        self.assertEqual(second['reopened'], [{'key': KEY, 'date': '2026-10-08'}])

        third = run('2026-10-14T10:00:00Z', {})
        self.assertEqual([f['key'] for f in third['findings']], [KEY])
        self.assertEqual(third['checked'], [])
        self.assertEqual(third['reopened'], [{'key': KEY, 'date': '2026-10-08'}])

        self.repo.write('upstream-audit/decisions.txt',
                        f'checked 2026-10-08 {KEY} — looked fine\nchecked 2026-10-14 {KEY} — new requirement met\n')
        fourth = run('2026-10-15T10:00:00Z', {})
        self.assertEqual(fourth['findings'], [])
        self.assertEqual([c['date'] for c in fourth['checked']], ['2026-10-14'])
        self.assertEqual(fourth['reopened'], [])

    def test_finding_hidden_last_run_can_be_closed(self):
        runs = os.path.join(self.repo.root, 'upstream-audit', 'runs')
        self.repo.write('upstream-audit/decisions.txt', f'declined 2026-10-08 {KEY} — not now\n')
        self.assertEqual(self.repo.slice({})[0], 0)

        # The decision line is removed and the finding no longer applies, in the same run.
        self.repo.write('upstream-audit/decisions.txt', '# none\n')
        code, out = self.repo.slice({'closed': [{'key': KEY, 'reason': 'bumped'}]},
                                    started='2026-10-13T10:00:00Z')
        self.assertEqual(code, 0, out)
        written = common.load_slice(os.path.join(runs, '2026-10-13-1000.json'))
        self.assertEqual(written['findings'], [])
        self.assertEqual([c['key'] for c in written['closed']], [KEY])

    def test_closing_a_finding_still_hidden_removes_it_from_the_hidden_section(self):
        runs = os.path.join(self.repo.root, 'upstream-audit', 'runs')
        self.repo.write('upstream-audit/decisions.txt', f'declined 2026-10-08 {KEY} — not now\n')
        self.assertEqual(self.repo.slice({})[0], 0)
        code, out = self.repo.slice({'closed': [{'key': KEY, 'reason': 'bumped'}]},
                                    started='2026-10-13T10:00:00Z')
        self.assertEqual(code, 0, out)
        written = common.load_slice(os.path.join(runs, '2026-10-13-1000.json'))
        self.assertEqual(written['suppressed'], [])
        self.assertEqual([c['key'] for c in written['closed']], [KEY])

    def test_impossible_deadline_is_a_message_not_a_traceback(self):
        code, out = self.repo.slice({'findings': {KEY: {'deadline': '2026-02-31'}}}, '--dry-run')
        self.assertEqual(code, 1, out)
        self.assertIn("deadline '2026-02-31'", out)
        self.assertNotIn('Traceback', out)

    def test_a_closed_finding_is_listed_once_with_its_reason(self):
        code, out = self.repo.slice({'closed': [{'key': KEY, 'reason': 'KSP moved to 2.3.13'}]}, '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertIn(f'## Closed this run\n\n- **KSP** — KSP must move · key: `{KEY}` · KSP moved to 2.3.13', out)
        self.assertNotIn('### KSP', out)

    def test_how_deep_the_version_check_went_reaches_header_digest_and_coverage(self):
        code, out = self.repo.slice({}, '--dry-run')
        self.assertIn('Versions: as the build asks for them only — resolving what ships is off (config.json)', out)
        self.assertIn('🗓️ Checked: 10 Oct → 12 Oct (2 days)\n🔎 Versions: as the build asks for them only — '
                      'resolving what ships is off', out)
        self.repo.resolved_json({'status': 'ok', 'source': 'config.json', 'seconds': 12,
                                 'configurations': [':app releaseRuntimeClasspath'],
                                 'lines': ['androidx.core (core-ktx): asks 1.15.0 → ships 1.16.0']})
        code, out = self.repo.slice({}, '--dry-run')
        self.assertIn('🔎 Versions: as the build asks for them and as the release build ships them\n', out)
        self.assertIn('(for Coverage) the release build ships another version than the build asks for '
                      '(:app releaseRuntimeClasspath):\n- androidx.core (core-ktx): asks 1.15.0 → ships 1.16.0', out)
        self.repo.resolved_json({'status': 'timeout', 'source': 'config.json', 'seconds': 300})
        code, out = self.repo.slice({}, '--dry-run')
        self.assertIn('upkeep line: 🔧 *Audit upkeep:* shipped versions not resolved', out)
        os.remove(os.path.join(self.repo.scratch, 'resolved.json'))
        code, out = self.repo.slice({}, '--dry-run')
        self.assertEqual(code, 1)
        self.assertIn('run `resolved.py --out', out)

    def test_done_closes_a_finding_and_stays_quiet_afterwards(self):
        self.repo.write('upstream-audit/decisions.txt', f'done 2026-10-11 {KEY} — exemption filed in Play Console\n')
        code, out = self.repo.slice({})
        self.assertEqual(code, 0, out)
        self.assertIn(f'- **KSP** — KSP must move · key: `{KEY}` · done 2026-10-11 (decisions.txt): '
                      'exemption filed in Play Console', out)
        self.assertIn('Active 0 (was 1)', out)
        # The next run no longer has the key; the line is the person's record, not an unknown key.
        code, out = self.repo.slice({}, '--dry-run', started='2026-10-13T10:00:00Z')
        self.assertEqual(code, 0, out)
        self.assertNotIn('unknown decision key', out)
        self.assertNotIn('Closed this run', out)

    def test_a_past_deadline_is_overdue_until_answered(self):
        self.repo.close()
        self.repo = FakeRepo(findings=[finding(deadline='2026-10-01', section='deadlines')])
        code, out = self.repo.slice({}, '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertIn('**overdue 11 days**', out)
        self.assertIn(f'`done <YYYY-MM-DD> {KEY} — <what was done>`', out)

    def test_closing_an_unknown_key_is_refused(self):
        code, out = self.repo.slice({'closed': [{'key': 'build/x/never-existed', 'reason': 'x'}]}, '--dry-run')
        self.assertEqual(code, 1, out)
        self.assertIn('build/x/never-existed', out)

    def new_finding(self, key, anchor):
        new = finding(key=key, recommendation='R.', sources=[SAVED_SOURCE], anchors=[anchor])
        for field in ('status', 'last_verified'):
            del new[field]
        return new

    def test_an_anchor_that_does_not_resolve_is_refused(self):
        # The carried finding's line changed, and three new findings point at nothing.
        self.repo.write('gradle/libs.versions.toml', CATALOG.replace('2.3.9', '2.3.12'))
        self.repo.write('upstream-audit/decisions.txt', 'declined 2026-10-08 build/x/hidden — no\n')
        code, out = self.repo.slice({'new': [
            self.new_finding('build/x/no-file', {'file': 'does-not-exist.gradle', 'line': 999, 'excerpt': 'x'}),
            self.new_finding('build/x/no-excerpt',
                             {'file': 'gradle/libs.versions.toml', 'line': 2, 'excerpt': 'invented = "1"'}),
            # Hidden by a decision, and still checked: it comes back when the decision does.
            self.new_finding('build/x/hidden', {'file': 'does-not-exist.gradle', 'line': 1, 'excerpt': 'x'})]},
            '--dry-run')
        self.assertEqual(code, 1, out)
        for refusal in [f'{KEY}: anchor GONE', 'build/x/no-file: anchor FILE_GONE',
                        'build/x/no-excerpt: anchor GONE', 'build/x/hidden: anchor FILE_GONE']:
            self.assertIn(refusal, out)

    def test_new_finding_anchor_on_a_moved_line_is_relocated(self):
        code, out = self.repo.slice({'new': [self.new_finding(
            'build/x/moved', {'file': 'gradle/libs.versions.toml', 'line': 40, 'excerpt': 'ksp = "2.3.9"'})]})
        self.assertEqual(code, 0, out)
        written = common.load_slice(os.path.join(self.repo.root, 'upstream-audit/runs/2026-10-12-1000.json'))
        moved = next(f for f in written['findings'] if f['key'] == 'build/x/moved')
        self.assertEqual(moved['anchors'][0]['line'], 2)

    def test_same_stamp_is_never_overwritten(self):
        self.assertEqual(self.repo.slice({})[0], 0)
        code, out = self.repo.slice({})
        self.assertEqual(code, 1)
        self.assertIn('already exists', out)

    def test_typo_stops_the_run_and_writes_nothing(self):
        code, out = self.repo.slice({'findings': {KEY + 'x': {'status': 'changed'}}})
        self.assertEqual(code, 1)
        self.assertIn('NOT WRITTEN', out)
        self.assertFalse(os.path.exists(os.path.join(self.repo.root, 'upstream-audit/runs/2026-10-12-1000.json')))

    def test_every_finding_needs_a_recommendation(self):
        # The report prints every finding in full: a carried one kept without a recommendation (a
        # slice from before that) is refused until the run gives it one.
        self.repo.close()
        self.repo = FakeRepo(findings=[finding(recommendation=None)])
        code, out = self.repo.slice({}, '--dry-run')
        self.assertEqual(code, 1)
        self.assertIn('needs a "recommendation"', out)
        code, out = self.repo.slice({'findings': {KEY: {'recommendation': 'R.'}}}, '--dry-run')
        self.assertEqual(code, 0, out)

    def test_replaced_sources_drop_the_wrong_quote(self):
        right = {'url': URL, 'read': '2026-10-12', 'claim': '2.4.20–2.4.21 | 7.6.3–9.7.0 | 8.5.2–9.3.1'}
        change = {'status': 'changed', 'recommendation': 'R.', 'sources': [right]}
        code, out = self.repo.slice({'findings': {KEY: change}}, '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertNotIn('old quote', out)
        self.assertIn('(read 2026-10-12, no publication date)', out)

    def test_quote_must_be_on_the_saved_page(self):
        change = {'status': 'changed', 'recommendation': 'Move to 2.4.21.',
                  'sources_add': [{'url': URL, 'published': '2026-10-12', 'claim': '8.5.2–9.4.0'}]}
        code, out = self.repo.slice({'findings': {KEY: change}}, '--dry-run')
        self.assertEqual(code, 1)
        self.assertIn('the claim is not on the saved page', out)
        change['sources_add'][0]['claim'] = '2.4.20–2.4.21 | 7.6.3–9.7.0 | 8.5.2–9.3.1'
        code, out = self.repo.slice({'findings': {KEY: change}}, '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertIn('**Recommendation:** Move to 2.4.21.', out)

    def test_unverified_quote_stays_flagged_until_its_source_is_replaced(self):
        unverified = {'url': 'https://example.test/js', 'published': '2026-10-12',
                      'claim': 'x', 'unverified': 'needs JavaScript'}
        code, out = self.repo.slice({'findings': {KEY: {'status': 'changed', 'recommendation': 'R.',
                                                       'sources': [unverified]}}})
        self.assertEqual(code, 0, out)

        code, out = self.repo.slice({}, '--dry-run', started='2026-10-13T10:00:00Z')
        self.assertEqual(code, 0, out)
        self.assertIn('upkeep line: 🔧 *Audit upkeep:* 1 quotes not checked against their page', out)
        self.assertIn('**unverified quote:** needs JavaScript', out)

        code, out = self.repo.slice({'findings': {KEY: {'status': 'changed', 'sources': [SAVED_SOURCE]}}},
                                    '--dry-run', started='2026-10-13T10:00:00Z')
        self.assertEqual(code, 0, out)
        self.assertIn('upkeep line: none', out)
        self.assertNotIn('unverified quote', out)

    def test_moved_anchor_is_followed(self):
        self.repo.write('gradle/libs.versions.toml', '# comment\n' + CATALOG)
        code, out = self.repo.slice({}, '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertIn('`gradle/libs.versions.toml:3`', out)

    def test_old_fact_must_be_verified_or_carried(self):
        code, out = self.repo.slice({}, '--dry-run', started='2026-10-20T10:00:00Z')
        self.assertEqual(code, 1)
        self.assertIn('re-verify it', out)
        self.assertEqual(self.repo.slice({'carried': [KEY]}, '--dry-run', started='2026-10-20T10:00:00Z')[0], 0)

    def test_declined_finding_leaves_the_body(self):
        self.repo.write('upstream-audit/decisions.txt', f'declined 2026-10-11 {KEY} — not now\n')
        code, out = self.repo.slice({}, '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertIn('**Declined on 2026-10-11:**', out)
        self.assertIn('reported: 0', out)

    def test_still_open_block_lists_unchanged_high_findings_without_a_deadline(self):
        self.repo.close()
        self.repo = FakeRepo([finding(),
                              finding(key='play/deadline-test', subject='targetSdk', deadline='2027-08',
                                      section='deadlines'),
                              finding(key='deps/low-test', subject='Example SDK', severity='low')])
        code, out = self.repo.slice({}, '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertIn('still-open block:\n📌 *Still open*\n• *KSP* — <consequence>\nshown line:', out)

    def test_failed_feeds_reach_the_header_and_the_upkeep_line(self):
        def failing_download(url, path):
            raise OSError('offline')

        original = feeds.download
        feeds.download = failing_download
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                feeds.cmd_fetch(argparse.Namespace(out=self.repo.scratch, since='2026-10-10T10:00:00Z'))
        finally:
            feeds.download = original
        code, out = self.repo.slice({}, '--dry-run')
        self.assertEqual(code, 0, out)
        names = [title for _, title, _, _, _ in feeds.FEEDS]
        self.assertEqual(len(names), 4)
        upkeep = next(line for line in out.splitlines() if line.startswith('upkeep line:'))
        for name in names:
            self.assertIn(f'feed failed: {name}', upkeep)
            self.assertIn(f'{name} — download failed', out)
        self.assertNotIn('Feed gaps: none', out)

    def test_what_a_run_could_not_see_reaches_upkeep_and_coverage(self):
        with open(os.path.join(self.repo.scratch, 'feeds.json'), 'w') as f:
            json.dump({'gaps': [{'feed': 'Kotlin blog', 'from': '2026-09-01', 'to': '2026-09-13', 'days': 12}],
                       'stale': [{'feed': 'Android blog', 'newest': '2026-08-01', 'days': 72}]}, f)
        self.repo.releases_json({'github_client': 'curl',
                                 'not_checked': ['com.example:sdk 1.0 — needs credentials  [x:1]']})
        self.repo.facts_json(build_facts(version_warnings=['GRADLE 10.1 is newer than the 9.x …'],
                                         problems=['included build x was not configured — its facts are missing']))
        code, out = self.repo.slice({}, '--dry-run')
        self.assertEqual(code, 0, out)
        upkeep = next(line for line in out.splitlines() if line.startswith('upkeep line:'))
        for phrase in ['feed gap in Kotlin blog (12 days)', 'Android blog feed silent for 72 days',
                       'GitHub queried with curl, unauthenticated (60 requests an hour) — install and authenticate gh',
                       '1 dependencies not checked',
                       '2 build-facts problems']:
            self.assertIn(phrase, upkeep)
        for coverage_line in ['- com.example:sdk 1.0 — needs credentials  [x:1]',
                              '- included build x was not configured — its facts are missing']:
            self.assertIn(coverage_line, out)

    def test_facts_are_kept_in_the_slice(self):
        code, out = self.repo.slice({})
        self.assertEqual(code, 0, out)
        written = common.load_slice(os.path.join(self.repo.root, 'upstream-audit/runs/2026-10-12-1000.json'))
        self.assertEqual(written['schema'], 7)
        self.assertEqual(written['facts']['vendored'], ['libs/m2/org/example/forked-processor'])
        self.assertEqual(written['facts']['app_modules'][':app']['targetSdk'], '36')
        self.assertNotIn('inputs', written)

    def test_config_json_is_checked_against_the_facts(self):
        self.repo.facts_json(build_facts(vendored=[{'path': 'lib/new-sdk', 'reason': 'jar or AAR files',
                                                    'files': [], 'used_by': []}]))
        code, out = self.repo.slice({}, '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertIn('UNCONFIGURED VENDORED: lib/new-sdk', out)
        self.assertIn('STALE CONFIG: vendored libs/m2/org/example/forked-processor', out)


if __name__ == '__main__':
    unittest.main()
