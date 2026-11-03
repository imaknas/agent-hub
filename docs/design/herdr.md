# brunna and herdr

Status: design, 11-03. Nothing built yet.

## Why

herdr (herdr.dev, Apache-2.0, about 48k stars on GitHub in November 2026) is a terminal workspace for running many
coding agents at once: a background server keeps each agent's terminal alive, a sidebar shows which agent is working,
blocked or done, and it runs on remote machines over SSH. The owner doesn't use it (iTerm2 with several Claude Code
sessions is enough). The point of supporting it is reach: herdr's users are exactly the people who run many agents and
then lose track of what each project knows, which is what brunna is for.

## How they relate

They are two layers, not competitors.

| | herdr | brunna |
|---|---|---|
| Time | now: the agents open right now, where they are, which one is stuck | over time: what each project knows, the nightly settling, schedules, the mailbox, what only the owner decides |
| Seen as | a window that is always open | mostly in the background: the to-do list, the status page, reminders in sessions |
| When an agent closes | nothing left | notes, state and to-dos stay; the next session carries on |

They overlap in two small places: both keep agent work running in the background, and both mark an agent that is
waiting for a person. brunna's tools already work inside any terminal, herdr's included: hooks, the MCP server and the
rules block don't care what draws the terminal. To confirm, not to build (see Proving it).

## What herdr offers to build on

From its own guide for agents (`herdr-app/defaults/AGENTS.md`) and herdr-core's page, as of 11-03:

- **Mods**: a folder `mods/<name>/` with `main.js` (an ES module whose default export extends `View`, drawn natively
  with gpui-kit: no HTML, no web view, no Node APIs), an optional `mod.json` (`place`: right / left / top / bottom /
  none, `size`, `focusable`, `overlay`) and images beside it. A mod can run a program (`exec(cmd, args, { cwd, on })`,
  `on` being one of herdr's machines), call herdr-core (`hc(args)`), `fetch`, show markdown (`TextView.markdown`),
  post a toast (`notify({ title, body, level, action })`), refresh on a timer (`cx.timer.every(ms, …)`) or when a folder
  changes (`watchFolder(dir)`), and read the agents and their state (`agents()`).
- **Sharing**: a public GitHub repo with the topic `herdr-mod`, each mod in `mods/<name>/` with a `RECIPE.md` (the
  brief an agent builds the mod from) and a `preview.png`; it appears on herdr.dev's Mods page within an hour, unreviewed.
  The repo must not have a `layouts/` folder. A user installs one by giving their agent a single line. On 11-03 the
  community list has no mod yet.
- **herdr-core** (`hc`): `hc run [--bg] [--name] [--on MACHINE] CMD`, `hc ls`, `hc read --lines N NAME`,
  `hc send NAME TEXT`, `hc wait`, `hc schema` (the socket API as OpenRPC, with streams and events).
- **OSC 7501**: a program in a herdr terminal reports its own state (`state=working|blocked|idle|done`, `app=…`) with an
  escape sequence; herdr shows it, mods read it.

## Phase 1: a glance, and a mod that shows it

### `brunna glance --json`

One neutral summary of what needs the owner and whether the platform is healthy, in a versioned contract
(`contracts/glance.v1.schema.json`, listed under the platform's `provides` so `tests/test_contracts.py` validates its
example). It is not herdr-specific: the same output feeds an iTerm2 status bar component, a menu-bar tool (SwiftBar,
xbar), or a script.

**Where the facts come from.** The status page's alert collection can't simply be called every minute: it refreshes the
registry (`scheduler.all_jobs()` → `registry.refresh()`, which rewrites the index), runs one git job per project
(`worktrees.overview()`), asks launchd and runs `hubbus status`. So the work is split by cost:

- **Per tick** (every 15 minutes, already running): `gen-status-page.py` collects its alerts once, as now, and also
  writes them, with the owner's open items, as `.state/status/glance.json` (atomic replace). The alert collection moves
  into a function the page and this file both use, so they never disagree. The owner's items come from
  `owner.open_items()` (to-dos and questions, for the built-in owner side and the assistant alike; it folds them from
  the bus records, one `hubbus records` per project, which is why it runs per tick and not per glance); a to-do's
  `since` is its raised record's `ts`.
- **Per glance** (cheap, live): the mail counts, by calling hubbus's functions directly with no project of its own (so
  the folder it runs in doesn't narrow it to one project's view), and the scheduler's heartbeat time.

So alerts and owner items are up to one tick old; mail and the scheduler's last tick are current. `glance` takes no write
lock in normal operation. An instance that isn't set up (no settings file) returns `{"set_up": false}` and exit 0
without touching the bus (opening it would create it); a missing or older `glance.json` (no tick yet, an older release)
is reported as such, never guessed.

**Fields:** `version`, `at`, `set_up`, `instance` (the instance folder, so a client can tell instances apart);
`needs_you` (`{kind: question|todo, project, id, text, since}`); `mail` (per project: open letters, the oldest one's
age; letters stuck past the platform's hour); `health` (`ok` or `problem`, and the alerts as `{level: bad|notify,
text}`, with `as_of`, the tick that wrote them); `scheduler` (last tick, alive); `jobs_failed` (names and when);
`status_page` (a URL and where it is reachable: the setting `status_page.url` in the settings file, new and optional;
without it `http://127.0.0.1:18790/` marked `local_to: hub` so a client on another machine doesn't offer a link that
opens its own localhost).

Alert texts are in the interface language, as the status page's are; letter and to-do texts are in the content language
they were written in. `brunna glance` without `--json` prints the same in a few lines for a terminal.

### The mod, in a repo of its own

herdr lists a mod from a public repo tagged `herdr-mod` with the mod at `mods/<name>/` and no `layouts/` folder. It
goes in a separate public repo (proposed: `imaknas/brunna-herdr`), not in this one: AGENTS.md keeps tool syntax in the
platform's adapters (`runtime_*`, `provider_*`, `modules/<tool>.yaml`), a `mods/` folder here would sit beside
`modules/` (opt-in features, unrelated), and herdr's sharing rules would bind the platform repo. The mod's repo
depends on nothing but the `glance` contract; its README leads to brunna.

- `mods/brunna/main.js`: a right-hand panel (`mod.json` `{"place": "right", "size": 320}`) that runs `glance --json`
  every 60 seconds (`cx.timer.every`) and renders it as markdown: what needs the owner first, then mail, then health; a
  line per item, the project in front; the age of the alerts (`as_of`) shown.
- A toast (`notify`) when an item id not seen in the previous glance needs the owner, at most one per minute, its
  action opening the status page when the URL is reachable from this machine. (brunna's own `owner notify` already
  sends new items to the owner's alert channels; the toast is only this window's echo of the same.)
- `keys.json` binds `brunna.refresh` and `brunna.open-status` to the mod's commands (`nextCommand()`).
- Where brunna runs: two props. `cli`, the absolute path of the CLI (default `~/.local/bin/brunna`, install-cli's
  wrapper: an app started from the GUI, or herdr's remote `exec`, usually doesn't have `~/.local/bin` on its PATH);
  `machine`, a herdr machine name passed as `exec`'s `on` when the hub is another computer. When the CLI isn't there,
  the panel says how to install brunna and stops polling.
- `RECIPE.md` (the brief) and `preview.png` (a screenshot of the panel) beside it, as herdr's sharing rules ask.

The mod reads `glance` and nothing else: no bus, no files, no herdr API beyond drawing, the timer, `exec` and `notify`.
The platform owns its data and its meaning (tool neutrality); the mod is a thin client that can be replaced.

### Publishing

The mod's repo gets the GitHub topic `herdr-mod` (the owner's decision, an outward step). This repo's README gets a
line "works with herdr" linking `docs/herdr.md`, which says: brunna's tools work inside herdr as they are; the mod and
where it is; the glance command for other status bars.

## Phase 2, later and only on demand: herdr as a session host

What brunna lacks today and herdr could give: agents without hooks (Codex, OpenCode, Gemini CLI) reach the mailbox only
when they call the MCP server; nothing wakes them. Inside herdr they could be:

- **seen**: `hc ls` (or the socket API) lists live terminals; with each terminal's folder, brunna knows a project has a
  live session (presence) without a hook;
- **woken**: `hc send <terminal> "📬 … run brunna inbox"`, the fixed notice Claude's waiter shows, only when the agent
  is idle (not mid-task);
- **watched**: brunna's own background handlers report OSC 7501 (`app=brunna`) when they run in a herdr terminal.

This is a session host beside the runtimes (`runtime_ports` stays as it is): a `host_herdr.py` adapter with `live()`
and `wake(terminal, text)`, chosen when herdr-core is installed. Hard rule: `hc send` types into the agent's input,
where it reads as the user's own words, and into a shell if the agent has exited (an agent reported `done` may have
left its pane at a prompt, where the text would run as a command). So all of these hold, or nothing is sent:
  - the terminal's foreground process is a known agent runtime, checked by brunna itself, not taken from the
    agent's own OSC 7501 report (a program can report any state);
  - the agent is idle, not mid-task;
  - the text is the fixed notice and nothing else: no field taken from a letter, not even the sender or the project
    named in it (letters are data, not instructions, the rule the Claude waiter follows), and no Enter is sent where
    a shell could be reading. Before building it: install
herdr-core in the Linux test container, read `hc schema`, and check how a terminal's folder and an agent's state are
exposed. Not before someone uses brunna with herdr.

## Proving it

- `glance`: a contract test (the schema validates the output of a fixture instance: an open question, a waiting to-do,
  a stuck letter, a failed job, a stopped scheduler); the not-set-up case leaves no bus behind; run from a sandboxed
  project's folder it still shows every project; the status page and glance.json carry the same alerts (one function);
  glance takes no write lock (a test holds the bus's write lock and glance still answers).
- The mod (in its own repo): a check that `mod.json` / `keys.json` are valid and `main.js` uses only the documented
  API names; then one real run: herdr's app on a Mac, the mod installed from `RECIPE.md` by an agent, a screenshot for
  `preview.png`. herdr's app is a desktop app, so this run can't happen in CI; the owner decides where.
- Compatibility: herdr-core (Linux) in the test container, a Claude Code session in a pane inside a connected project:
  the hooks fire, the rules block loads, a letter reaches the session. Recorded in `docs/herdr.md`.

## Open

- The mod's repo: its name (`imaknas/brunna-herdr` proposed) and creating it (the owner's: a new public repo).
- Where the mod's one real run happens (herdr's app on the owner's Mac, removed afterwards, or another Mac).
- Adding the `herdr-mod` topic to the mod's repo (the owner's, when the mod has run once).

Review (11-03, one cold round): the per-minute cost of the status page's alert collection (now per tick, read from a
file), `hc send` reaching a shell (now: a known runtime in front, no letter-derived field), the folder narrowing
hubbus's view, the status page URL off the hub machine, PATH in GUI apps, and the mod's place (now its own repo).
