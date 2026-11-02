## Commit titles: Conventional Commits

This project's registry entry says `commits: conventional`: commit titles are

    <type>[(<scope>)][!]: <description>

- type: feat (new feature), fix (bug fix), perf, refactor, test, docs, build, ci, chore, revert
- scope is optional: the name of the area of this repo; the description says what this commit makes different
- a breaking change adds `!` before the colon and says `BREAKING CHANGE: …` in the body (a major version at release)
- `brunna merge-branch` checks the title of every commit on the branch before merging and refuses to merge when one
  fails (just like a failing test): fix the titles and run it again
- if the repo has a `cliff.toml`, release notes are generated from the commits by git-cliff (feat, fix, perf and revert
  are listed)
