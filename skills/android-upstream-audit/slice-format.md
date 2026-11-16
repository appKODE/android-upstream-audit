# android-upstream-audit — the edits file and the run slice

Read at step 9 of `SKILL.md`, before writing the edits file. Read it whole: `slice.py`
refuses an edits file that breaks it.

`upstream-audit/runs/<YYYY-MM-DD-HHMM>.json` is **built by `scripts/slice.py`, never written
by hand.** The script carries every finding of the previous slice forward as `unchanged`,
rewrites anchor lines to where the excerpts are now, applies `decisions.txt`, records the
audited commits, and refuses to write a slice that breaks a rule. You give it only what this
run decided, as an edits file in your scratch directory — every field optional:

```json
{
  "verified": ["<key>"],
  "carried": ["<key>"],
  "reopen": ["<key>"],
  "closed": [{ "key": "<key>", "reason": "<why it no longer applies>" }],
  "findings": { "<key>": { "status": "changed", "title": "…", "areas": ["kotlin"], "sources_add": [ … ] } },
  "new": [ { <a complete finding, see below> } ],
  "quality": { "sources_ok": 0, "sources_failed": 0, "failed_urls": [], "coverage_gaps": [] },
  "dropped": [{ "title": "…", "reason": "not_applicable | no_repo_anchor | could_not_verify" }],
  "verified_good": [{ "key": "…", "title": "…" }],
  "verified_good_drop": ["<key>"],
  "inputs_read": ["gradle/libs.versions.toml", "…"]
}
```

- `inputs_read` — the files this run read in full. Needed only on a `FIRST FACTS RUN` (the
  previous slice kept no build facts): then it must hold every key file `facts.py` listed;
  `slice.py` names the missing ones and refuses the slice, and *Coverage* gets the count.

- `verified` — keys whose upstream fact you re-checked this run, or `"all"`; their
  `last_verified` becomes today. `carried` — keys past the freshness limit that you knowingly
  did not re-check (*Freshness*).
- `findings` — per-key overrides: what you set replaces the carried value; `sources_add` and
  `anchors_add` append.
  **To correct a source that was wrong, pass the whole new `sources` list, not
  `sources_add`** — appending leaves the wrong quote in the report next to the right one.
  Every source in a replaced list is checked against its saved page, so keep only what this
  run re-read.
- `quality` — the pages and API calls this run made. Leave the four feeds out of
  `sources_ok` and `sources_failed`: `slice.py` takes them from `feeds.json` itself.
- `reopen` — keys hidden by a decision that must be reported again (`cases.md`). The slice
  keeps the overridden decision under `reopened`, so a key is reopened once, not every run.
- `verified_good` — the entries of the previous slice are carried for you. Pass only what
  this run adds or rewords (the same key replaces the carried entry), and name under
  `verified_good_drop` the keys that no longer hold. A carried entry is still a claim of this
  report: re-check the ones the scripts' output can settle.

Run it with `--dry-run` first. It checks the edits file before anything else — an unknown
field, a key that names no finding, a value outside its list or a date in the wrong format
stops it with the exact problem. Then it prints every finding with status and anchors, the
trend line, the report entries, and anything it refuses; fix those, then run it for real.

A finding, as passed under `new`:

```json
{
  "key": "build/gradle.properties/agp10-removes-builtin-kotlin-optout",
  "subject": "android.builtInKotlin",
  "title": "<short English title>",
  "areas": ["build", "kotlin"],
  "section": "deadlines | requires-work | worth-adopting | design | blocked-debt",
  "severity": "high | medium | low",
  "deadline": "2026-12 | null",
  "manual_check": false,
  "what_to_check": "<only with manual_check: where a person looks and what settles it>",
  "anchors": [{ "file": "gradle.properties", "line": 15, "excerpt": "android.builtInKotlin=false" }],
  "sources": [{ "url": "<url>", "published": "2026-07-01", "claim": "<verbatim quote from pages.py>",
                "unverified": "<only when pages.py could not save the page: why>" }],
              // "published" is the date the source states. A page that states none (most
              // documentation pages) takes "read": "<YYYY-MM-DD>" instead — never both.
  "recommendation": "<what to do and what it buys, with the facts that decide it>",
  "blast_radius": "<affected vendored/pinned artifacts, or none>",
  "effort": "one-line | local refactor | cross-module | spike",
  "api_stability": "stable | experimental | alpha | preview | null"
}
```

`section` is the report section the finding is listed under (sections 1–5 of *Report
format*), and `manual_check` marks a rule 14 item; `slice.py` uses both to print the report
entries. `recommendation` is the body of the finding's report entry: what to do, and what it
buys against what it costs (rule 7); for a deadline, also what stops working. A new or
changed finding must have one, and a finding you mark `changed` gets its `recommendation`
rewritten in the edits file whenever the old one no longer holds — the report prints
whatever the slice says. A manual check also carries `what_to_check` — one sentence, written from the source
when the finding is created and carried from then on, so that it is not reworded each run. A
finding carried from a slice that has no `section`, or a manual check that has no
`what_to_check`, gets it this run, under `findings` in the edits file — `slice.py` refuses
the slice otherwise.

### Key

`<area>/<file or artifact>/<slug>`. **Never a line number** — the key must survive a refactor;
`excerpt` is what re-locates the anchor when a line moves. Reuse an existing key and `subject`
verbatim whenever a finding matches one in the previous slice; mint a new key only when
nothing matches. A key never changes once minted, even when its wording has aged.

### Areas

Every finding carries `areas` — one or more of:

| Area | Covers |
|---|---|
| `kotlin` | the Kotlin compiler and Gradle plugin, KSP, kotlinx libraries |
| `build` | AGP, Gradle, R8, convention plugins, static analysis |
| `google-play` | Google Play requirements and deadlines |
| `platform` | Android behaviour changes by API level |
| `compose` | Jetpack Compose and AndroidX libraries |
| `deps` | third-party SDKs, pins, forks and vendored artifacts |
| `security` | anything with a security consequence, whatever else it is |

Tag by what the finding is about, not by where it was found: a security fix in a Kotlin
release is `["kotlin", "security"]`. Show the areas in the report next to the key. A finding
carried from a slice that has none gets them this run — set `areas` for its key under
`findings` in the edits file; `slice.py` refuses a slice with an untagged finding.
