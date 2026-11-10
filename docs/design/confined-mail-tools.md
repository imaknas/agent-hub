# Confined runs send mail through the platform's tools, not a shell

Status: built (11-10), on one branch merged at once. Settled after six rounds of blind review (feasibility and
security each). What the build changed from the text below: `rule_problems` lets an absolute path through when it is the
run's own job folder (a live job names its state folder so; `project-agent-calls.md` A2 says so too), and the
`runtime_claude.project_roots` binding is a small function calling `policy.run_roots` rather than a module-level name
(the adapter imports the platform's modules only when called). The owner decided the direction on 11-10, which
answers the question of 11-02 (m11020427-89ec, raised in `docs/design/mailbox-routing.md`, "11-02: the handlers are
confined", paragraph **Bash**).

## The gap

The confined judgment points (`settings: platform`: `mailbox-escalation`, `mailbox-conversation`, `agent-job-confined`,
`agent-job-event`) get Bash, and allow rules for the mail subcommands (`Bash(brunna send *)` and the like,
`policy.platform_cli(mail_only=True)`). Their prompts carry text anyone can write: a letter from any project, or a bus
event. The arguments of an allowed command are the model's, so a letter can ask for
`brunna send … --text "$(cat ~/.ssh/id_ed25519)"`, which would mail the file to the letter's sender.

Whether that runs depends only on the coding agent's own parsing of the command line. Real runs on 11-09 (Claude Code
2.1.319, `dontAsk`, only `Bash(echo *)` allowed; the record is in brunna's notes for that day):

| command | result |
|---|---|
| `echo "$(whoami)"`, `echo "$HOME"` | ran |
| `$(head …)`, `$(ls … \| head)`, `$(security find-generic-password …)` inside the argument | refused |
| an arbitrary variable (`"$PROBE_SECRET"`), `${VAR:-x}`, backticks | refused |

So it is not open today, but the guarantee is the tool's, and `docs/design/tool-agnostic.md` says the platform's
guarantees never rest on a tool's own features. In a project that isn't sandboxed there is no OS boundary behind it
either: the command runs as the user.

The same holds for **any** rule that runs a command or reaches the network, in a run whose prompt is someone else's
text: `Bash(git log *)` with `$(cat …)` in it reads the same file, and a bare `WebFetch` carries the project's files
anywhere. M6 deals with that part.

Found on the way, open today, closed here:

- A confined job whose folder is its state folder (`cwd: state`) runs `brunna send` from inside the hub's checkout,
  so `hubbus` takes it for the hub project (`project_of`), and it sends, and with `JOB` set may close, as brunna. M3:
  the server's project is the run's, never its folder's.
- A confined run's file tools can write the project's `.git`: a sandboxed project's outbox (sent by `dispatch` with no
  confined check), or `.git/config`, whose `core.fsmonitor` the platform's next git command in that repo runs as the
  user. M7.
- A confined run can close records (`rec:…`) and withdraw letters its project sent. M3.
- The sessions' MCP server writes a note with a plain `open`, following a link the project planted. M3.

## The decision

A confined run gets no shell for mail. It sends, reads and closes letters through the platform's MCP server
(`bin/mcp_server.py`, `brunna mcp`), which the platform attaches to that run. The tool's arguments are values, never a
command line: the server hands them to `hubbus` as an argument list built so that no value can become an option (M3),
and `hubbus` applies the same confinement checks it applies to the commands today (`confined_refusal`, `run_refusal`
with `MAILBOX_LETTERS`), plus the new ones in M3.

## Depends on

Two parts of `docs/design/project-agent-calls.md` A2, not built yet, are built first, as step 0 here (they are step 2
of that design's build order, whose step 1, the prompt on stdin, is built; A2 is updated to the folder layout below):

- **The run's folder.** `llm.py` reads the point's `project_env` first; when it is empty it makes nothing and lets the
  adapter's existing refusal decide (a dry run exits 2, a real run fails), so `llm-golden.json` keeps its sixteen
  entries. Otherwise it removes run folders older than a day that a crash left, then makes
  `<state>/agent-jobs/.settings/<project>.<point>.<run id>/` (`mkdir`, so two runs never share one; the run id is
  `secrets.token_hex(8)`, made by `llm.py`) and puts it on the task as `Task.run_dir`, a neutral field. The adapter's `platform_settings` takes
  `run_dir` and writes the settings file in it (the fake's signature changes with it). The folder is removed on every
  way out of `llm.py run` and of a dry run, by a `try/finally` around everything after it is made, the adapter's
  `_flags` raising included.
- **`rule_problems`**, the adapter port that checks a run's rules when it starts (never a registry validation error;
  `registry.py check` and the status page list what a run would refuse). M6 and M7 add to it. It runs for **every
  `settings: platform` point** (the mail handlers included; A2's list of three is updated to this). Since `llm.py`
  doesn't read a point's rules (`{platform_mail}` and `allow_from_env` are the adapter's), the port takes the task:
  `rule_problems(task, *, sandboxed, network, untrusted) -> [sentences]`, the adapter gathering the rules through
  `_rules` as `servers_named` does; a second function, `rules_problems(rules, *, sandboxed, network, untrusted)`, takes a
  job's `allow:` list for the registry's and the status page's listing. Both under a new `PORTS` key, `rule_check`,
  with its `PORT_LABELS` entry; not in `OPTIONAL`, since the check is what the run's boundary rests on. A runtime
  without it refuses every `settings: platform` run (A2), and doctor's rows for confined points need it
  (`tests/test_runtime_ports_features.needs()` adds it for every `settings: platform` point). `runtime_fake`
  implements it (and `run_server`, M2), so callers that load a runtime with no `ports=` (lease-runner, e3,
  `test_lease_contract_v2`) keep working. On a runtime without it, `registry.py check` and the status page say the
  rules can't be checked there, as other unsupported features do. During the branch, A2's sandboxed-project check
  sees `_rules`' Bash mail rules until step 4 removes them; a sandboxed project's handler test written before then
  fails, as M6's would.
- `platform_settings(project, spec, run_dir, keep, cwd)`: it takes the point's spec too (M6's denies depend on
  `prompt_source`), and raises without a `run_dir`. It is called only by `_flags`, which is reached only through
  `llm.py`; the tests that call either directly pass a `run_dir` (listed under Tests).
- **`project_roots`** moves from the adapter to `policy.py` as `policy.run_roots` (everything it reads is neutral; the
  new name keeps it apart from `hubconf.project_roots()`), so M7's deny can use it. The adapter binds it at module
  level as `project_roots = policy.run_roots`, so the tests that patch `runtime_claude.project_roots` keep applying.

## M1. The declaration

**The judgment points.** `contracts/llm-points.yaml` gains four neutral keys, added to
`contracts/llm-points.v1.schema.json` in the same commit (its point object has `additionalProperties: false`;
`tests/test_contracts.py` and the nightly `contracts-check` validate against it). Adding fields keeps the version;
Muninn, which vendors the schema, is told.

- `mail: tools | none`: whether the run gets the mail tools. `{platform_mail}` goes away (only the two mail handlers
  used it).
- `mail_from_env: <VAR>`: with `mail: tools`, the tools are given only when that variable is `1` (the jobs' opt-in,
  below). Without it, always.
- `letters_env: <VAR>`: the run acts on given letters. Once the project is known (Depends on), `llm.py` refuses the
  run before it starts when the variable is empty, lists no letter, or lists an id that doesn't `fullmatch`
  `hubbus.EVENT_ID` (a run whose every call would be refused, or one where `run_letters()` would read "not a letter
  run" and turn `run_refusal` off). On a dry run it prints the reason and exits 2, as the `tool_servers` branch does;
  `fail()` only on a real run.
- `prompt_source: untrusted`: the prompt is text anyone who can write the bus wrote (M6). Named apart from the existing
  `prompts:` key.

`llm.py lint` gets the cross-field rules: `mail:` is required on every `settings: platform` point and refused on any
other; `mail_from_env` and `letters_env` only with `mail: tools`; every `mail: tools` point declares
`tool_servers: allowed_only` (so the reserved-id check, M2, runs on every such run; for a sealed run it costs nothing,
`ambient_servers` returning None). `mailbox-conversation` gains it, and doctor's mailbox follow-up row needs
`tool_servers` with it. The schema descriptions of the new keys name no project or path (`test_contracts.py`).

| point | mail | letters | prompt_source | tools |
|---|---|---|---|---|
| `mailbox-escalation` | tools | `MAILBOX_LETTERS` | untrusted | `Read,Edit,Write,Glob,Grep` (Bash removed) |
| `mailbox-conversation` | tools | `MAILBOX_LETTERS` | untrusted | the same |
| `agent-job-event` | none (below) | | untrusted | reading plus the tools its rules use, within M6's list |
| `agent-job-confined` | tools, `mail_from_env: AGENT_JOB_MAIL` | | | reading plus the tools its rules use; Bash only when a rule names it (today Bash is always added for the mail) |
| `project-agent` (`project-agent-calls.md`) | none | | | as that design says |

**The registry.** A registry agent job may declare `mail: true`: a bool, valid only on a clock `kind: agent` job with
`allow:` (added to `registry.JOB`, to `validate_runs`, to the header's job keys, and documented beside `trigger` in the
project schemas, whose job items don't close their properties). `scheduler_jobs` puts `AGENT_JOB_MAIL=1` on the job's
command, as it puts `AGENT_JOB_ALLOW`. Without it, a confined job gets no mail tools. Today every confined job can send
to any project, close any letter to its own, and read its whole mailbox (only letter runs have `MAILBOX_LETTERS`). No
job in the live registry sends mail (checked 11-10: no job prompt names a mail command), so nothing loses it. `mail:`
is not a security field: it only adds what the confined checks allow. On a `connectors: true` job it is an owner change
like any other change to such a job (`connector_jobs`), which is intended.

**Not on event jobs.** `mail: true` on an event-triggered job is refused: any bus writer's text would then send
requests and questions as the project to any project, and the send is a channel for the project's readable files
(the same reason M6 refuses the web). Scoping an event job's sends (replies to the triggering events' sources, as
`run_refusal` does for letters) is left for when a project needs it.

**Which tools**, constants in `mcp_server.py` that `llm.py` reads:

- a mail handler (`letters_env`) gets `inbox`, `show`, `thread`, `send`, `done`, `ask_owner` and `note`;
- a job with `mail: true` gets **only the sending tools, for new letters**: `send` and `ask_owner`, and `note` when its
  rules already write notes. Its `send` has no `reply_to` and no `question` field, and its types are `request`,
  `question`, `info` and `progress` (no `done`, no `drop`). Not `inbox`, `show`, `thread` or `done`: with no letters of
  its own, they would read the project's whole mailbox, so any project's letter would reach a run that keeps its Bash
  and web rules (M6), and close any letter to the project. A reply could name any letter to or from the project
  (learned from its notes, or a sandboxed project's `inbox.json`) and so count as the project's answer to the owner's
  open handoff; a `--question` could close the owner's questions. A job that has to answer mail leaves it to the
  project's sessions and the mail handlers. Without the notes rule, `note` would give the job a way into the notes the
  librarian reads every night that its rules don't. (`hubbus` holds the same line, M3: the tool schema is not the
  boundary.)

Not offered to any run, and refused by `call()` whatever the client sends: `ack` (a no-op for a run without letters)
and `pass` (refused for every platform run), and `open-questions` (the reconciliation letter names its question; the
owner's open questions are for the project's sessions).

`platform_settings` stops writing the mail commands into the settings file's allow list: the file holds the deny list
and the sandbox only. `policy.platform_cli`'s `mail_only` loses its last callers (the sessions' rules use
`platform_cli()` without it) and goes, with its test in `test_mailbox_route.py`; `MAIL_SUBCOMMANDS` stays, since
`SESSION_SUBCOMMANDS` is built from it.

## M2. Attaching the server

Shaped like `deny_servers`: a neutral field on the task that the adapter translates.

- **`runtime_ports.ToolServer(id, command, args, env, tools)`** and **`Task.servers: tuple[ToolServer, ...]`**.
  `llm.py` builds one for a run whose point gives it mail tools: `id` is reserved (below); `command` is the platform's
  Python; `args` is `bin/mcp_server.py --run <run dir>/server.json`; `tools` is `RUN_TOOLS` for the point.
- **What goes where.** Real run, 11-10: Claude Code expands `${…}` in an `--mcp-config` env value (`x-${HOME}-y` reached
  the server as the home path). A letter id an unsandboxed sender chose, or an event-chain entry a bus writer put in
  its payload, would be expanded from the agent's own environment, the login token included, and the chain is stamped
  on every letter the run sends. So:
  - **`env`** holds only what the platform itself names and the server needs before it can read anything: `HOME`,
    `PATH`, and the instance's locations (`ROOT`, `HOME` for the instance directory, `CONFIG`, `STATE`, `DATA`,
    `SECRETS`, `EVALS`), each **only when it is set in `llm.py`'s own environment**, copied as it is there (`hubconf`
    reads them at import, and e3 runs on its own instance; sending resolved values instead would turn off `hubconf`'s
    own-instance and migration logic). None of them may contain `$`, nor may `command` or `args`: `llm.py` checks, and
    refuses a run whose would.
  - **`<run dir>/server.json`** holds the run's values: the project, the point, the tools, the letters (already
    checked, M1), the event chain and depth. `llm.py` writes it once, after `run_server` (below) and before the run
    starts, with `O_EXCL` and mode 0600.
- **The port** is `run_server(task) -> task`: `PORTS` and `PORT_LABELS` gain it (`tests/test_runtime_contracts.py`
  asserts the two have the same keys, and loops over every port for runtime `none`). It returns the task with
  `task.servers` final: the adapter takes `note` out of a job's tools when the job's rules don't write the notes
  folder (the neutral question "do these rules write the notes?" is the adapter's to answer, `MEMORY_RULE`). It adds
  no rule. A runtime without it refuses every run that would get mail tools, with the reason, as a runtime without
  `confine` does. `runtime_fake` implements it; `acp` and `command` have no `confine`, so nothing changes there.
- **Doctor's feature table**: the handlers' rows need `run_server` when their mail is always on (the propose variant
  of the mailbox row doesn't). Jobs with `mail: true` get a row of their own ("agent jobs that send mail"), appended
  after the existing rows so their indices stay; `tests/test_runtime_ports_features.needs()` learns the new keys.
- **Claude Code.** `_flags` writes `<run dir>/mcp.json` from `task.servers` and passes `--mcp-config` with it. After
  `_rules` and after `rule_problems` have run on the point's and the job's own rules, it adds `mcp__<id>__<tool>` for
  each tool. `rule_problems` and `servers_named` see only the point's and the job's own rules, never these.
- **The reserved id** is `<cli>-run` under both CLI names (`brunna-run`, `agenthub-run`). The `ambient_servers` port's
  contract gains a sentence: ids made of `[a-z0-9-]` come back unchanged (true of `mcp_id`). So `llm.py` compares
  strings without the adapter. Nothing stops a user, a plugin or `.mcp.json` from naming a server that. What keeps it
  out is a check of its own, not the denial list: every point with mail tools declares `tool_servers: allowed_only`
  (lint, M1), and `deny_unnamed_servers`, which has the run's `loaded` list, refuses the run when one of them has the
  reserved id. Most such runs are sealed as well (`--strict-mcp-config`: `mailbox-conversation`'s `extra_args`,
  `run-agent-job.sh`'s `MCP_ARGS` unless `LLM_AUTH=subscription`), and then the list is empty. Tested with such a
  server. A project's own rules may not name it (M6).

Real runs (Claude Code 2.1.319; scripts in this session's scratchpad, results in brunna's notes for 11-10):

- With `--restricted --settings <file> --tools Read --permission-mode dontAsk --strict-mcp-config --mcp-config
  <json>`, a server given only by `--mcp-config` is offered to the run. With the rule `mcp__<id>__post` the call
  reached the server. Without it the call was refused (`permission_denials` names it) and the server was never called.
- The server sees the run's session id (`CLAUDE_CODE_SESSION_ID`, inherited), so a fork's letters keep their
  `from_session` as long as the server passes it on to `hubbus` (M3).
- `--resume <id> --fork-session` with the same flags takes the server too; the server sees the fork's own session id.
- `${…}` in a config env value is expanded (above).
- With a settings file that turns the sandbox on, the server still runs outside it: it wrote a file outside the run's
  folder (M4).

## M3. The server serves a confined run

Today `mcp_server.call` refuses every platform-started run (`HUB_LLM_POINT`). The change:

- **When it serves a platform run.** Started with `--run <file>`, it reads the file **once, at start**, before it
  answers `initialize` (so before the model can act): opened with `O_NOFOLLOW`, then checked on the open handle
  (`fstat`): a regular file, owned by the user, not writable by others, whose path is inside
  `<the server's own hubconf.STATE>/agent-jobs/.settings/` (the server's `hubconf`, from the env above, never a
  location named in the file). It serves only when the file's point declares `mail: tools` (the server reads
  `llm-points.yaml` from the platform's checkout), its project is registered and has a workspace, and, for a point
  with `letters_env`, it lists letters. When the file fails any of these, the server still answers `initialize` and
  `tools/list` (the point's tools when the file names a point it can read, else the mail handlers' set) but refuses
  every call with the reason (logged to stderr too), so the run's report says why its mail failed rather than showing
  a server that never started. A server started without `--run` whose environment has a non-empty
  `HUB_LLM_POINT` gets today's refusal (a user-registered `brunna mcp` that the run loads inherits the agent's
  environment and has no `--run`). Both checks come first in `call()`, before the branch for folders outside every
  project (`connect_project` runs `connect.py` as the user). Tests cover `agent-job` (auto mode) and lease sessions:
  refused. Presence and the open-letters notice are skipped because the server is in `--run` mode, not because of an
  inherited variable.
- **Its project** is the file's only: never `AGENTHUB_PROJECT` or `BRUNNA_PROJECT` (a user's config sets those,
  `docs/mcp.md`), never its working directory. Before every call it checks that `hubbus.project_of(<workspace>)` is
  that project (a workspace inside another project's folder could resolve to the other one), and it passes
  `--from=<project>`, and `--project=<project>` to `inbox`. It runs `hubbus` in the workspace; with no workspace the
  call is refused (no fallback to the server's own folder). A test runs a `cwd: state` job's server and sees it act as
  its own project, not the hub.
- **`hubbus`'s environment** is built, not inherited whole: `names.env_strip(os.environ)` (it drops the
  `AGENT_HUB_`/`AGENTHUB_`/`BRUNNA_` variables: no `JOB`, no chain), minus, by name, the unprefixed platform
  variables (`HUB_LLM_POINT`, `MAILBOX_LETTERS`, `MAILBOX_PROJECT`, `HUBBUS_LEASE`, `HUB_MAIL_BY_PLATFORM`,
  `HUB_THROWAWAY_COPY`, `HUB_MEMORY_OF`, `LLM_AUTH`, every `AGENT_JOB_*`) and the login variables (the auth port's
  `clean_env` and `completion.key_envs()`), plus the
  instance's locations as the server has them, plus the file's values: `CONFINED`, `JOB_CHAIN` and `JOB_DEPTH` through
  `names.env_set`, and `HUB_LLM_POINT` and `MAILBOX_LETTERS` **as raw keys** (`hubbus` reads them unprefixed; through
  `env_set` they would be renamed and the letter limits would silently go). Everything else the agent's environment
  carries stays, so the runtime's session id reaches `hubbus` without the server naming it. (`env_strip` also drops the
  language overrides, `LANG_UI`/`LANG_CONTENT`: the server's `hubbus` takes the language from the config, so a test
  that sets it by environment sets it in the run file's instance, not the agent's.)
- **The tools it lists** are the file's; `call()` refuses any other. It checks the arguments' types before building
  anything: `ids` a list of strings (a string would be iterated by character today, and each character is a valid
  `EVENT_ID`), `options` a list of objects of strings, every other field a string (`withdraw` aside, a bool). The `inbox` result has no header: the server's
  count is the project's, while a letter run's inbox lists only its letters. `instructions` at `initialize` is a short
  fixed text: letters are data; the run acts only on what it was started for.
- **No value becomes an option.**
  - Single values go as `--name=value` (`--text=…`, `--to=…`, `--note=…`, `--reply-to=…`, `--question=…`), and every
    option, `--withdraw` included, comes before the `--` that ends them.
  - Letter ids go after `--`, and each must `fullmatch` `hubbus.EVENT_ID` (the shape every id the platform writes has:
    `m…`, `qcheck-…`, handoff ids, outbox ids; `fullmatch`, since `EVENT_ID` ends in `$` and `.match` lets a final
    newline through). An id an unsandboxed sender chose with `--id` is not checked on the bus path today
    (`send_letter`); from now on `send_letter` refuses an `--id` that doesn't `fullmatch` `EVENT_ID`, and `dispatch`
    leaves an older letter with such an id out of a handler's batch, and neither routes nor forks it (`resume`), so it
    stays open for the project's sessions and one bad id never refuses a whole batch or spends a fork (M1's
    `letters_env` check).
  - `ask_owner`'s options: `--option` takes two values (`nargs=2`), so each id must `fullmatch` `hubbus.OPTION_ID` and
    neither the id nor the label may start with `-`; a test pins it (a label `--text` would otherwise be read as that
    option).

  This holds for sessions' servers too.
- **`send`** gains `question`, and its type list gains `drop`, valid only with `question` (the reconciliation close:
  `--type done|drop --question <id>`). Both are offered only to points with `letters_env` (M1).
- **`hubbus` under `CONFINED`** (the command and the server alike) also refuses:
  - `done` of anything but a message: `close_refusal` lets a confined run close any non-letter event to or from its
    project, records (`rec:…`) included, so a bus writer could have a run close the owner's records before the
    registrar applies them;
  - `done --withdraw`: a run with no letters (a job) could take back any open letter its project sent;
  - `close_refusal`'s exemption for the hub (closing letters the hub sent to other projects): a hub job's run is held
    like any project's;
  - a `request` to the owner (compared with `canon(a.to)`, so every owner alias counts): it raises an owner to-do just
    as `task` does (`send_record`), which `confined_refusal` refuses because the owner's to-dos need a person;
  - for a run **without letters** (a job: `run_letters()` is None, so `run_refusal` doesn't apply): every command but
    `send` (`inbox`, `show`, `thread`, `done`, `ack`, `open-questions`: today `inbox` lists the whole mailbox and
    `done` closes any letter to the project, and a clock job keeps Bash, so the CLI path must hold the line the tools
    hold), and on `send`, `--reply-to`, `--question`, and the types `done` and `drop`. Such a run starts new letters
    only: `request`, `question`, `info`, `progress` to a project; `question`, `info`, `progress` to the owner.

  A run **with letters** (a mail handler) is held by `run_refusal` as today: replies to its own letters' senders
  (`done`, or `info` when the letter asks; a fork's ordinary types), questions to the owner, and `done|drop
  --question` only for a question one of its letters names. The records refusal sits before the sandboxed-outbox
  branch of `done` (`take()` closes records addressed to the project too).
- **`note`** appends through a new `nofollow.append(base, rel, data)`: base is the project's workspace (the main
  checkout for the hub), rel is `.agent-memory/memory/<day>.md`; every folder from the base down is opened without
  following links, and only `memory` may be created; the day file is opened with
  `O_WRONLY|O_APPEND|O_CREAT|O_NOFOLLOW|O_NONBLOCK` and refused unless it is a regular file with one link
  (`st_nlink == 1`, as `lease_files.py` checks). This is how `outbox_put` already writes (base = the workspace).
  `append` has its own folder walk: `nofollow._folder(create=True)` would create every missing folder, and `append`
  creates only the last one.
  `policy.notes_dir` only answers "does this project have notes"; when it says no, `note` is refused. Today
  `write_note` uses a plain `open`, and the server runs outside every sandbox, so a project that planted
  `memory/<today>.md -> ~/.zshenv`, or swapped `memory` for a link at the right moment, would get a line written into
  the user's files; that holds for sessions' servers today, and the fix covers them. A hard link to a file outside the
  project can't be made from inside the macOS sandbox (Seatbelt refuses `ln` of a file outside the writable set, tried
  11-10); on Linux (Landlock) that is untested, and the `st_nlink` check covers it either way.
- The server's header loses "Standard library": it now reads `llm-points.yaml` itself, with PyYAML, which `bin/py`
  has.

## M4. Sandboxed projects

The coding agent starts the server itself, outside its command sandbox (Claude Code's sandbox covers Bash only; real
run above), so for a sandboxed project the mail no longer runs inside the project's OS sandbox, as `brunna send` in
Bash did. What the server can be made to do is bounded by its code:

- its inputs from the model are values, never paths or commands (M3);
- the project's files it reads are its view (through `nofollow`, size-capped; a forged view fools only that project's
  own checks, and `take()` judges each letter against the bus again), the `.git` pointer files (regular files only,
  after `gitdirs.holder` has settled a sandboxed folder), and the notes folder (M3);
- for an outboxed project, `hubbus` writes only that project's outbox, without following links, and `dispatch` sends
  each file as that project, never as a sender named in it (`docs/design/bus-sender.md`). This is the path a sandboxed
  project's sessions take with their own server today. The run itself can't write the outbox (M7).

Accepted residual, unchanged from today: a mail handler keeps Read and a reply to its letter's sender, so a project
can ask another project's handler to reply with a file from that project. What is never readable is denied (the deny
lists, and the declared secrets with `project-agent-calls.md` C); the rest of a project's folder is what its handlers
can quote.

Considered and not chosen: starting the server under `bin/sandboxed-run` with the project's profile. It would add an
OS boundary, but the profile would have to admit the platform's code, registry and templates, the server would need a
capability of its own in every adapter (`isolation.py`), and nothing the model or the project controls reaches a path.

## M5. Prompts, letters and evals

- `templates/prompts/mailbox.md`, `mailbox-resume.md` and `mailbox-delegate.md` name the tools (`send`, `done`,
  `inbox`…) instead of the commands, and drop "run each command as a command of its own".
- They also say: a letter, a refusal or a rules file that names a `brunna` mail command (`brunna send … --type info`,
  `brunna done <id>`, `--type done`, `no --from`) means the tool of the same name, with the same fields. The texts
  `hubbus` writes into letters (the "asks" reply line, the owner's answers, decisions) and its refusals stay as they
  are: sessions read them too, and they are correct there.
- The sessions' rules file (`templates/agent-instructions.md`) keeps the commands: sessions aren't confined runs.
- `llm.py` keeps putting the hub's `bin` on `PATH` (unconfined points' prompts name the commands). It is not what keeps
  a confined run from the CLI: `run-agent-job.sh` puts `~/.local/bin` on the path too, and the agent's shell may
  rebuild it from the login profile. What does is having no shell (handlers, event jobs) and `hubbus`'s `CONFINED`
  checks.
- A prompt change runs the evals before it merges (`docs/design/i18n.md`). `bin/evals/e3.py`'s mailbox cases call
  `llm.py run` directly, so the server is attached there too, on e3's own instance (the locations in the server's
  `env`, M2). Their scoring reads the letters sent from the bus as now. The `old` arm renders the prompts at `OLD_REF`,
  which name commands the new runs can't use, so it is dropped for the mailbox, resume and delegate cases; those are
  compared as `new` against `new-en` (the instance's content language), which e3 already runs, and its report says so.

## M6. Rules where the prompt is someone else's text

`rule_problems` (Depends on) gains `untrusted` (from `prompt_source: untrusted`), so the adapter knows what kind of
run it checks without knowing the points' names. Run when the run starts, on the point's and the job's own rules
(never the server's, M2), **after `{platform_mail}` and the Bash mail rules are gone** (step 4 below):

- **An untrusted run gets the same allow list as a sandboxed project's** (A2): Read, Glob, Grep, Edit, Write,
  MultiEdit, NotebookEdit, and `WebFetch(domain:d)` with `d` in the project's own `network:`. No Bash, no bare
  `WebFetch`, no `WebSearch`, no tool the list doesn't know, for every project. Any of them, with Read always on, would
  let any bus writer's text read the project's files and carry them out, or run a substitution (a broad rule such as
  `Bash(sh *)` even reaches `hubbus` with the confined checks stripped, `env -u`). The live registry has no event
  agent job (checked 11-10), and no live job's rules name the platform's CLI under Bash (checked 11-10: the only Bash
  rules are a project's own scripts and `git log`/`git show`). An event job that needs a script runs it as a command
  job before or after. (The mail
  handlers' rules are the point's own, so this changes nothing for them.)
- **An untrusted run doesn't write what the owner's sessions or the platform read as instructions.** Its settings deny
  Edit and Write on:
  - every runtime's rules-file names (`RULES_FILE_NAMES`, `CLAUDE.md`, `AGENTS.md`…) at any depth, and `.claude/rules/`
    (Claude Code loads every `.md` under it like `CLAUDE.md`, code.claude.com/docs/en/memory.md). `.claude/rules` also
    joins the project-level `CONTROL` list (`runtime_claude.py`), which today has it only at user level, so sandboxed
    projects' sessions are covered too;
  - `.agent-memory/**` except `memory/**`: the registrar sends `STATE.md`'s owner to-do lines every night. A deny can't
    be narrowed by an allow, so it is written as a list: `memory_paths.TOP` (`AGENT.md`, `RULES`, `STATE.md`) and
    `.agent-memory/.git` by name, whether or not they exist (a run must not create a missing `STATE.md`), plus
    `beside` for anything else present.

  What a name-based deny can't cover, said plainly: a project's existing `CLAUDE.md` can `@import` any relative file;
  the notes a handler writes feed the librarian, which writes `STATE.md`'s to-do section (unchanged from today). Other
  code an untrusted run may write is what the project's own `allow:` rules say: a project that gives an event job a
  write rule over its whole folder lets any bus writer change files its tests and services run as the user, and that
  is said plainly in the registry header beside `trigger` (in neutral words: the header is not in the ratchet's
  baseline for rule syntax).
- **Every confined point refuses** a rule that names the reserved server, and a Bash rule naming the platform's CLI
  (`brunna`, `agenthub`, `bin/hub`, the `hubbus` path), whatever the subcommand (`complete` and `cap-run` included: a
  confined run reaches the platform only through its tools; a script the job runs may call the CLI itself). This is
  reinforcement, not the boundary: a broader rule or an absolute path gets past a name check, which is why untrusted
  runs refuse Bash whole.
- **Clock jobs (`agent-job-confined`) keep their Bash and web rules.** Their prompt is the project's own file, and a
  `mail: true` job reads no mail (M1); the residual (the job reads untrusted text, from the web or from files, and is
  talked into a substitution or a fetch) is said plainly here, and a sandboxed project has no Bash rule at all (A2).
  Such a job's Bash can also read the run's `server.json`: nothing in it is secret (it is what the run already has),
  and the server read it before the model could act.
- The checks compare paths and names case-insensitively (the hub's volume is: `.GIT` is `.git`).

## M7. No confined run writes a `.git`

Open today: a confined run's file tools work outside the OS sandbox (`--restricted` confines them to the working
folders, nothing more), and nothing denies a project's git directories. A rule such as `Edit(./**)` passes `RULE`.

- **The settings.** The policy gains a neutral entry, every `.git` (directory or pointer file) at any depth under
  each of the run's folders (`run_roots`, the run's own working folder, and every folder `_linked_rules` adds), and
  the project's git common directory (found by reading the pointer files with `gitdirs`, never by running git in the
  project's repo). It is a pattern, not a list of what exists: for each folder the adapter writes Edit and Write denies
  on `/<folder>/**/.git` and `/<folder>/**/.git/**`, so a run can't create a new nested `.git` either; `platform_settings` writes
  Edit and Write denies for them (the adapter's translation, as for `.hub-run`). A deny wins over every allow, so a
  run's notes rule (`./.agent-memory/memory/**`) keeps working and `.agent-memory/.git` (a worktree's pointer file) is
  denied.
- **The rules.** `rule_problems` refuses, for every confined point, a rule whose fixed part enters a `.git` (compared
  case-insensitively), as A2 refuses `.hub-run`. The text alone isn't enough: a link the project planted
  (`out -> .git/brunna/outbox`) turns `Edit(./out/**)` into a write there. So `_linked_rules`, which already resolves
  every step of a `./` rule, also raises when a resolved step is or enters a `.git` or `.hub-run` (case-insensitively),
  and `_notes_rules` checks its folder the same way.
- **Case.** Real run, 11-10 (Claude Code 2.1.319, macOS, case-insensitive volume): with `Edit(./**)`/`Write(./**)`
  allowed and Edit/Write on `/<ws>/.git` and `/<ws>/.git/**` denied, writes to `.GIT/config`, `.Git/probe-b.txt` and
  `.git/probe-c.txt` were all refused and `.git/config` was unchanged; `ok.txt` was written. The runtime's matching
  holds for case; the platform's own check doesn't rely on it.
- **Links and folding** (code review of the build, probed 11-10 on the same version): with the same denies, a write
  through `out -> .git/brunna` and an edit of `.agent-memory/memory/today.md -> ../../.git/config` were refused (the
  runtime matches where a path resolves), so a link anywhere under an allowed tree, not only in a rule's fixed part,
  meets the `.git` deny. The volume also folds the long s (U+017F) to s: with no `STATE.md`, a write to `ſTATE.md` made a
  file the volume serves as `STATE.md`. M6's name denies are therefore written in every such spelling (`_folds`); with
  them `ſTATE.md`, `AGENTſ.md` and `.claude/ruleſ/x.md` were refused. `.git`, `.hub-run` and `CLAUDE.md` have no
  letter any character folds to. The project-level control denies (`.claude/settings.json` and the like) predate this
  and are left as they are here.
- **Tests**: from an untrusted run, a write to a sandboxed project's outbox and to `.git/config` are both refused;
  the notes rule still writes; creating a new `sub/.git` is refused; `Edit(./out/**)` with `out` linked into `.git`
  refuses the run.

This lands in step 0, with the run's folder: it doesn't wait for the mail.

## What goes away, and what else changes

- `{platform_mail}` and its expansion in `runtime_claude._rules`; the raw `hubbus` rule; the mail commands in
  `platform_settings`' allow list; Bash in the mail handlers' tools and in the confined default; `mail_only`
  (`MAIL_SUBCOMMANDS` stays, M1).
- Docs that become false: `docs/mcp.md` ("agents the platform starts cannot use it"); `docs/design/event-jobs.md`'s
  example `allow: ["Read", "Bash(git log:*)"]` on an event job; the `registry.py` header on `allow:` and its trigger
  examples; the comments in `llm-points.yaml` ("Bash only because the mail is a command");
  `docs/design/mailbox-routing.md` "11-02", which gets a pointer here; `project-agent-calls.md` A2 (updated with this
  draft: the mail, `mail: none`, the run's folder, `.git`).
- Tool neutrality: the new code puts MCP syntax (`mcp__`, `--mcp-config`) and `Bash(` only in `runtime_claude.py`.
  Some MCP syntax is outside the adapter already (`registry.py`'s connectors prefix, `lease-runner.py`,
  `run-agent-job.sh`'s `--strict-mcp-config`, e3, `llm-points.yaml`). `tests/test_runtime_boundary.py` gains `mcp__`
  and `--mcp-config` patterns in step 3, with a hand-edited baseline of today's counts in the same commit (its
  `update()` only lowers counts and never adds a file), so they only go down.

## Order of work

All of it lands in **one merge**: `main` is the running platform, and a handler without Bash whose prompt still names
the commands would fail every letter between two merges. Within the branch, as commits (each passes the commit gate,
`lint` and `static`; the whole suite runs at `merge-branch`):

0. From `project-agent-calls.md` A2: the run's folder (`Task.run_dir`), `rule_problems` with its checks so far; and M7.
1. The schema and the declarations of M1's keys (`mail`, `mail_from_env`, `letters_env`, `prompt_source`), the lint
   rule, the registry's `mail:` and `AGENT_JOB_MAIL`, `llm.py`'s letters check. Nothing else reads them yet.
2. M3: the server (`--run`, the file's checks, `hubbus`'s environment, values that can't become options,
   `nofollow.append`, `send` with `question` and `drop`), and `hubbus`'s new `CONFINED` refusals; with tests of its own.
3. M2: `ToolServer`, `Task.servers`, `server.json`, the `run_server` port, the Claude Code adapter, `runtime_fake`,
   `PORTS`/`PORT_LABELS`, doctor's rows, the reserved-id check, the ratchet's new patterns.
4. M1's removals (Bash, `{platform_mail}`, the settings file's allow list, `mail_only`) together with M5's prompts.
5. M6: `untrusted` in `rule_problems` (after step 4, so the handlers' old Bash mail rules are gone when it checks).
6. The e3 mailbox cases, then the docs above, before `merge-branch`. Tell Muninn (the schema), and the projects that
   have agent jobs (`mail: true`, and what event jobs may name).

## Tests

Existing tests change, not only grow:

- `test_handler_confinement.py`: the Bash mail rules, the settings file's mail allow list, `{platform_mail}`.
- `test_scheduler_events.py`: `Bash` in the confined default `--tools`, `Bash(agenthub send *)` in the settings.
- `test_mcp_deny.py`: reads the first `--allowedTools` value and expects `--strict-mcp-config` last.
- `test_mailbox_route.py`: `platform_cli(mail_only=…)` goes.
- `test_runtime_ports_features.py`: `needs()` and the new row at the end.
- `test_mailbox_triage.py`: its `platform_settings` mocks (whose signature gains `run_dir`), the settings and
  `platform_cli` fixtures, and the flag tests; the `platform_settings` mocks of `test_scheduler_events.py` and
  `test_confined_hardening.py` too. The direct calls of `platform_settings` (`test_handler_confinement.py`,
  `test_scheduler_events.py`, `test_mailbox_triage.py`) and of `_flags` on confined tasks (`test_handler_confinement`'s
  `flags()`, `test_scheduler_events.py`) pass a `run_dir` and the spec.
- `test_runtime_contracts.py`: `PORTS`/`PORT_LABELS` gain `run_server` and `rule_check`.
- `llm-golden.json`: unchanged (Depends on); the new command shape is asserted in `test_handler_confinement.py`.
- The i18n catalogs and goldens for `mcp_server`, `runtime_ports`, `registry`, `hubbus`, `llm` and doctor change with
  the new and changed strings.

New:

- **The server.**
  - Refused: no `--run` with a non-empty `HUB_LLM_POINT`; a run file that is a link, outside the server's own
    `.settings`, writable by others, whose point doesn't declare `mail: tools`, without a workspace, or a letters point
    without letters; `agent-job` and lease runs.
  - Served with the file's project, whatever `AGENTHUB_PROJECT` and its working directory say; a `cwd: state` job acts
    as its project, not the hub; a workspace that resolves to another project is refused.
  - `hubbus` gets `HUB_LLM_POINT` and `MAILBOX_LETTERS` unprefixed, no `JOB` or login variable, and the session id.
  - A tool not in the file's list is refused.
  - `text: "--withdraw"` reaches `hubbus` as text; an id `--withdraw`, or `m1\n`, is refused; an option id or label
    starting with `-` is refused.
  - `MAILBOX_LETTERS` limits hold through the server; `done` of a record, `done --withdraw`, a hub job closing the
    hub's letter to another project, and a `request` to the owner (under any alias) are refused.
  - A job's `send` with `--reply-to` (a `done` or `info` to an owner handoff), with `--question`, or of type `done` or
    `drop` is refused; a handler's `done` reply to its own asks letter still goes.
  - Through the CLI under `CONFINED`, a run without letters gets `inbox`, `show`, `thread`, `done`, `ack` and
    `open-questions` refused.
  - `send_letter` refuses an `--id` that isn't an `EVENT_ID`; `dispatch` leaves such an older letter out of a batch.
  - `ids: "abc"` is refused, not read as three ids; a bad run file answers every call with the reason.
  - `note` refuses a linked day file, a linked `memory` folder, a linked `.agent-memory`, and a hard-linked file;
    creates the day file; writes the main checkout's notes for the hub.
- **The runtime.**
  - A handler's command has `--mcp-config` with the reserved id and only its tools' rules, no Bash rule, no Bash tool.
  - A job without `AGENT_JOB_MAIL=1` has no server; with it, only `send`, `ask_owner` and (with the notes rule)
    `note`; without a Bash rule, no Bash.
  - An untrusted run in an unsandboxed project can't write a `CLAUDE.md` or `AGENTS.md` at any depth, a file under
    `.claude/rules/`, or `.agent-memory/STATE.md` (also when `STATE.md` doesn't exist yet); it still writes
    `.agent-memory/memory/`.
  - The `.git` deny covers the run's working folder and every added one, not only the project's roots.
  - `server.json` holds the run's values; the config's `env` has only `HOME`, `PATH` and the instance's locations; a
    `$` in any of them, or in `command` or `args`, refuses the run; a letter id that isn't an `EVENT_ID`, or no letters
    for a letters point, refuses it before it starts.
  - Two runs of one project get two run folders; a run that raises in `_flags`, and a dry run, leave none; a confined
    point with no project makes none and keeps its golden.
  - A runtime without `run_server` refuses the run; an environment server with the reserved id refuses it.
  - The settings file has no allow list, and denies every `.git` and the git common directory.
- **`rule_problems`**: for an untrusted run, Bash, bare `WebFetch`, `WebSearch` and an unknown tool are refused; the
  CLI under Bash, the reserved server, and a rule entering `.git` are refused for every confined run; with the
  sentences.
- **The registry**: `mail: true` on a job without `allow:`, on a command job, or on an event job is refused;
  `scheduler_jobs` puts `AGENT_JOB_MAIL=1` on a job that declares it.
