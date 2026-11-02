# Changelog

What changed in each version, generated from the commit messages (git cliff) when it is tagged; only new features,
fixes, speed-ups and reverts are listed.

## v0.6.1 (2026-11-02)

### Fixed

- **confine:** An unattended job can write its own state folder
- **hubbus:** Letters to the platform's other name stay in its mailbox

## v0.6.0 (2026-11-02)

### New

- **names:** One table of old and new names, and hubconf reads env vars through it
- **names:** The settings file is hub.json or brunna.json, whichever exists
- **names:** The platform's folder in each repo is .git/agent-hub or .git/brunna, whichever exists
- **names:** Rules-file blocks, hook marks and the CLI are recognised in both spellings
- **names:** The platform answers to either name, and its party ids are their own class
- **registry:** The platform's own entry is type: platform
- **names:** Job state, core units and services carry over between the platform's two names
- **names:** The shell side and the watchdog read both spellings
- **names:** Services.tsv, recover-service and contracts consumes take either platform name
- **hubbus:** A notice replying to a live session wakes it

### Fixed

- **upgrade:** An install-cli wrapper is recognised by its exec line too
- **watchdog:** It loads on the system python3 again
- **llm:** A docstring that names print-mode claude no longer trips the call lint
- **hubconf:** A HOME that isn't the account's has no service manager
- **render-services:** A generated unit that isn't loaded is loaded again

## v0.5.0 (2026-11-01)

### New

- **uninstall:** Agenthub uninstall takes agent-hub off a machine
- **wake:** An idle Claude Code session is woken for a letter by an asyncRewake waiter
- **connect:** A first summary the day a project is connected
- **sessions:** A forked Claude session counts as live from its hooks' record
- **test-runner:** A run log of every tier result, hits and callers included
- **test-waiting:** The waiting report, nightly and on the status page
- **librarian:** Switch each part to its completion version, shadow the to-do format fix

### Fixed

- **wake:** A claude -p run never arms the waiter, told from its own arguments
- **schedulectl:** Run without a service manager closes the job's output pipe
- **ph_records:** A 0.4 record is validated by 0.4's rules, not as its 0.5 form
- **render-settings:** A platform update to its own hooks is not reported as drift
- **init:** An answered question isn't listed again, and the agent steps say what a test run found
- **sessions:** Agent-hub's own sessions record their process too
- **mailbox:** Mailbox handlers and allow: clock jobs load only the platform's settings
- **mailbox:** A confined handler can withdraw its own question, and an Edit rule brings Write

## v0.4.0 (2026-11-01)

### New

- **init:** The language is the first question
- **install:** One line installs and goes on into init
- **scheduler:** A machine that sleeps catches up
- **init:** Init asks which AI agent the platform runs, and sets up its login
- **connect:** Agenthub connect brings existing folders in as projects
- **mcp:** Every session can reach agent-hub, and connect folders the owner named
- **ph_wire:** Canonical() is RFC 8785 in full
- **init:** An agent can drive init, and the owner still answers consents and secrets
- **install:** Install.sh --for-agent, and the README steps an agent follows
- **ux:** Plain words for what a newcomer reads

### Fixed

- **tests:** The README's personal-name baseline counts the install line's repo URL
- **records:** Read a renamed project's records filed under its former names
- **hubbus:** Question views read Decisions records through the settlement view
- **alert:** Every channel is tried when one fails
- **evals:** A tagged old arm sends the recorded prompt
- **propose:** The change listing compares bytes, so a planted attribute can't make it copy back an untouched file
- **mcp:** Connect_project's happy path is pinned, and add_root refuses what it should
- **registrar:** A record id already sent is not re-created and sent again
- **hubbus:** A record id sent with other content is kept; the greater RFC 8785 form prevails
- **init:** An answer can't be an option, and no key page opens without a model
- **init:** Agent-hub never receives or writes the Claude setup token

## v0.3.0 (2026-11-01)

### New

- **install:** A one-line installer, and CI on macOS
- **records:** Read Principal Handoff 0.5, send the version hub.json names
- **acts:** The registrar follows the Acts Markdown binding 0.5
- **registrar:** Migrate-v1 gives each owner to-do that exists only as a v1 letter a need record under its key

### Fixed

- **seatbelt:** A sandboxed process can stat the folders above its own
- **upgrade-idea:** A git that fails is not read as a detached HEAD

### Changed (breaking)

- **registrar:** Retire v1_compat; only records count (**breaking:** an owner to-do that exists only as a v1 task letter is no longer held; its line is raised again.
Run registrar.py migrate-v1 --apply (the previous commit) before this one lands.)

## v0.2.0 (2026-11-01)

### New

- **agenthub:** Upgrade to a release in one command
- **mailbox:** An MCP agent's presence holds back dispatch
- **agenthub:** Without a model, init leaves the LLM-only modules off
- **mailbox:** A wake port that nudges a live session once per letter
- **runtime:** Runtime.installed, session ids per runtime, and calls routed by runtime_of

### Fixed

- **agenthub:** Init acts only on arguments it knows; core services only from the main checkout
- **jobs:** Connectors jobs no longer die on macOS's bash before running
- **jobs:** Connectors jobs deny every MCP server they don't use
- **jobs:** A connectors job's deny also covers MCP resources
- **registry:** A connectors job needs an allow: list
- **no-llm:** A judgment point with no model is switched off once, with the reason
- **status:** Features that are off or not set up no longer turn the status page red
- **reflect:** The weekly report counts hours in the owner's time zone, and runs without system tzdata
- **reflect:** The no-transcripts notice goes to stderr, so the wrapper commits the json it wrote
- **schedule:** Without a service manager a job runs by hand, and the hints are absolute and per OS
- **todo:** Todo.md points at openclaw-migration-status.md only where that file exists
- **services:** The memory-sync service takes its default label, without the author's machine name
- **onboard:** Onboarding says only what it set up, and AGENT.md stops naming Claude Code's files
- **agenthub:** Init commits the config repo, and says when git has no identity
- **agenthub:** Doctor and init say plainly what runtime none and an uninstalled scheduler mean
- **agenthub:** Without a service manager, init and doctor give the absolute scheduler command

## v0.1.0 (2026-11-01)

### New

- Add onboarded-projects.tsv — central manifest of every onboarded project
- Add recover-project.sh; move templates/ into agent-hub
- Harden onboard-project.sh — auto-create everything mechanizable
- Add services.tsv + recover-service.sh — mirrors onboarded-projects.tsv for launchd services
- Add overview.sh — live aggregated view of projects+services+memory
- **merge-branch:** Conventional Commits for commit titles
- **evals:** Record the librarian's nightly inputs as eval cases
- **evals:** E2 and E3 harnesses for the translation work
- **i18n:** Language settings, per-script catalogs, commit_title first
- **i18n:** Hub_msg and hub_say for shell scripts
- **i18n:** Plural choices, and module descriptions through a catalog
- **i18n:** Plural choices in the mailbox, lease and runtime messages
- **librarian:** English prompts that pass E2 against the Chinese ones
- **sandbox:** Sandboxed projects' services and jobs run isolated on Linux too
- **llm:** Projects' own model calls through the platform (agenthub complete)
- **llm:** Usage rows say how a call is paid; the status page marks subscription cost as not billed
- **consolidate:** English prompt that passes E2 against the Chinese one
- **reflect-week:** English prompt that passes E2 against the Chinese one
- **mailbox-propose:** English prompt that passes E2 against the Chinese one
- **lease-handover:** English prompt that passes E2 against the Chinese one
- **evals:** E3 scenarios for the mailbox prompt and the MCP instructions
- **evals:** E3 reports STATE.md edits in note and memory notes per letter
- **rules:** English agent rules that pass E3 against the Chinese ones
- **hubbus:** Questions to the owner go as Decisions records
- **evals:** E3 scenarios for the branch copies and the lease preamble
- **hubbus:** English prompts for the background branch copies
- **mcp:** English tool and parameter descriptions
- **lease:** English lease instructions with the configured names
- **contracts:** State-todo.v2 and todo-md.v2
- **todo:** Readers accept the English to-do tokens
- **todo:** Writers follow the content language
- **evals:** E3 owner_todo checks the to-do token set
- **todo:** The English set is agent-ops-specs' en profile
- **isolation:** Adapters declare their guarantees, one conformance run checks them
- **lease:** A handover names the to-dos already waiting instead of raising them again
- **anchors:** Resolve STATE.md code anchors against the base branch
- **librarian:** Write code anchors in STATE.md and check them nightly
- **librarian:** Admit only anchors that resolve when written
- **scheduler:** Event-triggered jobs
- **scheduler:** Every run has a time limit
- **runtime:** An Agent Client Protocol runtime adapter
- **i18n:** Match another process's text in every catalog language
- **consolidate:** Hedge lists are per-language data
- **i18n:** Hub_content for content a shell script writes
- **reflect-week:** V2, fixed English values; the page in the UI language (**breaking:** weeks written from now on carry "v": 2 and English
classification values; a reader of v1 (the assistant vendors it) reads the
fields it knows unchanged, but the three classifications only through v2.)
- **hub:** Agent-hub works from linked worktrees
- **registry:** Project v2 schema and its validation, not yet live
- **tests:** Agenthub test, the test runner and the result store
- **hooks:** The platform's git hooks follow gate, commit_gate and conventional commits
- **tests:** Agent-hub switches to the test runner (**breaking:** agent-hub's commit gate is the platform's pre-commit in its
git dir; githooks/pre-commit, post-commit and commit-msg are gone.)
- **acts:** The registrar and the to-do readers follow Principal Handoff Acts 0.4
- **records:** Agent-hub sends Principal Handoff 0.4 records
- **registry:** Upgrade-files rewrites v1 files as v2; a nightly test job per project
- **registry:** New entries are written as project v2
- **templates:** Every project's agents are told how tests run
- **modules:** Testcontainers removes test containers more than a day old
- **runtime:** Transcripts carry entry ids, folders, subagents and command calls
- **agenthub:** --version prints the release tag the checkout is at

### Fixed

- Onboard-project.sh keeps .claude/settings.json from being hidden by the project's own .gitignore
- Overview.sh also looks for STATE.md in the hub machine's memory-only clone
- Agent-hub's own SessionEnd hook was still using git-sync-push.sh
- **test:** The CJK ratchet checked nothing inside the commit gate
- **evals:** Seal E3's agent runs from the environment's MCP servers
- **i18n:** A bad language in hub.json is reported, not raised
- **registry:** Owner tag from settings in the security-field message
- **i18n:** Messages still print when hub.json does not parse
- **gen-status-page:** The page's lang attribute follows language.ui
- **render-services:** A space after the colon of a changed-keys detail in English
- **merge-branch:** Refuse a project the validated registry does not have
- **registry:** An index is not rebuilt from another or an empty registry dir
- **i18n:** A usage docstring's braces are text, not placeholders
- **sync-all-memory:** No stray "1" in the offline commit log line
- **onboard-project:** The hub-machine origin check needs a server name and an ssh host
- **recover-service:** Exit 0 when a service has no recreate notes
- **contracts-check:** Close the bus connection after reading events
- **check-secrets:** Say expired N days ago / expires today, not negative days left
- **i18n:** English wording and plurals in the librarian and consolidate
- **i18n:** English wording and plurals in backup and the nightly checks
- **i18n:** English schedules and status page wording
- **i18n:** English wording and plural choices in agenthub and the platform tools
- **i18n:** English wording in migrate, idea sandbox, rename and commit-title messages
- **i18n:** English wording in the project and setup shell scripts
- **i18n:** English wording in the mailbox, lease, runtime and LLM messages
- **i18n:** Hubbus names the assistant where a bare noun reads right
- **consolidate:** Check the to-do block with the platform's Python, not python3 from PATH
- **i18n:** Hub-runtime's usage through the catalog; English comment in the CLI wrapper
- **i18n:** Module secrets' rotate text through the catalog
- **config:** Hub.example.json leaves the assistant's name unset
- **registry:** A refused rebuild records who tried
- **sandbox:** A sandboxed project's own services and jobs run sandboxed
- **memory:** The session-end sync commits only memory/ and STATE.md, in its own checkout
- **sandbox:** Landlock run test leaves HOME alone; a missing command exits 127
- **linux:** The 11 golden failures on Linux
- **librarian:** The to-do format repair keeps every existing tag
- **evals:** U+9AA8 is not a Simplified-only character
- **propose:** Changes a hook committed inside the worktree are still adopted
- **evals:** U+96C7 is not a Simplified-only character
- **registrar:** Read every to-do section of a file
- **registrar:** A key on any to-do line counts as present
- **edits:** Compare protected markers by key
- **todo:** Report a to-do label without its checkbox
- **registrar:** Only claim or relink items raised from a line
- **registrar:** Strike only in the to-do section; a warning is not forever
- **hubbus:** A task sent again under its key amends it; withdrawals name the need
- **memory:** Check memory copies out without symlinks
- **policy:** Own tool caches per repo, other projects' memory unreadable
- **sandbox:** Run git in a project's repo only as the project
- **worktrees:** The nightly cleanup names a project it could not read
- **sandbox:** Bound every project job; no git in a sandboxed repo hourly
- **anchors:** Ignored build output is not deleted; the stale note keeps statements
- **anchors:** Submodules resolve; removing an anchor means the sign and its path
- **evals:** Stale_kept leaves out statements that are only an anchor
- **evals:** Anchor retention leaves out the anchors the stale note named
- **librarian:** An edit undone by admission still completes the run
- **registry:** Paths is a security field
- **hubconf:** The registry fallback keeps enforce.sandbox
- **sandbox:** Hooks, upgrades and runners touch a sandboxed repo only as the project
- **merge:** --project must name the project that owns the repo
- **sandbox:** Nudge only after a git write; every YAML true counts as sandboxed
- **scheduler:** Agent event jobs run in the workspace; review round 3
- **scheduler:** Agent event jobs load only the platform's settings
- **scheduler:** Confine agent event jobs on every runtime; review round 5
- **scheduler:** Sandboxed Bash isn't auto-approved in event jobs
- **hubbus:** Confined runs mail only as and for their own project
- **hubbus:** A confined run can't pick ids, reply across projects or list others' questions
- **sandbox:** Find a hook's repo from the .git pointer files, not git
- **sandbox:** The settings file in a project's folder is never followed or waited on
- **reflect-week:** V2 values named after v1's words; E2 against main
- **sandbox:** A sandboxed project can't write what its sessions load hooks from
- **hubbus:** A sandboxed project's letters go through its own outbox
- **hubbus:** Outbox only for the project itself, views on delivery, task keys kept
- **tests:** The runner's runs live in the main checkout, not under .git
- **tests:** Tiers get a TMPDIR outside every repo and the admin tools on PATH
- **hubbus:** A project's own task keys leave the registrar's prefix; STATE.md items change only by their line
- **hubbus:** An assistant record not taken in is never acked past; 0.4 records are taken
- **i18n:** Translations name the owner and assistant from the settings
- **memory:** A project's memory remote is called memory, not the hub machine's nickname
- **hooks:** The gate's refusal names the branch in every language
- **contracts:** The project schemas name models and metered_models
- **tests:** The Linux container reaps orphans like a real host
- **hooks:** Hooks and shipped config name the hub where it is, not ~/agent-hub

### Faster

- **test-gate:** Run the tests in parallel, one process per module
- **test-gate:** Skip a tree that already passed; run slow modules test by test
- **tests:** Tiers keep a cache folder; the Landlock plan tests walk their own folder
- **tests:** Agent-hub's full tier keeps run-tests' durations in its tier cache

### Changed (breaking)

- Remove knas-tasks.py, replaced by registrar.py (**breaking:** bin/knas-tasks.py is gone; run bin/registrar.py.)

