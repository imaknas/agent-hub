# Changelog

What changed in each version, generated from the commit messages (git cliff) when it is tagged; only new features,
fixes, speed-ups and reverts are listed.

## v0.15.0 (2026-11-09)

### New

- **memlock:** One writer at a time in a memory copy
- **state:** Restore keeps today's to-do list and publishes with a compare-and-swap
- **librarian,registrar,consolidate:** Take the memory copy's writer lock

### Fixed

- **state:** Restore refuses a copy a fast-forward can't move, and lists every open item it leaves out

## v0.14.0 (2026-11-09)

### New

- **hubbus:** Done closes only letters to this project; a sender takes one back with --withdraw
- **hubbus:** Questions to the owner carry options and a default

## v0.13.1 (2026-11-08)

### Fixed

- **worktree-memory:** Write memory notes from a worktree without a prompt
- **worktree-memory:** Exclude the worktree's settings file it now writes itself
- **hubconf:** Move the bus and relink .logs/hubbus.log only for the root's own instance

## v0.13.0 (2026-11-08)

### Fixed

- **seatbelt:** Deny a linked path by its target too
- **hooks:** A sandboxed project's tool call with nothing to nudge is no failure
- **seatbelt:** A write entry under a linked folder is allowed by its target
- **policy:** One deny set for a sandboxed project's services and its sessions
- **sandbox:** A sandboxed project's sessions are denied what its services are
- **backup:** Verify restores into the private temp folder
- **policy:** Name the platform's Python variable through names.env
- **state:** What sandboxed code reads moves out of .state
- **policy:** .state is denied whole to a sandboxed project
- **state:** The move out of .state only from the main checkout, at land, and never silently
- **policy:** A confined job's own folder as its cwd stays readable
- **state:** Two renames records are merged by key, never picked between
- **test-runner:** A sandboxed project's tested copy leaves out the files its sandbox may not create
- **runtime:** The worktree hooks name their runtime, so another runtime.default can't take them
- **runtime:** The command runtime runs only mailbox-propose in the throwaway copy (**breaking:** runtime.default: command is refused; brunna upgrade moves it
to none. Judgment points no longer run on a command template: set up an LLM
provider, or another default runtime with mailbox-propose pinned on command.)
- **doctor:** The feature table asks for the ports the code needs
- **hub-event:** A hook that doesn't name its runtime is Claude Code's, never runtime.default's
- **config:** Config migrate brings every setting v0.12.0 took in line, and never commits the owner's edits (**breaking:** brunna upgrade (config migrate) removes llm.points pins of
command on any point but mailbox-propose, and llm.providers entries with `impl: command` together with the llm.use groups and point pins naming them.
Going back to an earlier release doesn't restore them.)
- **doctor:** The librarian's row counts its answer-only fallback, consolidate gets its own
- **completion:** A target runtime with no completion interface is a refusal, not an exception

## v0.12.0 (2026-11-08)

### New

- **upgrade:** Keep the platform's own entries in the user's tool settings current

### Fixed

- **runtime:** Odd hook shapes crash neither upgrade nor doctor, and the owner's text keeps its form

## v0.11.0 (2026-11-08)

### New

- **ph:** Read Principal Handoff 0.6 and fold in it
- **alert:** Platform alerts can also go to the personal assistant
- **hubbus:** A letter's state is per session, and a route, an ack and a fork all end
- **hubbus:** A headless handler triages and takes nothing
- **alerts:** Say so on the other channels when the assistant's pushes stop reaching the phone
- **alerts:** No phone subscribed is a problem, and a phone off all day is allowed for
- **hubbus:** The session record: project, waiter lifetime, last reply, status ports
- **hubbus:** Mail and a session's record give one answer for its project
- **hubbus:** Pass, for a session a letter was routed to and isn't its
- **registry:** Mailbox.wake, an off switch manifest.update can't change
- **hubbus:** Route a request to a live session first, and wake it
- **doctor:** Say which memory branch holds more than notes, and what a runtime leaves
- **llm:** Max_attempts for completion points, and an answer that says nothing when it fails
- **hubbus:** Mailbox-route, a small model's pick of which live session gets a letter

### Fixed

- **reflect:** Re-running a week keeps the analysis the model wrote
- **ph:** Send 0.5 records as 0.6 after the switch; a malformed 0.4 record is invalid, not an error
- **hubbus:** A batch kept for resending is judged when it goes
- **ph:** A record that names no extension is invalid, not kept
- **records:** The platform's former name goes on being pulled
- **hubbus:** One outbox file can't stop the outbox or the next project
- **hubbus:** A session is judged closed only on good evidence
- **hubbus:** Old letters keep alerting, leases stay quiet, a fork gets credit only for its own
- **runtime:** One process record ps rejects is gone, not a failed lookup
- **hubbus:** Nothing in one outbox file stops the rest, a point isn't an outbox's word, worktree sessions end cleanly
- **hubbus:** What the cold review of triage found
- **jobs:** A failed detach is a failed start
- **hubbus:** What breaks on one project stays with that project
- **hubbus:** A confined run's question on a handoff letter is no answer
- **hubbus:** A linked worktree's mail goes through hub-event, outside any sandbox
- **hooks:** A pointer a sandboxed project wrote decides nothing that runs as the user
- **hubbus:** What the cold review of step 3 found in the session record
- **hooks:** What runs as the user goes by the folder the hook names and the registry, not by anything under it
- **hooks:** A real worktree runs no memory planted beside it
- **hooks:** A platform run's agent runs no git of its own in a project's folder, and what a remembered folder may lead to
- **hooks:** A worktree under workspace_root or its main checkout keeps the reminder and local setup
- **hooks:** Nothing run as the user sits in a folder a sandbox writes, and a project is asked by its name
- **hooks:** A probe that can't clean up says so, like anything else that breaks it
- **hubbus:** No propose run for a sandboxed project, and the copy where a runtime looks for it
- **hooks:** The private temp folder fails visibly, gets pruned, and no test leaves anything in it
- **hubbus:** A name the registry's index lacks is couldn't tell, not not sandboxed
- **llm:** Restricted is honoured or the run fails, and the check after an update exercises it
- **hooks:** The roster script's temp folder makes a missing secrets folder 700 too
- **llm:** Restricted goes back on mailbox-propose, and a runtime that can't is held to the throwaway copy
- **hooks:** An old claude can upgrade, and the flag is asked of the version being installed
- **hubbus:** A propose refusal isn't a failed start
- **hooks:** The prune keeps what it can't be sure of, and says so
- **runner:** The leak check looks only at what this run's own tests could write
- **hooks:** Only making the folder reads as could not make a folder
- **hooks:** The name scan of an exported tree leaves out what a running test made
- **hubbus:** A wake's five minutes end only the route it was for
- **hubbus:** Read what sessions are doing once a tick, and bound it
- **hubbus:** What a woken session reads counts as read, and the texts say so
- **agent-jobs:** A job's status file is replaced whole, never read half written
- **test-runner:** A tier's temp folder is one no other project can write
- **project_git:** A memory bundle holding more than notes is refused
- **llm:** Agents in a copy of a memory branch run only on what is notes
- **test-runner:** A project whose jobs are exempt from the sandbox tests in the private folder
- **memory:** A stray file in memory/ no longer stops a project's notes
- **test-runner:** Remove only the temp folder it made; run-tests by hand doesn't run without a private one
- **hubbus:** A route call is marked before it starts, and only dispatch touches its file
- **hubbus:** Mailbox-route goes by the project's name now, not the one a letter was sent to
- **hubbus:** A broken mailbox-route shows as a job.failed, and a pick gone since is said so
- **mailbox-route:** Keep the calls and answers in a secrets folder of their own
- **hubbus:** A mailbox-route record that can't be kept spends the call, not the project's tick
- **mailbox-route:** The call's kill takes only its own process group
- **mailbox-route:** A call killed at its time leaves a usage row
- **systemd:** An interval unit doesn't kill what its tick started to outlive it
- **mailbox-route:** The model sees each candidate by a label, since the mask took every id
- **run-tests:** No bytecode is written, so macOS's python3 leaves no cache in the tests' HOME

### Faster

- **hubbus:** A hook judges its letters before the write lock, and reads the registry once

## v0.10.0 (2026-11-03)

### New

- **sessions:** Read the owner's sessions from every agent, not from the runtime
- **gate:** The platform's commit messages never name a private project

### Fixed

- **reflect:** The weekly page declares UTF-8 and a viewport
- **reflect:** The weekly review is brunna's, not one tool's
- **sessions:** Count each entry once across sources and copies
- **sessions:** A worktree's last activity counts sessions that moved into it
- **sessions:** What a cold review found in the session sources
- **gate:** Read the private-names location through names.env
- **gate:** The private-name check holds on every path to the public history
- **state:** A restore is all or nothing, and says what it can't do yet

### Reverted

- Keep the README lean; restore is in docs/commands.md and docs/features.md

## v0.9.0 (2026-11-03)

### New

- **state:** Brunna state history and restore

### Fixed

- **state:** The description is a module-level string, as the catalog check reads

## v0.8.2 (2026-11-03)

### Fixed

- A fresh install starts healthy
- **onboard:** Apply the config quietly after registering

## v0.8.1 (2026-11-03)

### Fixed

- **mods:** Share the herdr mod as herdr lists it, in herdr/brunna
- **registry:** A project is known as soon as it is added

## v0.8.0 (2026-11-03)

### New

- **glance:** Brunna glance, what needs you and the platform's health in one look
- **mods:** A herdr mod that shows brunna glance beside your agents
- **mods:** The brunna panel drawn with herdr's own elements
- **mods:** The brunna panel shows a refresh you asked for
- **status-page:** A designed overview in the herdr panel's look

### Fixed

- **mods:** The brunna panel shows item texts as written, with headings herdr renders

## v0.7.0 (2026-11-02)

### New

- **names:** The switch: the platform writes brunna
- **names:** The platform's own services take the new label too
- **i18n:** Messages name brunna, and the settings file as the instance has it
- **templates:** Project rules, prompts and setup name brunna
- **install:** A new install is ~/brunna from imaknas/brunna; upgrade stops at v0.6.0

### Fixed

- **names:** Presence, the handler guard, the run folder and the leak check read both spellings
- **names:** --version and the test runner's errors name brunna

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

