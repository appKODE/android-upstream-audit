# android-upstream-audit

A [Claude Code](https://code.claude.com) plugin that tells an Android team **what changed
upstream since the last check that the project has to act on**: Kotlin and KSP releases, AGP and
Gradle, Google Play requirements and deadlines, new Android API levels, AndroidX and Jetpack
Compose, and the Android and Kotlin blogs.

It works **outside in**: every finding starts from something published upstream, and the audit
checks whether it touches your code — instead of sweeping the code for problems. The result is a
report with verbatim, checked quotes, a `path:line` anchor in your project, the Google Play
deadline when there is one, and a short digest to paste into a team chat. Findings are remembered
between runs, so the next report shows only what is new or changed.

## What it gives you

- **What will break the build or block publishing** — Google Play target-API and policy
  deadlines, AGP and Gradle removals, Kotlin ↔ KSP ↔ AGP compatibility.
- **What ships, not only what is declared** — optionally resolves the release classpath and lists
  libraries that ship at another version than the build asks for (raised by a BOM or another
  library).
- **Stopped and moved libraries** — dependencies whose upstream went dormant or moved.
- **New APIs that replace what you use or implement yourself.**
- **Memory between runs** — findings keep stable keys; your team answers them in
  `upstream-audit/decisions.txt` (`checked`, `declined`, `done`).

It complements [Android skills](https://developer.android.com/tools/agents/android-skills): those
teach an agent how to do a task when asked (upgrade AGP, migrate to Compose); this audit finds out
what needs doing in the first place.

It does **not** look for defects in your code; security and performance reviews are separate.

## Install

In Claude Code:

```
/plugin marketplace add appKODE/android-upstream-audit
/plugin install android-upstream-audit@android-upstream-audit
```

Auto-update is off by default for third-party marketplaces. Turn it on in `/plugin`, or update by
hand:

```
claude plugin update android-upstream-audit@android-upstream-audit
```

## Requirements

- **A Gradle build** (Groovy or Kotlin DSL) that configures, with its `gradlew`. Maven and Bazel
  are not supported.
- **A JDK** in `JAVA_HOME` — the one the build needs; Android Studio's `jbr` works.
- **The Android SDK** — through `ANDROID_HOME` or `local.properties`; otherwise the audit uses the
  location where Android Studio installs it and says so.
- **A git checkout** with an `origin` remote that has the base branch.
- **Python 3.9 or later** (standard library only; the `python3` of macOS works), `curl` and `git`.
- **`gh`, recommended** — see [GitHub access](#github-access).

## Usage

Run from the root of the Gradle build (the directory with `settings.gradle(.kts)`).

Once per project, and again when the build gains a vendored artifact:

```
/android-upstream-audit --init
```

It asks a few questions (base branch, where vendored JARs/AARs come from, which repositories to
follow) and writes `upstream-audit/config.json`. Commit that file — the team shares it.

Then, on every run (monthly works well):

```
/android-upstream-audit
```

The run writes a report (`upstream-audit/<date>.md`) and a run slice
(`upstream-audit/runs/<date>.json`); committing them is your step. Only the newest report and
slice are needed.

Arguments:

| Argument | Meaning |
|---|---|
| `--init` | Set the audit up for this project; no audit runs |
| `--allow-stale-branch` | Audit a branch that is behind its base branch |
| `--resolved-versions on\|off` | For this run, override whether the release classpath is resolved |

The audit is started only by a person: the model never runs it on its own.

Runs are not fully deterministic. The scripts' part — build facts, versions, feeds, quote checks
— is the same every run; which upstream items become findings is the model's judgement.

## What it runs, fetches and writes

**Runs on your machine**

- Your project's `gradlew` with an init script (`audit-facts.init.gradle`) that only
  **configures** the build — nothing is compiled, no project file changes. An optional second
  Gradle run resolves the release runtime classpath; it gives up after 5 minutes.
- Python 3 scripts from the plugin, `git`, `curl`, and `gh` when installed.

**Fetches over the network** (read-only requests)

- `api.deps.dev` — versions and repositories of your dependencies.
- `api.github.com` — releases and head commits of the toolchain and of the repositories you chose
  to follow.
- Maven repositories: `dl.google.com`, `repo1.maven.org`, `plugins.gradle.org`, and the
  repositories your build declares. Repositories that need credentials are not queried.
- Documentation and blogs: `developer.android.com`, `kotlinlang.org`, `blog.jetbrains.com`,
  `blog.google`.

**Sends** only public identifiers in those requests — artifact coordinates, versions, repository
names. No source code, no personal data, no telemetry. The plugin does not check for its own
updates; the marketplace does.

**Writes** only inside your project, in `upstream-audit/`. `decisions.txt` there is written by
people, never by the audit.

### GitHub access

GitHub allows 60 unauthenticated API requests an hour. That is usually enough for a run; a
large project, or several runs an hour from one network, can need more.

- **`gh` installed and logged in** — requests go through `gh api`, authenticated however you
  logged in (`gh auth login`). The audit never sees the token.
- **No `gh`, or `gh` not logged in** — requests go through `curl`, unauthenticated: 60 requests
  an hour. The report says so.

Installing and authenticating `gh` is the recommended way. The audit never reads a token itself;
`gh` reads `GH_TOKEN` / `GITHUB_TOKEN` on its own, so a CI job with only a token gets
authenticated requests by installing `gh`.

## Things to know

- **Project hooks** in `.claude/settings.json` may refuse some shell commands of the audit (for
  example, any command that names a file with secrets). The run then works around them or stops
  and says why.
- **Which copy runs.** If a project also has a copy of the skill in `.claude/skills/`, Claude Code
  may run that copy instead of the plugin.
- **Two runs do not write the same report.** Versions, dates and dependencies come from
  scripts and are the same on every run. Which upstream items become findings, their severity
  and wording come from the model, so two runs on the same commit, even with the same model,
  can differ in a few findings. Check *Considered and dropped* and *Coverage* before you trust
  a "nothing new". Details:
  [What is the same on every run, and what is not](skills/android-upstream-audit/README.md#what-is-the-same-on-every-run-and-what-is-not).

How the audit works inside — files, scripts, the report format and how to answer findings — is in
[`skills/android-upstream-audit/README.md`](skills/android-upstream-audit/README.md).

## Contributing

Issues and pull requests are welcome. Rules for contributors and AI agents are in
[`AGENTS.md`](AGENTS.md); changes are listed in [`CHANGELOG.md`](CHANGELOG.md).

## License

MIT — see [LICENSE](LICENSE).
