# Changelog

All notable changes to this plugin. Versions follow [Semantic Versioning](https://semver.org):
a major version means you have to act after updating, and that is said in the entry.

## 1.1.0 — 2026-11-17

- The skill no longer reads `GITHUB_TOKEN`. Install and authenticate `gh` (`gh auth login`) for
  authenticated GitHub requests; `gh` reads `GH_TOKEN` / `GITHUB_TOKEN` itself. Without `gh` the
  audit asks GitHub unauthenticated (60 requests an hour), and the report says so.
- `gh` installed but not logged in no longer fails every GitHub call: the audit falls back to
  unauthenticated curl and says why.
- No format change; no `--init` needed after updating.

## 1.0.0 — 2026-11-16

First public release as a Claude Code plugin.

- `/android-upstream-audit` — audit of the project's Kotlin/Android toolchain against what was
  published upstream: Kotlin and KSP, AGP and Gradle, Google Play requirements, Android API
  levels, AndroidX and Jetpack Compose, the Android and Kotlin blogs.
- `--init` — sets the audit up for a project and writes `upstream-audit/config.json`
  (config schema 2).
- `--allow-stale-branch` — audits a branch that is behind its base branch.
- `--resolved-versions on|off` — resolves the release runtime classpath to show what ships.
- Run slices use schema 7.
- The report header shows the plugin version (`Skill: 1.0.0`).
