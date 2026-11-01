# The newcomer's path: four user stories (11-01, r6)

Owner, 11-01: more and more people, not only engineers, will run many projects at once with AI agents, and agent-hub
is for them. The lowest bar should be "paste one line" or, for someone who never opens a terminal, "paste one sentence
to your agent". This design covers four user stories, the building blocks they need, how the project describes itself,
and what a user without an always-on machine gets.

Status: design, nothing implemented. To be scheduled by the release session.
r6: B0, the language is the first question (owner, 11-01). Since 11-01 the agent-hub session owns UX and builds this
design; the release session cuts releases.
r5: the threat model is stated; consent goes through a native dialog, not the page; onboard re-checks the allowlist.
r4: the local page is opened by init itself and its address never reaches the agent; connect resolves real paths and
refuses symlinks; onboarding's own git calls run with the folder's hooks off.
r3 fixes r2's review:
- secrets and user-level consent go through a local page the agent never sees (B3);
- `connect_project` is confined to folders the user named, outside the model's say (B5);
- the Codex preset's schema change is named (B4);
- the watchdog's grace after a resume, retention and the scheduler's real filter names (B7);
- git is run safely in folders being checked (B6).
r2: rewritten around the owner's user stories, after a cold review of r1 and a check of the desktop apps' docs.
Corrections:
- the Claude credential the platform actually uses;
- Codex goes through a command preset, not an adapter;
- claude is installed only through nvm;
- the order of steps inside init;
- install.sh pitfalls, macOS git detection, the Linux watchdog after a resume, and jobs tied to a calendar day;
- the project picker;
- positioning that overclaimed.

## The four stories

| # | Who | Uses | Wants |
|---|---|---|---|
| 1 | new user, can use a terminal | about to learn Claude Code or Codex CLI | install agent-hub and start a first project |
| 2 | new user, no terminal | about to learn Claude Desktop or the Codex (ChatGPT) desktop app | the same, without ever typing a command |
| 3 | already uses Claude Code or Codex CLI | has existing projects: single repos, a monorepo, or a project spread over several repos | connect them |
| 4 | already uses Claude Desktop or the Codex app | the same existing projects | connect them, from the app |

Every story ends in the same place:
- the platform installed and running;
- the user's agent knows agent-hub is there;
- projects connected.

From then on the user just talks to their agent.

## Where things stand (checked 11-01)

- **`install.sh`** (a43a72d) needs git and stops without it.
  - It installs uv at user level, clones the repo, checks out the newest release tag and runs `bin/setup`.
  - It ends with "Next: agenthub init".
  - On macOS, `command -v git` always passes, because `/usr/bin/git` is a stub that pops Apple's install dialog.
- **`bin/setup`** checks commands and installs none.
  - Required: git, jq, uv. Optional: claude, gh, restic.
  - On macOS its hints are `brew install …`, but it never checks for Homebrew.
  - jq is used only by `bin/hubpaths.sh`, and only where `plutil` is absent. Setup's stated reason ("the librarian
    reads JSON") is stale.
- **`agenthub init`**:
  - **What it asks:** the instance directory, the owner's name, time zone, where projects live, modules, and which
    model makes judgment calls. A provider then brings more questions: a key, and the standard/fast/strong models.
  - **No `claude` on PATH:** it sets `runtime.default: none` without a word (`bin/agenthub:314`). Doctor's fix line is
    generic.
  - **Interrupted init:** hub.json and the pointer are written before llm setup, so an init interrupted there never asks
    for a model again.
- **The platform's Claude credential.** Owner decision, 10-24: judgment points and agent runs use the headless token
  `~/.claude/headless-oauth-token`, from `claude setup-token`, by default (`bin/llm.py:30`). It works the same under
  launchd and SSH, and it is one thing to replace.
  - `runtime_claude.credentials()` always reports the subscription login as present, so it can't confirm a login.
  - A newcomer who logs into Claude and picks "the coding agent's own login" still gets every nightly judgment call
    failing until the token file exists.
- **claude must come from nvm's npm.** `hubconf.nvm_bin()`, known-issues.md, and the nightly `update-claude` that keeps
  that copy current all assume it. A second copy, e.g. from Anthropic's native installer, drifts.
- **Codex works today only through `runtime_command`** with a command template. That gives agent runs and judgment
  calls, but no hooks, sessions or credentials.
- **The desktop apps:**
  - **Claude Desktop's Code tab:**
    - It "runs the same underlying engine" and "reads the same configuration files" as the CLI: settings, hooks,
      CLAUDE.md/CLAUDE.local.md, and MCP servers in `~/.claude.json`.
    - It runs commands after a permission prompt in the app.
    - It runs **its own copy** of Claude Code, which it downloads and updates itself. So it needs no CLI, and the
      platform can't rely on that copy.
  - **The ChatGPT/Codex desktop app** shares MCP configuration with the Codex CLI ("for the same Codex host") and runs
    commands with approval.
    - Whether it runs Codex hooks and reads AGENTS.md the same way as the CLI is not documented.
    - Both apps run on macOS, Windows and Linux (Linux in beta).
- **Onboarding** is `agenthub onboard <folder>`. Without `github.owner` it fails unless given `--no-remote`, and the
  README and init's last line don't add it. It git-inits and commits the folder, and refuses a folder with
  uncommitted changes.
  - The `new-or-onboard-project` skill is project-level in the hub repo. It works only in a session opened in the hub's
    folder.
  - Its text still carries this deployment's history.
- **Projects and repos:** a monorepo holding several projects, or one project over several repos, needs the model in
  `docs/design/projects-and-repos.md` (r13). It is designed but not implemented. Today one project is one repo, or
  one folder.
- **A machine that sleeps:**
  - macOS: launchd fires missed intervals on wake, timed jobs catch up the same day (`scheduler.is_due`), and the
    watchdog needs two stale checks in a row, so there is no false alarm.
  - Linux: systemd timers pause during suspend. After a resume the 5-minute watchdog can fire twice before the
    15-minute scheduler ticks, which is a false alarm.
  - Jobs tied to a calendar day skip their whole period if that day was missed: reflect on Mondays, backup-verify on the
    1st, retention on Sundays.

## Building blocks

### B0. The language comes first

Today the platform speaks English and Traditional Chinese: `language.ui` sets what it shows the owner, and
`language.content` sets what agents and models write. But init never asks. It leaves `language` unset (en), and until
hub.json exists everything is English, init's questions included. A Chinese-speaking newcomer gets English throughout
and never learns that the choice exists.

**No translated text outside the catalogs.** `tests/test_i18n_static.py` rejects CJK in tracked files outside
`locales/`, fixtures and specs. `docs/design/i18n.md` has the scripts that run before the platform's Python print
English. So:
- install.sh and bin/setup stay English. They are a few lines before init, and that's honest: the platform's own
  language starts at init. B1's handover makes the switch immediate.
- init's first question is built from the catalog, not from literals. The English msgid and the zh-Hant catalog's
  translation (`i18n.translate(…, language="zh-Hant")`) are printed side by side. The language names come the same
  way, so `bin/agenthub` holds no Chinese.
- The README's zh-Hant paste text for story 2 (B3) lives in a translated README file (`README.zh-Hant.md`) added to the
  test's `SPEC` set with a comment ("translated docs"), in the same commit as the file, since the test requires listed
  files to exist.

**The default comes from the system.**
- One function in hubconf, `system_language()`, used by init. It maps to `zh-Hant` for `zh_TW`, `zh_HK`, `zh_MO` and
  any `zh-Hant*`; everything else maps to `en`. Simplified Chinese maps to `en` and says so in one line until a zh-Hans
  catalog exists.
- **macOS:** `AppleLanguages` first (`defaults read -g AppleLanguages`, a plist array parsed with `plistlib` from
  `defaults export -g -`), because `LANG` there follows the Region setting, not the preferred language.
- **Elsewhere:** `LC_ALL`, then `LC_MESSAGES`, then `LANG`.
- A table test covers the mapping.

**init's first question is the language**, asked before the instance directory, in both languages at once:
- the default is `system_language()`;
- choices: English, Traditional Chinese, and "a different language for notes than for the interface", which asks the
  second one;
- every later question is asked in the chosen language, because init sets `AGENT_HUB_LANG_UI` for its own process once
  the answer is in. `_lang` reads the environment on every call, catalogs are cached per language, and child processes
  (llm setup, `config apply`) inherit it;
- the answer goes into `language.ui` and `language.content`.

**Flags and agent mode.**
- `--lang <ui>[,<content>]` answers it without asking, for `--yes` and CI. Without it, `--yes` takes
  `system_language()`.
- In agent mode (B3), the agent first calls `--questions` and gets only the language question (`"via": "chat"`). The
  agent asks it in the language the user is chatting in. Then it calls `--questions --lang <answer>` and gets the rest
  in that language.

**Changing later.** `agenthub init --lang <ui>[,<content>]` on an existing instance changes only the language and
returns early, before init's re-run path (units, `config apply`). It:
- writes hub.json;
- commits that one file with `bin/commit-hub-files.sh`, as `schedulectl set-model` does;
- prints which long-running processes keep the old language until they restart (lease-runner, an open MCP server).

Short-lived commands, the status page, alerts and the watchdog read `language.ui` on every run and need nothing.

**What switching the content language does to existing files.**
- Written notes and STATE.md text stay as they are.
- The to-do section keeps its existing heading. Readers already take both token sets (`bin/todo_tokens.py`), but two
  to-do sections in one file count as a format problem (i18n.md), so the librarian must not add a second heading. New
  lines are written in the new set under the existing heading, which the contract allows.
- The librarian's prompt is told this, and an e2 eval run checks it before the switch ships.
- Doctor's first row names both languages and the command to change them.

**Tests:**
- `system_language()`'s table;
- a golden of init's language question in both languages;
- `init --lang` on an existing instance (only hub.json changes, one commit);
- the existing goldens are unaffected, since they pin the language.

### B1. install.sh goes on into init

- The whole script is a function called on the last line, so a cut-off download runs nothing.
- **Terminal input:**
  - With a terminal (probed with `( : </dev/tty )`, not `[ -t 1 ]`), it runs `"$DIR/bin/py" "$DIR/bin/agenthub" init
    </dev/tty`.
  - By full path, so a failed `install-cli` or a stale PATH doesn't matter.
  - Without a terminal, and with `AGENT_HUB_NO_INIT=1`, it prints the next step as today.
  - Every prompt reads from `/dev/tty` and defaults to "no" when there is no terminal.
- **If init fails**, the script says "rerun: agenthub init" instead of dying silently under `set -e`.
- **Pasting the line again** after a partial install doesn't stop at "already holds agent-hub". When the clone is there
  but no instance exists (no `.hub-home` / hub.json), it goes on to setup and init.
- **Flags:**
  - `--yes` through a pipe is documented as `curl … | sh -s -- --yes`.
  - `--for-agent` is B3's mode.
- **init itself:**
  - It writes hub.json only after every question is answered, or records which steps are unfinished. A re-run then asks
    the unfinished ones (llm setup included) instead of skipping them because hub.json exists.
  - `INIT_FLAGS` / `INIT_OPTIONS` gain `--agent` (B4), `--connect` (B6), `--lang` (B0), and `--questions` /
    `--answers` (B3); otherwise `init_argv_problem` rejects them.

### B2. Fewer prerequisites, and asked before installing

- **jq is no longer required on the hub machine.**
  - `hubpaths.sh` reads hub.json with the platform's Python when `bin/py` works, matching jq's output: scalars only,
    lowercase `true`/`false`, nothing for a missing key.
  - On any error from `bin/py` it falls back to `plutil`/jq.
  - jq stays optional, for the remote machine. The watchdog doesn't source hubpaths, so it is unaffected.
- **git on macOS:**
  - It is detected with `xcode-select -p`, not `command -v git`.
  - When it is missing, install.sh asks "Install Apple's command line tools now? [Y/n]" and runs `xcode-select
    --install`. That starts Apple's own installer and returns at once, so install.sh waits for it, polling `xcode-select
    -p`, and says so.
  - Over SSH the dialog can't appear, so install.sh says "run this on the machine's screen, or install the command line
    tools from developer.apple.com" and stops.
- **git on Linux:** it needs the package manager and sudo. install.sh shows the exact command, asks, and runs it only on
  yes, with the user typing their own password.
- **Homebrew is never required.** No step depends on it. bin/setup's hints say "with Homebrew: … / otherwise: …".
- **`--yes`** answers yes for unattended installs. The user stays the one who decides to install system software. The
  only change is that agent-hub offers to run the command.

### B3. Setup driven by an agent in a desktop app (stories 2 and 4)

Someone with no terminal has an agent that has one.

**What the user pastes into Claude Desktop's Code tab or the Codex app** (the README gives the exact text, in English
and zh-Hant):

> Install agent-hub for me from https://github.com/imaknas/agent-hub. Follow its "Set up through your agent" steps,
> ask me each question in this chat, and tell me before anything is installed.

**What the agent then does:**
1. Runs `curl … | sh -s -- --for-agent`. In this mode install.sh never prompts and never installs system software by
   itself. It reports missing prerequisites as structured lines, and the agent asks the user and runs the install
   command on yes. The app's own permission prompt is a second confirmation.
2. Runs `agenthub init --questions`, which prints the remaining questions as JSON: id, text in the UI language,
   default, choices. The agent asks them in the chat.
3. Runs `agenthub init --answers <file>`, which applies them non-interactively. Steps that need the user in person
   come back as `{"needs_user": …}` with what to do.

**Secrets and user-level consent never pass through the agent.** An answers file the agent writes is relayed consent,
and a key typed into the chat reaches the model provider. Both break platform rules: credentials live only in files
outside version control and are never shown, and user-level settings change only on the owner's direct confirmation.
Answers and keys travel separately:
- `--questions` marks each question `"via": "chat"`, `"via": "page"` or `"via": "dialog"`. API keys and the
  setup-token step (B4) are `page`. Consent is `dialog`: the yes to user-level registration (B5), installing system
  software (B2), and the project roots (B5's allowlist).
- For those, init starts a one-time local page on 127.0.0.1, at a random port with a random path, and **opens it in
  the user's browser itself** (`open` on macOS, `xdg-open` on Linux). It never prints the path. The agent gets only
  `{"needs_user": "answer the page agent-hub opened in your browser"}`. The page closes after one submission or 15
  minutes.
  - The URL is never printed to init's output or written to files. It does pass as an argument to `open` /
    `xdg-open` and the browser, so a same-user process can read it with `ps` and race the user to submit.
  - That is why the page carries **only secrets** (keys, the setup token). An agent that submits a key itself gains
    nothing it didn't have.
  - **Consent** is asked in a native dialog that init shows itself: `osascript -e 'display dialog …'` on macOS, `zenity`
    or `kdialog` on Linux, init's own terminal prompt otherwise. It covers the yes to user-level MCP registration (B5)
    and the yes to installing system software (B2) in agent mode. The dialog's answer goes straight to init's process
    and is never readable or submittable through a URL.
  - If no browser can be opened (no desktop session, SSH), the step falls back to init's own terminal prompt, and in
    agent mode returns `needs_user` with "run `agenthub init` in a terminal". It never falls back to printing the URL.
  - **Threat model, stated plainly.** This keeps the agent's *text channel* (what it types in chat, what it writes to
    an answers file) from standing in for the owner's consent or carrying secrets into the transcript. It is not a
    boundary against an agent that runs shell commands as the same user. Such an agent could already run `claude mcp
    add` or edit `~/.claude.json` directly, script a native dialog, or drive the screen with computer-use tools. That
    boundary is the app's own permission prompt and the user's choice of permission mode. The platform's job is that
    in normal operation, consent and secrets come from the owner directly and never pass through the model.
- init writes the values itself (mode 600) and only reports "done" to the agent.
- Consent counts as direct confirmation because the native dialog is answered by the owner on the owner's own screen.
  The agent's say-so never counts.

**Why the docs back this:**
- Both apps run commands after an in-app approval.
- Claude Desktop reads the same settings and hooks as the CLI.
- The ChatGPT/Codex app shares the CLI's MCP configuration.

The init dialogue is the same code path as the terminal one. Only the transport differs, so no question exists in one
and not the other.

### B4. The coding agent and the platform's credential

The platform needs an agent it can start unattended (`runtime.default`), separate from the app the user chats in.

**The init step** comes before the modules and llm setup (`llm_expected()` reads `runtime.default`):

```
Which AI agent should agent-hub use for its own work (nightly tidy-up, the mailbox)?
  1. Claude Code — everything (session hooks, the mailbox in your sessions, background work)
  2. Codex — agent runs and judgment calls; your sessions reach the mailbox through MCP only
  3. None for now — memory, schedules and the mailbox work; agent features stay off
```

**Claude Code:**
- The platform's copy comes from nvm (the rule above). Claude Desktop's built-in copy doesn't count.
- If `claude` from nvm is missing, init offers the steps (install nvm, then node, then `npm i -g` the CLI) and runs them
  on yes. All are user-level, nothing needs sudo. In story 2 the agent runs them for the user.
- **Credential.** init runs `claude setup-token` and writes the token to the platform's token file (mode 600). That is
  the documented route for unattended runs, and the owner's 10-24 decision.
  - setup-token needs a Pro, Max, Ultra, Team or Enterprise plan. Without one, init says so and offers an API key
    through `agenthub llm setup` instead.
  - **Check:** init makes one real request with that token, the same check as `check-claude-login`. It never asks the
    user to "press Enter when logged in".
  - init runs `claude setup-token` as its own subprocess and captures stdout. The token goes straight into the file
    and is never echoed. When an agent ran init, the agent sees only init's output, never the token.
  - **To verify before building:** whether `claude setup-token` can complete without typed input when started from
    init (it opens a browser). If it needs typed input, the local page (B3) runs it in a terminal the page shows, or
    offers the API-key route there.

**Codex:**
- Its preset (the command template, model names, how to check a login) lives in `modules/codex.yaml`. AGENTS.md
  allows tool syntax there. A module today is a feature switched on in hub.json, and `MODULE_TOP` rejects unknown
  fields (bin/registry.py), so this needs a schema change: a module may carry a `runtime_preset` (the command template
  plus `INSTALL`). Turning the module on makes the preset available to init. The schema and its validation change
  together, with tests.
- init writes `runtime.command.run` from it. The menu says plainly what it gives and what it doesn't.
- The native adapter is the next version's work (docs/design/mailbox-runtimes.md).

**Per-tool data is a constant, not a port.** An adapter or module declares `INSTALL = {steps, check, plans}`, like
`MODELS` / `THROWAWAY_ONLY`. The menu lists registered runtimes that declare it. "Installed" already means hub.json
`runtime.installed`, so it isn't used for this.

**`agenthub init --agent`** re-runs this step:
- it switches `runtime.default` from none;
- it re-offers llm setup and the modules that need an agent;
- it commits hub.json and runs `config apply`.

### B5. Every session knows agent-hub is there (stories 2, 3 and 4)

Today a session opened in the user's own project knows nothing of agent-hub until the project is onboarded. And it can
only learn to onboard in the hub's own folder.

- **init asks**, and the owner answering yes in init is direct consent to a user-level change: "Let your AI apps use
  agent-hub in every project? This registers agent-hub's MCP server for your user." On yes it runs the tool's own
  user-scope command:
  - `claude mcp add --scope user agenthub -- <hub>/bin/agenthub mcp`. It lands in `~/.claude.json`, which the Desktop
    Code tab reads too.
  - `codex mcp add agenthub -- …`, which is global and shared with the Codex app.
  - The commands live in the runtime's or module's `INSTALL` constant.
- **The MCP server outside any project.** Today every tool errors outside a project. Instead it offers:
  - `connect_project(path, …)`, which runs B6's checks and connects the folder;
  - `status`, a short "agent-hub is installed; N letters for you; projects: …".

  Its `instructions` say what agent-hub is and when to suggest connecting a folder.
- **Safety.** The model can be prompt-injected, so nothing here rests on the model asking first. The
  guarantees are mechanical:
  - **Only folders the user named.** init records the user's project roots (B6's "where are your existing projects?",
    confirmed in init's native dialog when an agent drives init). `connect_project` accepts only an existing folder whose resolved path
    (`realpath`) is strictly inside one of them. It refuses any symlink anywhere in the given path, so `ln -s ~
    ~/code/x` doesn't escape, and it never creates folders. `onboard-project.sh` itself switches from `cd "$1" && pwd`
    to `pwd -P`, so every caller works on the real path. It **re-checks the allowlist on that real path** when called
    for `connect_project`, which closes the gap between the check and the use: a folder swapped for a symlink in
    between is refused.
    It refuses `$HOME` and its ancestors, hidden folders, the hub, the instance directory, other projects, and anything
    else. A new root is added only by the owner, through `agenthub connect --add-root` or the local page.
  - **No remote through MCP.** It never creates a GitHub repo, sets a remote or pushes. That stays with `agenthub
    connect` run by the user.
  - **Not in platform-started runs:** it refuses under `HUB_LLM_POINT` and `HUBBUS_LEASE`.
  - **Confirmation outside the model where the client allows it.** When the client supports MCP elicitation, the
    server asks the user directly ("Connect ~/code/notes as a project? It will …"). Without elicitation, the root
    allowlist is the guarantee, and the tool's result tells the agent to show the user what was done.
  - In a sandboxed project the MCP server resolves to that project (`project_of` by path), so `connect_project` isn't
    offered there.
- This is tier 1: it works for any MCP-capable app with no per-tool code beyond the registration command.

### B6. Connecting existing folders (stories 3 and 4)

One flow, reached through `agenthub connect` (the new user-facing name for onboard; `onboard` stays as an alias), init's
`--connect`, or B5's `connect_project`.

1. **Where to look.** The user names a folder or picks one (init asks "where are your existing projects?"). Not the
   newly created, empty workspace root.
2. **What each candidate is**, found by reading the files. The folder isn't trusted yet, so every git call the
   connect flow makes in it runs with the folder's hooks and fsmonitor off (`-c core.hooksPath=/dev/null -c
   core.fsmonitor=`). That covers the unsaved-changes check and `onboard-project.sh`'s own calls (`remote get-url`,
   `rev-parse HEAD`, `status --porcelain`, `init`, `add`, `commit`), which today run with the folder's config.
   `onboard-project.sh` also hard-codes `~/git-origin` in `mini_sh` and in the registry line. It reads hub.json
   `git_origin` instead (`hubpaths.sh`), from the release session's newcomer test, 11-01:
   - a plain folder with no repo;
   - one repo, which the flow then checks for monorepo signs (`pnpm-workspace.yaml`, `lerna.json`, `go.work`, a Cargo
     `[workspace]`, several package manifests one level down);
   - a folder holding several repos (several `.git` one level down).
3. **What connecting does, said before it happens, in plain words:**
   - "agent-hub keeps this project's notes in a separate place, so your files don't change";
   - "a folder without version history gets it, with one first save of what is there now";
   - "a folder with unsaved changes is skipped until you save them".

   No GitHub repo is created unless `github.owner` is set and the user says yes.
4. **Layouts:**

| Found | Connected as | When |
|---|---|---|
| plain folder, or one repo | one project | now |
| a monorepo | one project for the whole repo | now (a valid case of the projects-and-repos model, not a stopgap) |
| a monorepo, as several projects | one project per part | after projects-and-repos steps land; the flow asks the question now and records the answer for then |
| a folder holding several repos | one project per repo | now |
| several repos as one project | one project spanning repos | after projects-and-repos steps land |

The flow asks the question for each layout, and only offers what can be done today. It never connects a part of a
monorepo as if it were a repo of its own. An answer for later ("split this monorepo into its parts") is recorded in the
project's registry entry as `layout_intent`. When the projects-and-repos steps land, `agenthub connect --apply-intents`
offers to carry it out.

### B7. A machine that sleeps is fine

**README:** the status note says "an always-on machine is recommended, not required", with this table:

| Feature | While the machine sleeps |
|---|---|
| Daily jobs (tidy-up, digest, to-dos, backup) | Run when it wakes, the same day |
| Weekly / monthly jobs (weekly reflection, backup check) | Run in the first tick of their period after waking (scheduler change below) |
| Backup retention (inside the daily backup, on Sundays) | Applied at the first backup of the week that hasn't applied it yet (backup.py change below) |
| Mailbox dispatch | Paused; letters wait |
| Background services (bots, webhooks, viewers) | Offline: the one real difference |
| Reaching sessions from a phone or the assistant | Unavailable |
| Event-triggered jobs | Handled after waking |

**Scheduler:** day filters are `days_of_week`, `days_of_month` and `months` (the registry's `days:` becomes these).
They are checked in `runs_today`. A job can opt in with `catch_up: period`: it becomes due at the first tick of its
period, the week or the month, when it hasn't run in that period. Today it is due only on the named day. The
platform's own weekly and monthly jobs (reflect, backup-verify) turn it on. Project jobs keep today's behaviour unless
they opt in, so nothing changes for them silently. Characterization tests on `runs_today` come first.

**Retention** isn't a scheduler filter: `bin/backup.py` checks `weekday() == 6` inside the daily run. It changes to
"the first backup of the week that hasn't applied retention", recorded in the backup's state.

**Watchdog on Linux:** after a resume it holds its alarm until the scheduler has had a chance to tick.
- It compares `CLOCK_BOOTTIME` with `CLOCK_MONOTONIC`. When the gap grew since the last check, the machine was
  suspended, and the watchdog records the resume time.
- systemd interval timers resume their remaining time, so a check at +10 minutes could otherwise alarm before the
  scheduler's first tick at +15. So no alarm is raised until the heartbeat is newer than the resume, or 20 minutes have
  passed since it. The 35-minute stale rule applies again after that.
- The clock constant is read with `getattr(time, "CLOCK_BOOTTIME", None)`. It is missing on macOS, where launchd fires
  missed intervals on wake, so the check is Linux-only.
- It keeps using the system python3 and the standard library.

**Watchdog channels** (from the release session's newcomer test, 11-01): the watchdog sends only to Discord. A
newcomer who sets up ntfy or email, as check-secrets suggests, sees "watchdog has no Discord settings" in red. The
watchdog sends through whichever channels hub.json `alerts` configures that it can reach with the standard library: an
HTTP POST for Discord and ntfy, `smtplib` for email, `osascript` for a macOS notification. It stays under the system
python3, with no platform imports, so it reads hub.json and the secrets files itself. The status page is red only when
no channel at all is set.

**Doctor:** an info row (not a warning) when the machine sleeps. On macOS it reads `pmset -g` for a sleep setting other
than 0, which covers desktops set to sleep. On Linux it checks for a battery or a suspend target. The row lists the
table above.

### B8. Plain words

- Everything a newcomer reads avoids "worktree", "bare repo", "registry" and "runtime": init, install.sh, doctor's
  newcomer rows, B5's MCP texts, the README quick start.
  - For example: "connect a folder as a project", "AI agent", "notes", "version history".
  - Design docs and AGENTS.md keep the exact terms.
- Each changed message goes through the zh-Hant catalog, and the goldens are updated on purpose.
- The public `new-or-onboard-project` skill loses this deployment's history.

## The stories, step by step

**Story 1: terminal, new to Claude Code / Codex CLI.**
1. Paste the one line. install.sh asks before installing Apple's tools (or the Linux packages).
2. init asks its questions. At the agent step it offers Claude Code or Codex, installs the CLI through nvm on yes, and
   runs the credential step.
3. init asks "let your AI apps use agent-hub everywhere?" (B5) and "where are your existing projects?" (B6), or offers
   to create a first one.
4. The README shows the first session: `cd` into the project and run `claude` / `codex`.

**Story 2: no terminal, new to Claude Desktop / the Codex app.**
1. Install the app from its website and log in. That is the app's own flow, and the README links it.
2. Paste B3's sentence into a new chat. The agent installs agent-hub, asking before each install. It asks init's
   questions in the chat, sets up the platform's agent, and does the credential step. Anything only the user can do
   comes back as one instruction.
3. B5's registration: from now on, any chat in the app in any folder can say "connect this folder" or "what's waiting
   for me".
4. The status page link (`http://localhost:18790`) is how the user sees health without a terminal.

**Story 3: terminal, existing projects.** Paste the line (or `agenthub upgrade` if already installed). Then:
- init, or `agenthub connect ~/code`, lists what it finds under that folder: repos, a monorepo, a folder of repos;
- it asks per layout (B6);
- existing sessions pick up the project's setup at their next start.

**Story 4: app, existing projects.** As story 2 for the install, where init's native dialog confirms the project roots.
Then, in a chat opened in the project's folder, say "connect this project to agent-hub".
- `connect_project` explains what it will do, asks about the layout (by elicitation where the app supports it), and
  connects.
- The user then starts a new chat in that folder: hooks and rules files load at session start.

## Positioning (decided 11-01)

**The owner picked a revised draft 1 (11-01), with cross-project memory first: settling and updating memory across
projects is why agent-hub exists.** Now in the README, the social preview and (on the owner's OK) the GitHub
description:
> **Many projects, run by AI agents. Each one remembers, and you decide.**
> Your agents write down what they do and learn. Every night agent-hub settles those notes into each project's current
> state, corrects what has changed, and gathers what matters across all your projects, so nothing is lost between
> sessions or tools. What only you can decide comes back to you.

The drafts it was chosen from:


**The owner, 11-01:**
- for anyone running many projects with AI agents, not only engineers;
- "one person" is doubtful: the decider can be a person, a team, or an agent the person authorised (Principal
  Handoff's `delegated` authority).

So the drafts drop "one person" and "coding", and make the subject the one who decides. "Tool-agnostic" moves from the
second paragraph into the feature list: supporting many agents is common now (docs/design/competitive-gaps.md), so it
doesn't set agent-hub apart.

**Draft 1 (recommended).**
> **Many projects, run by AI agents. You decide.**
> agent-hub keeps every project's notes, schedules and services going on a machine you own, and brings back to you only
> what is yours to decide.

**Draft 2.**
> **Run more projects than you can keep in your head.** Your agents do the work; agent-hub keeps the state, and asks you
> only what only you can answer.

(r1's draft 2, "the people who decide for them", is withdrawn: today there is one decider per installation.)

**The status note under it says what is true today:**
- one decider per installation;
- macOS or Linux;
- the agent features need an AI agent the platform can run (Claude Code gives everything, Codex a subset);
- the nightly judgment calls need a model;
- the owner's to-dos go to a personal assistant, or the built-in owner inbox;
- an always-on machine is recommended (B7);
- setting up without a terminal works through the user's AI app (B3).

Once picked, the text goes in the README's first lines, the GitHub description, and the social preview's tagline.

## Order of work

0. **B0**: the language question. It is first because every later message depends on it. **Done 11-01** (9f1a99b).
1. **B1 + B2**: install.sh goes on into init (pulled forward 11-01: it is what a newcomer notices first). **Done
   11-01**: install.sh is one function, asks before installing git (xcode-select on macOS, refused over SSH; the
   package manager on Linux), carries on after a half-done install, and runs init on /dev/tty; init finishes an
   unfinished llm setup on a re-run (`.state/init-unfinished`); hubpaths reads hub.json with bin/py, so jq is
   optional; bin/setup's macOS hints name Homebrew only when it is there. `--for-agent` is B3's.
2. **B7**: README table, the scheduler's period catch-up, the Linux watchdog, doctor's row. **Done 11-01**: `catch_up:
   period` (scheduler `missed_day`, registry, on for weekly-reflect and backup-verify), retention after a missed Sunday
   (`backup.retention_due`), the watchdog on every alert channel with a resume grace on Linux, the status page asking
   the same channel rules, doctor's sleep row (`pmset -g` sleep, or a battery on Linux), the README table.
3. **B4**: the agent step, the credential step, the Codex preset, `init --agent`. **Done 11-02**: init asks which
   agent the platform runs (offered from `install_info()` in runtime adapters and `runtime_preset` in modules:
   `runtime_claude`, `modules/codex.yaml`), offers the nvm-based install on a yes, and sets hub.json `runtime`;
   once hub.json exists, the credential step runs. Claude: `claude setup-token` runs in the terminal and its token
   is pasted into a hidden prompt, written mode 600 and checked with one real request (llm.py `login-check`), so it
   works whether or not setup-token wants typed input. Codex: `codex login status`, else `codex login`. `--agent
   <name>` answers it; on an existing instance it re-runs only this step, offers llm setup, commits hub.json alone
   and applies the config. Nothing is installed or logged in without a yes on the terminal (`--yes` doesn't).
4. **B6**: `agenthub connect` with layout detection, for the layouts that work today. **Done 11-02**: `bin/connect.py`
   (plain, repo, monorepo by workspace files or two manifests, a folder of repos), refusals on the real path (home
   and parents, hidden, the hub, the instance, inside a project), the unsaved-changes check with hooks and fsmonitor
   off, `--no-remote` unless `--remote` with github.owner, `layout_intent` (split / span) in the registry and both
   project schemas; onboard-project.sh uses `pwd -P`, runs its git in the folder with hooks off (`sgit`), and reads
   `git_origin`; init ends with "Where are your existing projects?" (`--connect`). The root allowlist and the MCP
   tool are B5's.
5. **B5**: user-level MCP registration and the server outside projects. **Done 11-02**: init offers registration
   after the credential step (`--register-mcp` on an existing instance), only on a yes on the terminal (`--yes` and
   no terminal register nothing); each tool's command is the adapter's or module's `mcp_register`. Roots are hub.json
   `connect.roots` (plus `workspace_root`), added by `agenthub connect --add-root` or init's existing-projects answer.
   Outside a project the server lists `status` and `connect_project`; `connect_project` checks `in_roots` (absolute,
   no symlink on the path, existing, strictly inside a root) and `connect`'s refusals, asks the user through MCP
   elicitation when the client offers it (a decline does nothing), never under a lease, and runs `agenthub connect
   --yes` (never a remote) with `AGENTHUB_CONNECT_ROOTS`, which onboard-project.sh re-checks on the real path before
   it creates anything. The local page and native dialog for roots are B3's.
6. **B3**: `init --questions/--answers` and `install.sh --for-agent`, then a dry run of story 2 in both apps. **Built
   11-02, the dry run not done yet**: every question init asks goes through `bin/init_dialogue.py` (Terminal, Recorder
   for `--questions`, Agent for `--answers`), each with a stable id. The answers map to init's own options and are
   validated strictly (known ids, text, choices); consents and secrets have no id in the file at all, and `--yes`
   doesn't go with either mode. Consent is `native_yes` (osascript with the question as an argument, zenity,
   kdialog; No by default, 10 minutes); a secret is `secret_page` (one page per value, random path never printed or
   logged, Host and Origin checked, one submission, 10 minutes). "Is there a screen" is an Aqua session on macOS,
   DISPLAY or WAYLAND_DISPLAY on Linux; without one, both come back as `needs_user`. In agent mode the setup token is
   pasted into the page after the user runs `claude setup-token` in a terminal (the app's own works); init doesn't run
   it for them. A provider's model is one answer (`llm_model`) for every group, like `--llm-model`. Owner steps left
   over keep `init-unfinished`, so the same `--answers` carries on. `install.sh --for-agent` never prompts and never
   runs sudo; on a Mac with a screen it opens Apple's installer window and returns `needs_user`. README has the
   pasteable prompt and the agent's steps; `README.zh-Hant.md` is new.
7. **B8**: the wording pass.
8. **Positioning**: done 11-01 (README, social preview, GitHub description).

**Files touched:**
- install.sh, bin/setup, bin/hubpaths.sh;
- bin/agenthub (init, doctor, connect), bin/onboard-project.sh, bin/mcp_server.py;
- bin/scheduler.py, bin/scheduler-watchdog.py;
- bin/runtime_claude.py (`INSTALL`), modules/codex.yaml (new);
- .claude/skills/new-or-onboard-project/SKILL.md, README.md, assets/logo/social.svg;
- locales.

**Waits on projects-and-repos:** a monorepo connected as several projects, and several repos connected as one project.

## Open questions

- Can `claude setup-token` finish without typed input when started by init (B4)? If not, the local page shows a
  terminal for it.
- Do Claude Desktop and the Codex app support MCP elicitation? Without it, B5 relies on the root allowlist alone.
- Does the ChatGPT/Codex desktop app run Codex hooks and read AGENTS.md like the CLI? It matters only once the native
  Codex adapter exists. Story 4 works without it, through B5's MCP.
- Windows: both apps run there, but agent-hub doesn't (no sandbox adapter, no service manager). The README says so.
