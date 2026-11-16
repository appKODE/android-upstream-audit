# android-upstream-audit — cases that do not happen on every run

`SKILL.md` describes the ordinary run. This file holds what to do when something unusual
comes up. **Read a section only when its trigger fires** — each one says what the trigger is
and, where a script reports it, what the script prints.

---

## First run — there is no previous slice

*Trigger:* `upstream-audit/runs/` is empty; `anchors.py` prints "no previous run slice".

Report everything, say in the report's opening lines that there was nothing to compare
against, and do not fail or ask. Read every key file `facts.py` lists in full — there are no
earlier facts to compare with. The window is the last 90 days. The digest trend line is
`first run` (which `slice.py` prints). Every finding is `new`.

`decisions.py` reports every line of a kept `decisions.txt` as `unknown_key` — there is no
slice to hold the keys yet. That is expected. When a finding of this run takes a key that a
decision names, reuse that key verbatim: `slice.py` applies the decision to it, as it would to
a carried finding. A decision that matches nothing this run stays an unknown key.

## The branch is behind its base branch

*Trigger:* step 0, `git rev-list --count HEAD..origin/<base>` prints a number above 0.
`<base>` is `base_branch` of `upstream-audit/config.json`.

- **Without `--allow-stale-branch`: stop.** Fetch nothing else, write no report and no slice.
  Tell the developer, in the language of the conversation and with the flag spelled exactly:
  "The branch is N commits behind <base>; update it, or run with `--allow-stale-branch`." Do
  not update the branch yourself.
- **With `--allow-stale-branch`:** continue on the tree as it is. `slice.py` records the
  number and prints "N commits behind origin/<base>" on the `Audited tree:` line; repeat it
  in *Coverage*. The number is enough — do not list the files that differ.

If `git fetch origin <base>` itself fails, say so and treat the tree as stale with an unknown N.

Why it matters: one run started 165 commits behind and would have reported a detekt setup
that the base branch had already replaced.

## A script fails

*Trigger:* a script exits with an error or prints `FAILED`.

A `FAILED` line for one repository or one feed is a fetch-log entry: record it and go on. If
the script itself breaks, say so in *Coverage* and do that step by hand:

- anchors — grep each `excerpt` of the previous slice and note the current line;
- releases — `gh api "repos/<owner>/<repo>/releases?per_page=5"` (without `gh`:
  `curl -sL https://api.github.com/repos/<owner>/<repo>/releases?per_page=5`), one plain
  command per repository; `https://api.deps.dev/v3/systems/maven/packages/<group>%3A<name>`
  or `maven-metadata.xml` with `curl`;
- feeds — `curl -sL` each feed URL from `SKILL.md` and parse the XML;
- slice — write the JSON by hand in the shape of the previous slice, and say that it was not
  validated.

A broken script is a reason to report, never a reason to skip the step.

**`facts.py` is the exception: it has no hand fallback.** When it stops, the build did not
configure (no JDK in `JAVA_HOME`, no Android SDK location, a broken script, no network for the
plugins) or did not finish in 9 minutes: tell the person what it printed — it names the likely
fix and the log with Gradle's full output — and stop the run, as on a stale branch. Reading the build scripts by hand instead would bring back the guessing the
facts replaced.

## A feed does not reach back to the window start

*Trigger:* `feeds.py fetch` prints `GAP:` for a feed, and a list under `Feed gaps:`.

The stretch between the window start and the feed's oldest entry was not seen by any run.
`slice.py` reads the gaps from `feeds.json` and puts them on the report's `Feed gaps:` line
and into the digest's upkeep line; repeat them in *Coverage*. Do not try to fill the gap
yourself and do not imply it was checked: the lines exist so that a developer can look at
that period by hand.

## A feed has gone silent

*Trigger:* `feeds.py fetch` prints `STALE FEED` for a feed.

The feed still parses, but nothing new has appeared in it for longer than that feed usually
goes quiet — the way the Android Developers blog looked when it moved off Blogger on
2026-10-15. Find where the source went (the last entries, the site's own page, a
`<link rel="alternate">` on its home page) and name the new address in *Coverage*, so that a
person updates `FEEDS` in `scripts/feeds.py`. `slice.py` already puts the feed on the
report's `Feed gaps:` line and into the digest's upkeep line. Do not treat its silence as
"nothing new upstream".

## A blog source no longer says it

*Trigger:* re-quoting a carried finding's blog source, `pages.py grep` of its URL prints
`FAILED` or no matching line.

That is not a reason to carry the finding unchecked. If the page is gone or no longer says
it, the finding's upstream fact is unverified: mark the finding `changed`, say so in its
write-up, and leave its `last_verified` as it was (do not list it under `verified`).

## A stable Compose release falls in the window

*Trigger:* `feeds.py fetch` prints `STABLE COMPOSE RELEASE in window`.

Read the release notes of **all five** Compose groups we depend on with `pages.py`:
`https://developer.android.com/jetpack/androidx/releases/compose-<group>` for `ui`,
`foundation`, `material3`, `animation`, `runtime`. A release blog post is not a substitute —
it covers the headlines, while deprecations and behaviour changes are only in the release
notes.

Look for: a stable release newer than ours; new components or APIs that replace something we
implement ourselves in the project's UI kit or design system; deprecations of APIs we call (then grep for the call
sites — rule 9). Record the stability of every new API (rule 6).

A page you did not read is listed in *Coverage* by name. "Compose release notes read" is not
a valid entry when only some were.

## Two sources disagree

*Trigger:* a page and a post, or two pages, give different values for the same fact.

The more specific source wins, in this order: API reference and release notes → requirement
and policy pages → documentation guides → blog posts. Report the winning value and name the
conflict in one line ("the 2026-08-12 blog sample still shows
`@OptIn(ExperimentalGridApi::class)`; the 1.12.0 release notes say promoted to stable"). If
the losing side is the release notes, fetch that page a second time with a narrower prompt
before believing it.

## An anchor is gone

*Trigger:* `anchors.py` prints `GONE` or `FILE_GONE`; `slice.py` refuses to write.

The excerpt no longer exists, so the code changed. Decide which it is:

- the thing the finding was about is gone or fixed → **close** the finding: add it to
  `closed` in the edits file with the reason, and mention it in the report;
- the code moved or was rewritten but the finding still holds → give it a new anchor in the
  edits file (`"findings": { "<key>": { "anchors": [ … ] } }`) and mark it `changed`.

Never carry the old line forward. `MOVED` needs nothing from you — `slice.py` rewrites the
line.

## A decision has an unknown key

*Trigger:* `decisions.py` prints `unknown_key`; `slice.py` prints "unknown decision key".

`decisions.py` runs before the edits file exists, so it knows only the previous slice's keys:
a decision for a finding this run adds under `new` shows up there as `unknown_key` and is then
applied by `slice.py`. **The "unknown decision key" lines `slice.py` prints are the final
list** — report those, not the earlier `decisions.py` output.

The decision does nothing, and silently doing nothing is how a typo hides. List every such
line in *Coverage* under "decisions with an unknown key", with the closest existing key the
script suggests. **Do not apply the decision to the suggested finding** — only report it.
`slice.py` records the keys in `quality.unknown_decision_keys`.

A line the script calls `unparseable` is not a decision either: quote it in *Coverage* and
treat the finding as undecided.

## A `checked` decision has expired

*Trigger:* `decisions.py` prints `expired` (older than 180 days) — or you find, while
re-verifying, that upstream published something for that key after the check date (a new or
moved deadline, a revised requirement, a wider scope), or that the finding's anchor changed
in a way that affects it. The script sees only the age; the other two are yours to notice.

Bring the finding back as `changed`, with its heading reading "re-check — last checked
<date>", say what changed since the check, and end the item with the line the person should
add to `decisions.txt`. A hidden finding is kept whole inside the slice, so nothing has to be
reconstructed: for an expiry by age `slice.py` returns it by itself; for the other two, add
the key to `"reopen"` in the edits file, once: the slice records the overridden decision,
and later runs report the finding until a person adds a newer line (`decisions.py` prints it
as `reopened`). Its upstream fact will usually be past the freshness
limit — re-verify it and list it under `verified`.

The old line stays in `decisions.txt`; the person answers with a new line and a new date.
The same mechanism returns a finding when its line is simply deleted from `decisions.txt`.

## `config.json` no longer matches the project

*Trigger:* `releases.py` prints `config.json: needs update`, with `UNCONFIGURED VENDORED` or
`STALE CONFIG` lines.

- `UNCONFIGURED VENDORED` — a vendored artifact the build uses and `config.json` does not
  name. If its source is obvious from the repository (a comment next to it, its folder),
  check that upstream by hand this run (`releases.py --body` or the GitHub API for releases
  and the head commit); otherwise name it in *Coverage* as not checked.
- `STALE CONFIG` — an entry for a path the build no longer uses. Nothing to check.

`slice.py` runs the same check itself, records it in the slice, sets the header line and the
digest's upkeep line, and prints the lines for *Coverage*, so that a person runs `--init`
again. The run does not edit `config.json`.

## The skill and the project's files disagree on the format

*Trigger:* a script stops with `update the skill` or `breaking update`.

`config.json` and every slice carry `schema` (the format) and `skill_version` (the skill that
wrote them). Stop the run and tell the person the script's sentence:

- `update the skill` — a newer skill wrote the file (someone on the team updated). Update the
  plugin, then start again. Never edit the file to make it fit.
- `breaking update` — this skill needs a newer `config.json`. Run `--init`: it suggests the
  current answers, so only what is new is asked.

An earlier slice is read as it is; it never needs `--init`.

## A slice with this stamp already exists

*Trigger:* `slice.py` prints "already exists".

Two runs started in the same minute. Never overwrite: use the current time for `--started`
and the matching name for the report.
