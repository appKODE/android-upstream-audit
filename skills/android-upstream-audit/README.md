# android-upstream-audit — for people

`/android-upstream-audit` compares this repository with what was published upstream since the
previous run — Kotlin, KSP, AGP, Google Play requirements, Android API levels, AndroidX,
Compose, the Android and Kotlin blogs — and reports what we have to act on.

This file explains how it works. `SKILL.md` is the instruction for the model that runs it,
with `cases.md`, `slice-format.md` and `digest.md` read at the step that needs them and
`init.md` read only by `--init`. A run does not read this file.

## Requirements

- **A Gradle build that configures**, with Groovy or Kotlin DSL scripts, and its `gradlew`.
  Maven, Bazel and other build tools are not supported. The audit runs `gradlew` with an init
  script that only configures the build — nothing is compiled, nothing in the project changes
  (about 20 s on 1151 projects). A build that does not configure stops the audit.
- **A JDK in `JAVA_HOME`**, the one the build needs; Android Studio's own (`jbr`) works.
- **The Android SDK.** Gradle finds it through `ANDROID_HOME` or `sdk.dir` in
  `local.properties`, which Android Studio writes when it opens the project. A fresh clone or a
  git worktree has neither; then `facts.py` passes the SDK from where Android Studio installs it
  (`~/Library/Android/sdk`, `~/Android/Sdk`, `%LOCALAPPDATA%\Android\Sdk`) and says so.
- **A git checkout** with an `origin` remote that has the base branch.
- **Python 3**, standard library only, plus `curl` and `git`.
- **`gh`, recommended.** Without it the GitHub API is asked through curl: 60 requests an
  hour, or more with `GITHUB_TOKEN` set. The report then says so in the upkeep line.
- Network access to `api.deps.dev`, `api.github.com`, the project's Maven repositories and the
  documentation sites.

## Requested and shipped versions

A version in a build file or a BOM is a request. When another library needs a newer one, Gradle
takes the newer one, and that is what the app ships: a library can lift a dependency to a beta
without anyone writing it down. The configuration run sees only the requests.

`resolved_versions` in `config.json` (asked by `--init`) decides whether a run also checks what
ships. On, `resolved.py` starts Gradle a second time — the task `upstreamAuditResolved`, nothing
compiled — resolves the release runtime classpath of every application module, and lists each
library that ships another version than the build asks for, with Gradle's reason:

```
SHIPS ANOTHER VERSION: androidx.credentials (credentials-play-services-auth): asks 1.5.0 → ships 1.6.0-beta01
```

| | Cost of the second run |
|---|---|
| Gradle's cache holds the libraries (the project was built on this machine) | seconds: 2 s and 29 s on the projects the skill was tried on |
| No cache (a fresh machine, a CI job that does not keep `~/.gradle/caches`) | minutes: the libraries' metadata is downloaded; 75 s on the first run there |
| Longer than 5 minutes, or Gradle fails | the audit goes on without it; the report and the upkeep line say so |

Off, a version raised by a BOM or another library is not seen. The report header and the Slack
digest say which it was (`🔎 Versions: …`), so a reader knows how deep the check went. The
setting is a default: one run overrides it with `/android-upstream-audit --resolved-versions on`
or `off`.

## Libraries that stopped

`releases.py` marks a dependency that will get no newer version under the coordinates we use:

| Line | What it means | Example |
|---|---|---|
| `NO BUILD FOR TWO YEARS` | its newest version is older than two years: dead, or simply finished | `javax.inject:javax.inject:1` |
| `MOVED ON WITHOUT IT` | a `-ktx` artifact its base artifact absorbed | `androidx.biometric:biometric-ktx` → `biometric` |
| `UPSTREAM MOVED ON` | one of the first kind whose source repository (from deps.dev) still releases: the project went on under other coordinates | `io.coil-kt:coil` 2.7.0 → Coil 3 as `io.coil-kt.coil3` |

"Two years" is counted from the newest build of any kind, stable or pre-release: leakcanary's
stable line ends at 2.14 (2024) while its 3.0 alphas go on, so it is not dormant. This is a
different question from "is there a newer version than ours", which the dependency lines answer
and which, like Android Studio, offers pre-releases only to a pin that is already on one. The
line shows the newest version by number, stable when there is one (protoc's `21.0-rc-1` from
2022 outnumbers its 4.x line), and the date of the last build. deps.dev dates a few old builds
by when it indexed them; such a date can keep a dormant library off the list, the quieter
mistake of the two.

## Running it

Once per project, and again when the build gains a vendored artifact:

```
/android-upstream-audit --init
```

It asks a few questions — the base branch, where each vendored jar or AAR comes from (or why
it is not watched), which other repositories to follow — and writes
`upstream-audit/config.json`. Commit that file. Without it the audit stops and asks for
`--init`. Then, on every run:

```
/android-upstream-audit
```

Run it from the root of the Gradle build — the directory with `settings.gradle` or
`settings.gradle.kts`. The run first checks that the branch is current with the
`base_branch` of `config.json`. If it is behind, it stops and writes nothing: update the
branch (rebase on the base branch) and start again, or start it with
`/android-upstream-audit --allow-stale-branch` to audit the tree as it is. The report then says how
many commits it was behind.

The run writes two files and stops; committing them is your step.

## Who writes what

```
INPUTS                          WHO                 OUTPUT
───────────────────────────────────────────────────────────────────────────────
Gradle (init script)          ─> facts.py      ─> facts.json; the run reads what changed
GitHub releases, Maven        ─> releases.py   ─┐
four feeds                    ─> feeds.py      ─┤
documentation pages           ─> pages.py      ─┼─> the run decides what is new,
runs/<previous>.json          ─> anchors.py    ─┤   changed, closed or dropped
decisions.txt                 ─> decisions.py  ─┘
                                                    │
                                                    ▼
                                      <scratch>/edits.json  — every fact, written once
                                                    │
runs/<previous>.json ─┐                             ▼
decisions.txt        ─┤
<scratch>/feeds.json ─┤  (feed gaps)
<scratch>/pages/     ─┤  (saved pages, to check quotes)
<scratch>/facts.json ─┤  (build facts)
config.json          ─┴───────────────────────> slice.py
                                                    │
                              ┌─────────────────────┴───────────────────┐
                              ▼                                         ▼
                runs/<YYYY-MM-DD-HHMM>.json                printed: report header,
                (the slice — only slice.py                 sections 1–7, trend line,
                 writes it)                                upkeep line
                                                                        │
                                                                        ▼
                                              the run writes <YYYY-MM-DD-HHMM>.md:
                                              slice.py writes it up to Considered
                                              and dropped; the run adds Fetch log,
                                              Coverage and the Slack digest

decisions.txt is written only by people, config.json only by --init; a run reads both.
```

## The scripts

All in `scripts/`, standard library Python plus `curl` and `git`. `gh` is used for the GitHub
API when installed; without it the scripts use curl, unauthenticated (60 requests an hour,
enough for a run) or with `GITHUB_TOKEN`, and the digest's upkeep line says so.

| Script | Role | What it does |
|---|---|---|
| `facts.py` + `audit-facts.init.gradle` | Ask Gradle for the build facts | Runs the project's `gradlew` with the init script — configuration only, nothing compiled — and writes `facts.json`: Gradle, AGP and Kotlin versions, application modules with SDK levels and manifests, declared dependencies (`buildscript` classpath and plugins blocks with a version included), each variant's `minSdk` and `targetSdk` when product flavors override them (a value set in `androidComponents.beforeVariants` is not seen), catalogs, vendored artifacts, repositories, plugin sources. Writes Gradle's output to `gradle-facts.log` while it runs, with a progress line every 50 projects, and prints how long it took. Stops with a hint and the log path when the build does not configure or takes more than 9 minutes; warns when Gradle, AGP or Kotlin is a newer major than tested. `releases.py` and `slice.py` read it; `slice.py` keeps a snapshot and prints what changed since the previous slice |
| `resolved.py` | Check what the release build ships | When `resolved_versions` is on (or `--mode on`): runs the init script's `upstreamAuditResolved` task and writes `resolved.json` — each library the release runtime classpath of an application module carries at another version than the build asks for. Off, failed or over 5 minutes, it records why and the audit goes on; `slice.py` needs the file either way |
| `anchors.py` | Check carried findings against the code | Is every anchor of the previous slice still in the code, and which findings are too old to carry |
| `decisions.py` | Read what people decided | Which lines of `decisions.txt` apply, which expired, which name no finding |
| `releases.py` | Watch releases of the dependencies | Takes the dependencies, catalogs and repositories from `facts.json`; what deps.dev lists for every dependency, with dates (the project's Maven repositories as fallback); which artifacts stopped — a `-ktx` artifact folded into its base, or no build for two years, and whether such an artifact's repository still releases under other coordinates; GitHub releases of the toolchain, the pins and the repositories `config.json` names; whether `config.json` still matches the project |
| `init.py` | Set the audit up for a project | `plan` asks Gradle for the facts (the vendored artifacts, the key files) and shows what `--init` asks, with suggested answers; `write` checks the answers against the project and writes `config.json` |
| `feeds.py` | Watch the blogs and release notes | What the four feeds published since the previous run |
| `slice.py` | Build and guard the run's state | Builds the new slice from the previous one plus the edits, checks it, prints the report entries and the digest lines |
| `pages.py` | Read each page once, quote it truthfully | Downloads a documentation page once per run, keeps its text and prints the lines that match; `slice.py` checks every new quote against that text |
| `edits.py` | Reject a malformed edits file | Unknown fields, keys that name no finding, values outside their lists, dates in the wrong format — all at once, before `slice.py` uses the file; not run on its own |
| `tests/` | Keep the scripts honest | One `test_<script>.py` per script, with what they share in `fixtures.py`: a fake repository and hand-written Gradle facts, no network, no Gradle, about five seconds: `python3 -m unittest discover skills/android-upstream-audit/scripts/tests`. Run after changing any script; a run never runs them. After changing `audit-facts.init.gradle`, also run them with `RUN_GRADLE_TESTS=1` and `JAVA_HOME` set: that one test runs Gradle on a tiny build (about 20 s) |
| `common.py` | Share helpers between the scripts | Shared helpers and limits (7, 90, 180 days); not run on its own |

### When `facts.py` fails

The Gradle run turns two of the project's settings off for itself, without touching
`gradle.properties`: the configuration cache (on a cache hit Gradle skips configuration, and
the init script would write nothing) and isolated projects (Gradle refuses to drop the cache
while they are on, and the init script reads every project from the root, which they forbid).
A project that sets `org.gradle.isolated-projects=true`, such as nowinandroid, needs nothing
more.

- **Gradle failed.** The script prints where and what went wrong and the path of
  `<scratch>/gradle-facts.log`, which has Gradle's whole output with the stack trace.
- **Gradle hangs.** `tail -f` on the log: the last `upstream-audit:` line names the build and
  how many of its projects are configured. The script stops by itself after 9 minutes.
- **More than the log shows.** Run the same command by hand without `-q`, adding `--info` if
  needed:
  `./gradlew upstreamAuditFacts --stacktrace --no-configuration-cache -Dorg.gradle.isolated-projects=false -I <skill>/scripts/audit-facts.init.gradle -DupstreamAudit.out=/tmp/facts-raw`
- **Gradle worked, `facts.json` is wrong.** `facts.py --raw /tmp/facts-raw --out <dir>` builds
  `facts.json` again from the raw files, without Gradle. A value the init script could not
  read is listed in `facts.json` under `errors`.

## The JSON files

- **`upstream-audit/config.json`** — what the build files cannot tell; written by `--init`,
  committed by you.
  ```json
  { "schema": 2, "base_branch": "main", "resolved_versions": true,
    "releases": ["<owner>/<repo>"], "commits": ["<owner>/<repo>"],
    "vendored": { "libs/<forked-library>": { "github": "<owner>/<repo>" },
                  "libs/<closed-sdk>": { "watch": false, "reason": "closed SDK, no public source" } },
    "inputs": { "include": [], "exclude": [] } }
  ```
  The toolchain (Kotlin, KSP, coroutines, serialization) and every pin with a suffix or on a
  pre-release are followed without an entry. Every run checks the file against the project:
  a vendored artifact it does not name, or an entry for one the build no longer uses, puts
  `config.json: needs update` in the report header and the upkeep line — run `--init` again.
- **Versions.** `config.json` and the slice carry `schema`, the format, and `skill_version`,
  the skill that wrote them: the `version` in the plugin's `plugin.json`, or `dev` for a copy
  in a project's `.claude/skills/`. Scripts decide by `schema` only (`CONFIG_SCHEMA` and
  `SLICE_SCHEMA` in `common.py`; raise one in the commit that changes the format). A file of a
  later schema stops a run (update the skill); a `config.json` of an earlier one stops the
  audit as a breaking update (run `--init`); an earlier slice is read as it is. Every format
  change that needs `--init` gets a line in the plugin's changelog. `plugin.json` must set
  `version` explicitly, or the report says `dev`.
- **`<scratch>/edits.json`** — what one run decided; thrown away after the run.
  ```json
  { "verified": ["build/libs.versions.toml/ksp-2.3.9-breaks-on-kotlin-2.4"],
    "findings": { "build/libs.versions.toml/kgp-outside-tested-gradle-agp-matrix": {
        "status": "changed", "title": "…", "recommendation": "…", "sources_add": [ … ] } },
    "dropped": [{ "title": "LeakCanary 3.0-alpha-9 — newer alpha only", "reason": "not_applicable" }],
    "inputs_read": ["gradle/libs.versions.toml"] }
  ```
  `inputs_read` names the files the run read in full; on the first run with build facts
  `slice.py` refuses the slice while a key file `facts.py` listed is missing.
- **`upstream-audit/runs/<YYYY-MM-DD-HHMM>.json`** — the slice: the full state after the
  run, written only by `slice.py`. The next run starts from it.
  ```json
  { "schema": 7,
    "run": { "started": "2026-10-13T15:10:10Z", "prior_run": "runs/2026-10-13-1305.json",
             "audited_commit": "e5c1ccb16d…", "base_branch": "main", "base_commit": "80c2194912…",
             "commits_behind_base": 16 },
    "quality": { "sources_ok": 22, "sources_failed": 0, "feed_gaps": [], "config_gaps": [],
                 "coverage_gaps": [ … ], "facts_changes": ["version changed: agp 9.2.1 → 9.3.1"],
                 "facts_problems": [] },
    "facts": { "versions": { "gradle": "9.5.0", "agp": "9.3.1", "kgp": "2.3.0" },
               "app_modules": { ":app": { "compileSdk": "37", "minSdk": "26", "targetSdk": "36", "…": "…" } },
               "dependencies": { "com.squareup.okhttp3:okhttp@root": "5.5.0", "…": "…" },
               "vendored": ["libs/<forked-library>"], "…": "…" },
    "findings": [{
        "key": "build/libs.versions.toml/ksp-2.3.9-breaks-on-kotlin-2.4", "subject": "KSP",
        "title": "KSP must move to >= 2.3.10 with Kotlin 2.4; 2.3.12 released",
        "status": "unchanged", "section": "requires-work", "areas": ["kotlin", "build"],
        "severity": "high", "deadline": null,
        "anchors": [{ "file": "gradle/libs.versions.toml", "line": 8, "excerpt": "ksp = \"2.3.9\"" }],
        "sources": [{ "url": "https://github.com/google/ksp/releases/tag/2.3.12",
                      "published": "2026-09-09", "claim": "…" }],
        "blast_radius": "vendored KSP processor jars, …", "effort": "one-line",
        "last_verified": "2026-10-13" }],
    "dropped": [ … ], "verified_good": [ … ], "closed": [ … ],
    "checked": [ … ], "suppressed": [ … ] }
  ```
  `checked` and `suppressed` hold the findings hidden by a `checked` or `declined` decision,
  whole, so that they can come back.

## Reading a report

`upstream-audit/<YYYY-MM-DD-HHMM>.md`, in this order: *Deadlines*, *Requires work*, *Worth
adopting*, *Needs design sign-off*, *Blocked debt*, *Verified good*, *Considered and
dropped*, *Fetch log*, *Coverage*, and the Slack digest at the end.

- The report is the state as of the run: every open finding in full, unchanged ones too, each
  marked `NEW`, `CHANGED` or `UNCHANGED`. A finding the run closed is listed once under
  *Closed this run* with the reason, then leaves the report. Only the newest report and slice
  are needed; delete the older ones when you like.
- `key: …` is the finding's identifier; it never changes. Copy it into `decisions.txt`.
- *Coverage* says what the run did not look at. Read it before trusting a "nothing new".
- The digest between `<!-- slack-digest:start -->` and `<!-- slack-digest:end -->` is ready
  to paste into Slack. A `🔧 Audit upkeep` line in it means the run did not see everything —
  `config.json` out of date, a failed feed, a feed gap or a silent feed, failed sources, a branch behind its base branch; *Coverage*
  says what to fix.

### What is the same on every run, and what is not

Two runs on the same tree do not write the same report. Part of a run is scripts, part is a
model's judgement:

| From the scripts — the same on every run | From the model — can differ between runs |
|---|---|
| The build facts: versions, SDK levels, dependencies, vendored artifacts (`facts.py`) | Which upstream items become findings, and which are dropped |
| What ships in the release build, when `resolved_versions` is on (`resolved.py`) | Which release notes and pages it reads beyond the required ones |
| Every published version with its date, `NEW`, artifacts that stopped or moved on (`releases.py`) | How deep it follows a lead: a compatibility floor, a deprecation in our code |
| Feed entries and their gaps (`feeds.py`) | Severity, effort and the wording of the recommendation |
| That every quote is on the saved page, that a carried finding was re-checked, that a key file was read (`slice.py`) | |

On nowinandroid, two runs on the same commit agreed on 8 of 11 findings. One found the KSP
version Kotlin 2.4 needs, the other a regression in the newest KSP build; one read the Coil 3
migration guide, the other dropped Coil as merely dormant. When a miss like that is mechanical,
it is turned into a script: `UPSTREAM MOVED ON` now prints the Coil case on every run. So a
finding you expected and do not see is worth a look in *Considered and dropped* and *Coverage*
before you trust its absence; a carried finding, once found, stays until its anchor changes or
a decision closes it.

## Answering a finding

Add a line to `upstream-audit/decisions.txt`:

```
<status> <YYYY-MM-DD> <key> — <reason>
checked  2026-10-08 play/developer-verification/app-registration-2026-09-30 — Play Console Home shows no registration prompt
declined 2026-10-08 deps/libs.versions.toml/example-lib-1.2.3-pin-upstream-dormant — not going to bump
done     2027-04-02 play/sign-in/example-requirement-apr-2027 — exemption request filed in Play Console
```

- `checked` — you verified it; this is how a "manual check" is answered. It comes back after
  180 days, or earlier if upstream publishes something new for it.
- `declined` — the team will not act on it; it stops being reported.
- `done` — you did what it asked outside the code (an exemption in Play Console, a console
  setting); the run closes the finding for good. A fix in the code needs no line: the run
  sees the changed line and closes the finding itself. A finding past its deadline stays in
  the report as `overdue` until it is fixed or answered with `done`.

To renew a decision, add a new line with a new date and keep the old one. Deleting a line
brings the finding back on the next run.

## Not done yet

- Findings about things we do not use today but should adopt, and targeted security
  checks — deliberately out of scope for now.
- Running one area at a time (`/android-upstream-audit kotlin`), the Slack sender, checking plugin
  versions in `releases.py`.
