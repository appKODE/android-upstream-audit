// Commit message rules from AGENTS.md ("Commits"). CI checks every commit of a pull request:
// pull requests are rebase-merged, so each commit lands on main as it is.
// Self-contained on purpose (no `extends`), so nothing has to be resolved at run time.
export default {
  // Do not skip merge, fixup! and squash! commits: they must fail.
  defaultIgnores: false,
  rules: {
    'type-enum': [2, 'always', ['feat', 'fix', 'docs', 'refactor', 'test', 'chore', 'ci']],
    'type-case': [2, 'always', 'lower-case'],
    'type-empty': [2, 'never'],
    'scope-enum': [2, 'always', ['skill', 'scripts', 'gradle', 'docs', 'release', 'ci', 'repo']],
    'scope-case': [2, 'always', 'lower-case'],
    'scope-empty': [2, 'never'],
    'subject-empty': [2, 'never'],
    'subject-case': [2, 'never', ['sentence-case', 'start-case', 'pascal-case', 'upper-case']],
    'subject-full-stop': [2, 'never', '.'],
    'header-max-length': [2, 'always', 72],
    'header-trim': [2, 'always'],
    'body-leading-blank': [2, 'always'],
    'footer-leading-blank': [2, 'always'],
  },
};
