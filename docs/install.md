# Installing agent-hub

The one line from the [README](../README.md):

```bash
curl -fsSL https://raw.githubusercontent.com/imaknas/agent-hub/main/install.sh | sh
```

## What the installer does

The installer clones the repo into `agent-hub` in your home folder (`AGENT_HUB_DIR` to choose another), checks out the
newest release, installs [uv](https://docs.astral.sh/uv/) if it is missing, runs `bin/setup`, and goes straight on
into `agenthub init`, whose first question is the language. git is the one thing it may install, and it asks first
(on macOS, Apple's command line tools; on Linux, your package manager with your own `sudo` password). `bin/setup`
names anything else that is missing with the command to install it. Pasting the line again after an install that
stopped halfway carries on from where it stopped. Without a terminal (CI, a container) it stops after the install;
`curl … | sh -s -- --no-init` does the same on purpose.

After that, the README's [Getting started](../README.md#getting-started) takes over.

## By hand

Read the installer first if you like: [`install.sh`](../install.sh). The same by hand:

```bash
git clone <this repo URL> agent-hub && cd agent-hub   # any folder; hooks name it where it is
git checkout "$(git describe --tags --abbrev=0 --match 'v[0-9]*')"   # the newest release (stay on main to follow development)
bin/setup                            # check required tools, build the platform Python, put `agenthub` on PATH
agenthub init                        # the questions (open a new terminal first if agenthub isn't found)
```

With a model set up (`agenthub llm setup`) and the scheduler running, the next morning there is a digest and a `todo.md`
in the instance's data directory. Without a model they are not written: the librarian's judgment calls fail and say
why, and `agenthub doctor` reports that judgment points have nowhere to run until you set one up.

## Requirements

- macOS (launchd) or Linux (systemd user units). Project sandboxes use Seatbelt on macOS and Landlock on Linux (a
  declared listening port needs Landlock ABI 4, kernel 6.7 or later). The capability runner, which runs programs a
  sandbox blocks, uses Seatbelt, so it is macOS only.
- `git`, `jq` and [`uv`](https://docs.astral.sh/uv/). `uv` builds the platform's own Python 3.13 venv, so platform scripts
  don't depend on whichever `python3` is on PATH.
- **A model for the platform's judgment calls**, for the core payoff: the nightly digest, the upkeep of each `STATE.md`,
  and the items in `todo.md` (which are read from the to-do sections the model keeps in `STATE.md`) are all written by
  a model. It can be your coding agent's own login, an Anthropic or OpenAI-compatible API key, or a local Ollama; pick
  one with `agenthub llm setup` (`agenthub init` asks). Without one, the platform still runs: project memory folders,
  the mailbox, the scheduler and its jobs, the status page and the project registry all work, judgment calls fail and
  say why, and init leaves the modules that only do model work (consolidation, reflection) off.
- Optional:
  - a coding agent, such as [Claude Code](https://claude.com/claude-code) or any headless CLI agent. Without one, init
    sets `runtime.default: none`: agent features (dispatching agents to mailbox requests, session hooks) are then off,
    and `agenthub doctor` lists each one.
  - `gh`, to create GitHub repos on onboarding;
  - `restic`, for the backup module;
  - an alert channel: Discord, ntfy, email or macOS notifications;
  - Docker, for `bin/linux-test`.

## A machine that sleeps

An always-on machine is recommended, not required. On a laptop, or a desktop set to sleep, agent-hub catches up when
it wakes; `agenthub doctor` says when it sees a machine that sleeps.

| While the machine sleeps | What happens |
|---|---|
| Daily jobs (tidy-up, digest, to-dos, backup) | Run when it wakes, the same day |
| Weekly and monthly jobs (weekly reflection, backup check) | Run at the first tick after waking, if their day was missed |
| Backup retention (Sundays) | Applied at the first backup after a missed Sunday |
| Mailbox dispatch | Paused; letters wait |
| Background services (bots, webhooks, viewers) | Offline: the one real difference |
| Reaching sessions from a phone or the assistant | Unavailable |
| Event-triggered jobs | Handled after waking |

The watchdog doesn't raise a false alarm on waking (on Linux it waits for the scheduler's first tick after a resume).

## Without launchd or systemd

`agenthub init` installs the scheduler (a tick every 15 minutes) and its watchdog as launchd agents on macOS or systemd
user units on Linux, then applies the config, which installs the platform's background services (the status page,
mailbox dispatch, memory sync). `agenthub init --no-launchd` does neither: use it in a container or on a machine you are
only trying the platform on. A system with neither launchd nor a systemd user session (many containers, WSL without
systemd) ends up the same way, and init and `agenthub doctor` say so.

Then nothing runs by itself. Run a single job now with `agenthub run <job>` (`agenthub jobs` lists them), or schedule
the scheduler's tick yourself, every 15 minutes, with cron or similar: `<hub>/bin/py <hub>/bin/scheduler.py`.
`agenthub doctor` shows what is not running.

The status page is a static page. Every scheduler tick rewrites it as `.state/status/index.html` in the hub folder, and
the `local.status-page` service only serves that folder on `127.0.0.1:18790`. Without the service, open that file in a
browser, or serve the folder yourself: `bin/py -m http.server 18790 --bind 127.0.0.1 --directory .state/status` in the
hub folder. Without the scheduler, `bin/py bin/gen-status-page.py` writes it once.

## Where things live

The repo holds no user data. `agenthub init` creates an **instance directory**, `~/.agent-hub` by default, and
`<repo>/.hub-home` points to it:

| | What | Notes |
|---|---|---|
| `config/` | `hub.json`, the project registry (`projects/<name>.yaml`), `schedule.yaml` | A local git repo. `agenthub config check / plan / apply` |
| `data/` | digests, `todo.md`, review log, weekly reflections | Generated; not in git |
| `secrets/` | API keys, tokens, backup password | Never printed; the registry records names and locations only |

Projects live wherever you choose, `~/workspace/<name>` by default. Their memory bare repos and the librarian's read-only
copies sit beside them; the paths are set in `hub.json`.
