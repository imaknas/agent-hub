# Contributing to agent-hub

Thanks for looking. agent-hub is run day to day on one machine by its author, so the bar for a change is that it keeps
working there and on any other install. A few rules keep it that way.

## Before you start

Read [AGENTS.md](AGENTS.md): it is the rulebook every coding agent working in this repo follows, and it applies to
people too. The design documents behind each part are in [docs/design/](docs/design/). For anything larger than a fix,
open an issue first and say what you want to change and why.

## The rules that matter most

- **Tool neutrality.** Tool-specific syntax (a coding agent's flags, settings format, model names) lives only in the
  adapters (`bin/runtime_<name>.py`, `bin/provider_<name>.py`). Everything else uses the neutral interfaces in
  `bin/runtime_ports.py`. `tests/test_runtime_boundary.py` and `tests/test_tool_agnostic.py` check this.
- **No paths, names or personal data in code.** Paths, names and the owner come from `bin/hubconf.py` (settings in the
  instance's `hub.json`). Never hard-code a home directory, a project name or a person.
  `tests/test_personal_boundary.py` checks this.
- **English in code, comments and docs.** Text the owner reads goes through the catalog (`_()` in Python, `hub_msg` /
  `hub_say` in shell); translations live in `locales/`. No other place may contain Chinese text
  (`tests/test_i18n_static.py`).
- **Python 3.13, through the platform's Python.** Run scripts with `bin/py`; build it once with `bin/setup-python`
  (needs [uv](https://docs.astral.sh/uv/)).
- **Tests before rewrites.** When you change behaviour that no test covers, first add a test that pins today's
  behaviour, then change it. Behaviour visible from outside is pinned by golden files under `tests/fixtures/`;
  regenerate them only on purpose and check the diff.

## Checks

```bash
bin/setup-python     # once
bin/lint             # ruff, pyflakes rules only
bin/run-tests        # every test, in parallel, under a throwaway HOME
bin/linux-test       # the same suite in a Linux container (Docker)
```

CI runs `bin/lint`, `bin/linux-test` and a gitleaks scan on every pull request.

`bin/run-tests` runs the suite as a zh-Hant instance, and CI runs it as an English one on macOS and on Linux
(`bin/linux-test`), so a test that
assumes the instance's language fails in one of them: a test that checks text in one language sets that language
itself (`AGENT_HUB_LANG_UI` / `AGENT_HUB_LANG_CONTENT`).

## How changes reach main

Contributions come as pull requests: fork, branch, open a PR. A PR merges only when every CI job passes (lint, the
Linux and macOS test suites, the installer, the secret scan). The maintainer works in git worktrees and lands changes
with `agenthub merge-branch`, which runs the full suite before it moves main, then pushes main directly; CI runs again
on the push. Nobody can force-push or delete main.

## Commits

Commit titles follow [Conventional Commits](https://www.conventionalcommits.org/):
`<type>[(<scope>)][!]: <description>`, where type is one of feat, fix, perf, refactor, test, docs, build, ci, chore or
revert. A breaking change adds `!` and explains itself in a `BREAKING CHANGE:` paragraph. Release notes are generated
from these titles with [git-cliff](https://git-cliff.org/) (`cliff.toml`), so the description should say what the
change makes different for a user.

## Releasing

Versions follow the commits: `feat` raises the minor version, `fix` and `perf` the patch, `!` the major
(`git cliff --bumped-version` says which). Nothing in `CHANGELOG.md` is written by hand.

1. `git cliff --tag vX.Y.Z -o CHANGELOG.md`, and commit it as `chore(release): vX.Y.Z` (through
   `agenthub merge-branch`).
2. `git tag -a vX.Y.Z -m vX.Y.Z` on that commit, and push the commit and the tag. `agenthub --version` reads the tag.
3. `gh release create vX.Y.Z --title "agent-hub X.Y.Z" --notes "$(git cliff --latest --strip header)"`.

Users upgrade only to tags (`agenthub upgrade`), so a fix merged on main reaches them only when it is released. Release
whenever main has user-visible changes (anything `git cliff --unreleased` lists), rather than holding them back. What
`agenthub upgrade` shows a user before moving is the release's `CHANGELOG.md`, with the breaking entries first, so a
breaking change needs its `BREAKING CHANGE:` paragraph to say what the user has to do. A release must keep
`bin/upgrade.py --steps --from <ref>` working: the version a user upgrades from runs it after moving the checkout.

## License

By contributing you agree that your contribution is licensed under [Apache-2.0](LICENSE), like the rest of the project.
