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

## Commits

Commit titles follow [Conventional Commits](https://www.conventionalcommits.org/):
`<type>[(<scope>)][!]: <description>`, where type is one of feat, fix, perf, refactor, test, docs, build, ci, chore or
revert. A breaking change adds `!` and explains itself in a `BREAKING CHANGE:` paragraph. Release notes are generated
from these titles with [git-cliff](https://git-cliff.org/) (`cliff.toml`), so the description should say what the
change makes different for a user.

## License

By contributing you agree that your contribution is licensed under [Apache-2.0](LICENSE), like the rest of the project.
