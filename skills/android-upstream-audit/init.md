# android-upstream-audit — `--init`

Read only when the command is started with `--init`. It writes `upstream-audit/config.json` —
what the audit cannot find in the build files — and stops. It does not run the audit, and it
never touches git: committing the file is a person's step.

## Steps

1. `python3 <skill>/scripts/init.py plan --out <scratch dir>` — what each question is about,
   with a suggested answer. The suggestions come from the current `config.json`; without one,
   only the base branch and the vendored artifacts are suggested. It asks Gradle for the build
   facts first (needs `JAVA_HOME`) — run it the way *Running Gradle* in `SKILL.md` step 1
   says: announce it, a 10-minute shell timeout, a plain command. If it stops, the build does
   not configure — tell the person what it printed and stop: `--init` needs a build that
   configures, as the audit does.
   A `PROBLEM` line means Gradle configured the build but some facts are missing or
   incomplete: read the `error:` lines `plan` prints under it before asking anything, tell the
   person what they name, and when the cause can be fixed for the run (the SDK, the network, a JDK), fix it and
   run `plan` again. Ask the questions on a plan without `PROBLEM` lines, or say which remain
   and why.
2. Ask the questions below with AskUserQuestion, at most four per call, one subject per
   question. Put the suggested answer first and mark it *(Recommended)*. Every question names
   what it is about, so it can be answered without the rest of the conversation.
3. Write the answers to a JSON file in the scratch directory, shaped as `config.json` (below),
   and run `python3 <skill>/scripts/init.py write --from <file>`. It refuses what does not
   match the project; fix and run it again. It adds `skill_version` itself; do not write it.
4. Show the person the written file and tell them to commit it.

## The questions

- **Base branch** — the branch the audit compares the tree with in step 0 and diffs from in
  step 1. Options: the suggested branch, then the other conventional branches `plan` lists;
  any other one through *Other*.
- **Each vendored artifact** — a jar, an AAR or a local Maven artifact the build uses, which no
  public index can tell the source of. Options: the suggested `owner/repo` when there is one;
  *Do not watch — closed SDK, no public source*; through *Other*, an `owner/repo` or another
  reason not to watch it. A watched artifact's repository is followed by its head commit.
- **Repositories followed for releases** — besides the toolchain and the pins, which the
  audit follows by itself. Options: keep the suggested list; none; through *Other*, a list of
  `owner/repo`.
- **Repositories followed by head commit** — projects that do not tag releases. Same options.
- **Key files** — say how many `plan` found: the files a first run reads in full. Options: use
  them as found; through *Other*, files to add (`include`), or modules, files and vendored
  paths to leave out of the audit (`exclude` — an excluded module is no application module,
  its dependencies are not checked, its jars are not vendored). Ask only when `plan` printed a
  `PROBLEM` line or the person wants to see the list; otherwise keep the suggestion.

- **Shipped versions** (`resolved_versions`) — whether each run also checks which versions the
  release build really ships. Put the facts in the question so that the person can decide:
  - A version in a build file or a BOM is a request. When another library needs a newer one,
    Gradle takes the newer one, and that is what ships — a library can lift a dependency to a
    beta without anyone writing it down.
  - On: each run starts Gradle a second time, after the configuration run — the task
    `upstreamAuditResolved`, nothing compiled — and resolves the release runtime classpath of
    every application module. It lists each library that ships another version than the build
    asks for, and the report and the Slack digest say the versions were checked as shipped.
  - Cost: seconds when Gradle's cache already holds the libraries, which it does when the
    project was built on this machine (2 s and 29 s on the projects the skill was tried on);
    minutes when it has to download their metadata — a fresh machine, or a CI job that does
    not keep the Gradle cache (75 s on the first run there). It stops after 5 minutes and the
    audit goes on without it.
  - Off: a version raised by a BOM or another library is not seen; the report says so.
  - This is only the default: a single run can override it with
    `/android-upstream-audit --resolved-versions on` or `off`.

  Options: *On (Recommended)* — or the current value when `config.json` has one; *Off*.

A vendored artifact `plan` marks as no longer used is dropped without a question.

## config.json

```json
{
  "schema": 2,
  "base_branch": "main",
  "releases": ["<owner>/<repo>"],
  "commits": ["<owner>/<repo>"],
  "vendored": {
    "libs/<forked-library>": {"github": "<owner>/<repo>"},
    "libs/<closed-sdk>": {"watch": false, "reason": "closed SDK, no public source"}
  },
  "inputs": {"include": [], "exclude": []},
  "resolved_versions": true
}
```

Every vendored artifact `plan` lists must have an entry: either `github` or `watch: false`
with its reason. One without an entry is reported on every run until `--init` names it.
