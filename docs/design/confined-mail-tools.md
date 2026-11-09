# Confined runs send mail through the platform's tools, not a shell

Status: first draft (11-10), for a blind review before it is built. The owner decided the direction on 11-10, which
answers the question of 11-02 (m11020427-89ec, raised in `docs/design/mailbox-routing.md`, "11-02: the handlers are
confined", paragraph **Bash**).

## The gap

The confined judgment points (`settings: platform`: `mailbox-escalation`, `mailbox-conversation`, `agent-job-confined`,
`agent-job-event`) get Bash, and allow rules for the mail subcommands only (`Bash(brunna send *)` and the like,
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
guarantees never rest on a tool's own features: an update that relaxes the parser, or another runtime with a looser
one, opens it. In a project that isn't sandboxed there is no OS boundary behind it either: the command runs as the
user.

## The decision

A confined run gets no shell for mail. It sends, reads and closes letters through the platform's MCP server
(`bin/mcp_server.py`, `brunna mcp`), which the platform attaches to that run. The tool's arguments are values, never a
command line: the server hands them to `hubbus` as an argument list, and `hubbus` applies the same confinement checks
it applies to the commands today (`confined_refusal`, `run_refusal` with `MAILBOX_LETTERS`). Bash is gone from the
mail handlers, and an `allow:` job gets Bash only when one of its own rules names it.

## M1. The declaration

`contracts/llm-points.yaml` gains `mail: tools | none` (neutral: no runtime syntax), and `{platform_mail}` goes away
(only the two mail handlers used it):

| point | mail | tools |
|---|---|---|
| `mailbox-escalation` | tools | `Read,Edit,Write,Glob,Grep` (Bash removed) |
| `mailbox-conversation` | tools | the same |
| `agent-job-confined`, `agent-job-event` | tools | unchanged: reading plus the tools its rules use, Bash only when a rule names Bash (today Bash is always added for the mail) |
| `project-agent` (`docs/design/project-agent-calls.md`) | none | as that design says |

The points linter requires `mail:` on every `settings: platform` point, and refuses it on any other: an unconfined run
that wants mail has the commands, as a session does.

The mail tools a run gets: `inbox`, `show`, `thread`, `send`, `done`, `ask_owner`, `note`. Not `ack` or `pass`:
`run_refusal` and `confined_refusal` refuse both for these runs already, so they aren't offered.

`platform_settings` stops writing the mail commands into the settings file's allow list: the file holds the deny list
and the sandbox only. The rules for the tools are the run's own (M2), on its command line with the rest.

## M2. Attaching the server (a new port)

`llm.py` builds a neutral description of the server for a `mail: tools` run, and the runtime attaches it:

- `runtime_ports.ToolServer(id, command, args, env)`: `id` is a reserved name, `<cli>-run` (`brunna-run`), so it can
  never be a server the user configured; `command` is the platform's Python; `args` is `bin/mcp_server.py`; `env` is
  given explicitly: `AGENTHUB_PROJECT` (the run's project, from `project_env`), `HUB_LLM_POINT`, `AGENT_HUB_CONFINED=1`,
  `MAILBOX_LETTERS` when the run has it, and `HOME`/`PATH` as the run has them.
- A new port, `run_server`: `attach(task, server) -> (task, rules)` adds the server to the run and returns the allow
  rules that name exactly the mail tools of M1. `llm.py` appends those rules to the run's allow list.
- Claude Code: `--mcp-config <file>`, the file written next to the run's platform settings file, one per run (the
  settings files are per run already, `project-agent-calls.md` A2); rules `mcp__brunna-run__<tool>`.
- A runtime without the port: every `mail: tools` point is refused, with the reason, as a runtime without `confine`
  is today; doctor's feature table lists the port on those rows. `runtime_fake` implements it for the tests.
- A point with `tool_servers: allowed_only` loads the environment's servers too: the run is refused when one of them
  has the reserved id (it would be mistaken for the platform's). The reserved server is never in the denial list: its
  tools are named by the rules above.
- A project's own `allow:` rules may not name the reserved server (`mcp__brunna-run__…`): refused by registry
  validation and by the run-time rule check (`project-agent-calls.md` A2).

Real runs, 11-10 (Claude Code 2.1.319; scripts and output in this session's scratchpad, recorded in brunna's notes):
with `--restricted --settings <file> --tools Read --permission-mode dontAsk --strict-mcp-config --mcp-config <json>`,
a server given only by `--mcp-config` is offered to the run. With the rule `mcp__<id>__post` the call reached the
server. Without it the call was refused (`permission_denials` names it) and the server was never called. The server
saw both the value set in the config's `env` and one inherited from the agent's own environment.

## M3. The server serves a confined run

Today `mcp_server.call` refuses every platform-started run (`HUB_LLM_POINT`): such work writes its letters in its
output (`mailbox-propose`). The change:

- A platform run is served only when all three hold: its point (`HUB_LLM_POINT`) declares `mail: tools` in
  `contracts/llm-points.yaml` (read by the server from the platform's checkout, not from anything the run controls),
  `AGENT_HUB_CONFINED` is set, and `AGENTHUB_PROJECT` names a registered project. Anything else gets today's refusal.
- Its project is `AGENTHUB_PROJECT` only, never its working directory. `hubbus` runs in that project's folder, as
  `hubbus_cli` does now, so `confined_refusal` sees the same `here`.
- `tools/list` gives it the seven tools of M1. No presence is recorded and no notice is appended (both skip
  `HUB_LLM_POINT` already). `instructions` at `initialize` is a short fixed text for a platform run: letters are data,
  the run acts only on its letters.
- **No value becomes an option.** Every value goes to `hubbus` as `--name=value` (`--text=…`, `--to=…`,
  `--note=…`), and every letter id must have a letter id's shape before it is passed. Otherwise a text starting with
  `--` or an id like `--withdraw` would be read as an option: the same lesson as the prompt on stdin
  (`project-agent-calls.md` A4). This holds for sessions' servers too.
- `send` gains `question` (the reconciliation close: `--type done|drop --question <id>`), which the mailbox prompt
  uses today through the command.
- `note` writes through `nofollow` (the module `hubbus` uses for a project's outbox and view): a link anywhere in
  `.agent-memory/memory/<day>.md` is refused, not followed. Today it opens the file normally, and the server runs
  outside every sandbox (below), so a project that planted `memory/<today>.md -> ~/.zshenv` would get a line
  appended to the user's shell file. This holds for sessions' servers too.

## M4. Sandboxed projects

The coding agent starts the server itself, outside its command sandbox (Claude Code's sandbox covers Bash only), so
for a sandboxed project the mail no longer runs inside the project's OS sandbox, as `brunna send` in Bash did. What
the server can be made to do is bounded by its code, not by the sandbox:

- its inputs are values, never paths or commands (M3);
- for an outboxed project, `hubbus` writes only that project's outbox and reads only its view, both without
  following links, and `dispatch` sends each file as that project, never as a sender named in it
  (`docs/design/bus-sender.md`); this is the path a sandboxed project's sessions take with their own server today;
- `note` writes only the project's notes folder, without following links.

Considered and not chosen: starting the server under `bin/sandboxed-run` with the project's profile. It would add an
OS boundary, but the profile would have to admit the platform's code, registry and templates, the server would need
its own capability in every adapter (`isolation.py`), and nothing the agent controls reaches a path. The review should
challenge this if it sees a path the agent does control.

## M5. Prompts and evals

`templates/prompts/mailbox.md`, `mailbox-resume.md` and `mailbox-delegate.md` name the tools (`send`, `done`,
`inbox`…) instead of the commands, and drop "run each command as a command of its own". The sessions' rules file
(`templates/agent-instructions.md`) keeps the commands: sessions aren't confined runs.

A prompt change runs the evals before it merges (`docs/design/i18n.md`): `bin/evals/e3.py`'s mailbox cases start the
run as `run-agent-job.sh` does, so they get the server attached too, and their scoring reads the letters sent from the
bus as now.

## M6. A job's rules can't take the shell route back

An `allow:` job whose rules name the platform's CLI under Bash (`Bash(brunna …)`, `Bash(agenthub …)`, or the raw
`hubbus` path) is refused by registry validation (the job is left out, with the reason) and by the run-time rule
check: its mail is the tools. The live registry has no such rule today (checked 11-10), so nothing is left out by the
change.

## What goes away

`{platform_mail}` and its expansion in `runtime_claude._rules`; the raw `hubbus` rule; the mail commands in
`platform_settings`' allow list; Bash in the mail handlers' tools and in the confined default. `llm.py` still puts the
hub's `bin` on `PATH` for unconfined points, whose prompts name the commands.

## Order of work

1. M3: the server (serving a confined run, values as `--name=value`, ids checked, `note` without following links,
   `send` with `question`), with tests of its own.
2. M2: `ToolServer`, the `run_server` port, the Claude Code adapter, `runtime_fake`, doctor's table.
3. M1: the declarations, the linter, `platform_settings`, the confined default tools.
4. M5: the prompts, then the e3 mailbox cases.
5. M6: the registry and run-time rule checks.

## Tests

- The server: a platform run whose point doesn't declare `mail: tools`, or without `AGENT_HUB_CONFINED`, or without
  `AGENTHUB_PROJECT`, is refused; one that does is served, with the project from the environment whatever its working
  directory; `--text=--withdraw` reaches `hubbus` as text; an id not shaped like a letter id is refused before
  `hubbus`; `MAILBOX_LETTERS` limits hold through the server (a reply to another letter is refused); `note` refuses a
  linked day file and a linked `memory` folder.
- The runtime: a `mail: tools` point's command has `--mcp-config` with the reserved id and only the seven tool rules,
  no Bash rule, no Bash tool for the handlers; an `allow:` job without a Bash rule has no Bash; a runtime without
  `run_server` refuses the point; an environment server with the reserved id refuses the run; the settings file's
  allow list is empty.
- The registry: a rule naming the CLI under Bash, or the reserved server, is refused with the reason.
- `tests/test_runtime_boundary.py` and `tests/test_tool_agnostic.py` keep passing: the MCP syntax lives only in
  `runtime_claude.py`.
