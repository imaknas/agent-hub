#!/usr/bin/env bash
# Run a scheduled job that "needs Claude's judgment" (e.g. a daily check, video analysis).
# Replaces Claude Desktop's "Scheduled" tasks: those only run while the laptop is open and awake; this runs on the hub
# machine, triggered by scheduler.py from schedule.yaml, so all schedules are managed in one place, schedule.yaml.
#
# Usage (as the command in schedule.yaml): run-agent-job.sh <name> [workdir] [prompt name]
#   prompt: agent-jobs/<prompt name, default name>.md (version-controlled); without a workdir, the job's own state
#   directory is used
#   (mailbox requests use the platform's templates/prompts/mailbox.md, which hubbus passes as a full path after filling
#   in the names; name is mailbox-<project>, each with its own state directory)
#   Output: .state/agent-jobs/<name>/
#     <YYYY-MM-DD-HHMM>.md   each run's report (Claude's last reply)
#     status.json            the last run's status (running/ok/failed, times, session id, report path)
#     STOP                   created when the job itself decides "no need to run any more" (e.g. its stop condition
#                            holds); skipped from then on
#   A report whose first line starts with 🔔 = needs the owner's attention; the status page flags it.
#
# Returns as soon as it's running in the background: scheduler runs jobs one at a time, an LLM job easily takes half
# an hour, and it mustn't hold up the upload jobs behind it that have to run on time. Success/failure is recorded in
# status.json, not the scheduler's log.
#
# Judgment points: a job that declares allow: (AGENT_JOB_ALLOW) runs under agent-job-confined, event-triggered ones
# under agent-job-event, mailbox requests under mailbox-escalation (AGENT_JOB_POINT): all three load only the
# platform's settings for the project (docs/design/event-jobs.md 3.2), so neither the project's saved permissions nor
# its hooks apply, and the notes they write are committed by the hourly sync-all-memory. Only a job without allow:
# (agent-job, auto mode, flagged on the status page) still runs with the project's settings and hooks: when the
# workdir is an onboarded project its SessionEnd commits+pushes the .agent-memory notes.
#
# Uses the claude.ai subscription login (the hub machine's keychain; readable under launchd, not under SSH);
# llm.py decides the credentials: the headless token by default; registry jobs that set connectors: true (claude.ai
# connectors like Upwork) get LLM_AUTH=subscription and use the subscription login: with CLAUDE_CODE_OAUTH_TOKEN set,
# connectors don't load.
set -uo pipefail

HUB="${AGENT_HUB_ROOT:-${BRUNNA_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}}"
source "$(dirname "${BASH_SOURCE[0]}")/hubpaths.sh"
NAME="${1:?$(hub_msg "usage: run-agent-job.sh <name> [workdir] [prompt name]")}"
PROMPT="$HUB/agent-jobs/${3:-$NAME}.md"
case "${3:-}" in /*) PROMPT="$3" ;; esac  # registry agent job: the prompt is the full path of projects/<name>/jobs/*.md
OUT="$HUB/.state/agent-jobs/$NAME"
CWD="${2:-$OUT}"
mkdir -p "$OUT"

status() {  # status <state> [key=value...]
  "$HUB/bin/py" - "$OUT/status.json" "$@" <<'PY'
import json, sys, datetime, pathlib
path, state, *kv = sys.argv[1:]
p = pathlib.Path(path)
d = json.loads(p.read_text()) if p.exists() else {}
d["state"] = state
d["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
for item in kv:
    k, v = item.split("=", 1)
    d[k] = v
p.write_text(json.dumps(d, ensure_ascii=False, indent=2))
PY
}

[ -f "$PROMPT" ] || { hub_say "run-agent-job: {prompt} not found" prompt="$PROMPT" >&2; exit 1; }
if [ -f "$OUT/STOP" ]; then
  hub_say "run-agent-job: {name} has stopped itself ({reason}), skipping" name="$NAME" reason="$(head -1 "$OUT/STOP")"
  exit 0
fi
if [ ! -d "$CWD" ]; then
  # missing input must fail loudly (known-issues.md): e.g. the project hasn't moved to the hub machine's ~/workspace yet
  err="$(hub_msg "workdir {dir} does not exist" dir="$CWD")"
  status failed "error=$err" "started_at=$(date -Iseconds)"
  echo "run-agent-job: $err" >&2
  exit 1
fi

if [ -z "${RUN_AGENT_JOB_FOREGROUND:-}" ]; then
  # detach completely (double fork + setsid): when a launchd job ends, launchd kills what's left in the same process
  # group, and a plain nohup ... & gets killed along with the scheduler's tick (hit on 2026-10-14). This step's exit code is
  # whether the run started, up to its exec: the pipe closes at the exec (not inherited), or gets a byte if it can't happen
  if ! "$HUB/bin/py" - "$OUT/runner.log" "$0" "$@" <<'PY'
import os, sys
log, *argv = sys.argv[1:]
r, w = os.pipe()
if os.fork():
    os.close(w)
    sys.exit(1 if os.read(r, 1) else 0)
try:
    os.setsid()
    if os.fork():
        os._exit(0)
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(fd, 1); os.dup2(fd, 2)
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    os.environ["RUN_AGENT_JOB_FOREGROUND"] = "1"
    os.execv("/bin/bash", ["bash", *argv])
except OSError as e:
    print(f"run-agent-job: {e!r}", file=sys.stderr)
    os.write(w, b"x")
    os._exit(1)
PY
  then
    err="$(hub_msg "the run could not be started in the background")"
    status failed "error=$err" "started_at=$(date -Iseconds)" 2>/dev/null
    hub_say "run-agent-job: {name}: {error}" name="$NAME" error="$err" >&2
    exit 1
  fi
  hub_say "run-agent-job: {name} started in the background, the result goes to {status}" name="$NAME" status="$OUT/status.json"
  exit 0
fi

# ---- background: the actual run ----
unset CLAUDE_CODE_OAUTH_TOKEN
export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
stamp=$(date +%Y-%m-%d-%H%M)
report="$OUT/$stamp.md"
raw="$OUT/.$stamp.json"
status running "started_at=$(date -Iseconds)" "report=" "session_id=" "error=" "cwd=$CWD"

cd "$CWD" || exit 1
# judgment point (permission mode etc. declared in contracts/llm-points.yaml): AGENT_JOB_POINT when the caller names
# one (mailbox-escalation, agent-job-event); else a job that declares allow: (AGENT_JOB_ALLOW) is confined to it and
# the platform's settings (agent-job-confined, the project in AGENT_JOB_PROJECT), one that doesn't is agent-job
POINT="${AGENT_JOB_POINT:-}"
if [ -z "$POINT" ] || [ "$POINT" = "agent-job" ]; then
  POINT=agent-job
  [ -z "${AGENT_JOB_ALLOW:-}" ] || POINT=agent-job-confined
fi
# MCP servers from the environment aren't loaded (the global ones include production databases; turning tools off
# doesn't block them, 10-27 sealed experiment): --strict-mcp-config.
# Exception: registry jobs with connectors: true (LLM_AUTH=subscription, e.g. Upwork) need claude.ai connectors,
# so they load the environment's servers; llm.py then denies every server their allow: doesn't name, found for that
# very run, and doesn't run them at all when it can't tell (the points' tool_servers: allowed_only)
MCP_ARGS=()
[ "${LLM_AUTH:-}" = "subscription" ] || MCP_ARGS=(-- --strict-mcp-config)
# an event-triggered run (AGENT_HUB_EVENTS, docs/design/event-jobs.md): the job's prompt plus the events, as data
RUN_PROMPT="$PROMPT"
if [ -n "${AGENT_HUB_EVENTS:-${BRUNNA_EVENTS:-}}" ]; then
  RUN_PROMPT="$OUT/.$stamp.prompt.md"
  if ! "$HUB/bin/py" "$HUB/bin/event_jobs.py" prompt "$PROMPT" "$RUN_PROMPT" 2>"$OUT/.$stamp.prompt.err"; then
    # never run without the events it was started for: that would consume them unseen
    status failed "error=$(tail -c 300 "$OUT/.$stamp.prompt.err" | tr '\n' ' ')" "finished_at=$(date -Iseconds)"
    rm -f "$RUN_PROMPT" "$OUT/.$stamp.prompt.err"
    exit 1
  fi
  rm -f "$OUT/.$stamp.prompt.err"
fi
# ${a[@]+"${a[@]}"}: bash < 4.4 (macOS's /bin/bash, which the background run uses) takes an empty array for an unbound
# variable under set -u, and a connectors job died right here, its status left at running (10-27 to 11-01)
"$HUB/bin/py" "$HUB/bin/llm.py" run "$POINT" --prompt-file "$RUN_PROMPT" ${MCP_ARGS[@]+"${MCP_ARGS[@]}"} >"$raw" 2>"$raw.err"
rc=$?
[ "$RUN_PROMPT" = "$PROMPT" ] || rm -f "$RUN_PROMPT"

# the report's footer is written below (owner-facing text, in the UI language); this step leaves its values in .footer
rm -f "$OUT/.footer"
"$HUB/bin/py" - "$raw" "$report" "$OUT/.footer" "$(hub_msg "claude printed no JSON")" <<'PY' >"$OUT/.parsed" 2>&1
import json, sys
raw, report, footer, no_json = sys.argv[1:]
try:
    d = json.loads(open(raw).read())
except ValueError:
    print(f"error={no_json}")
    sys.exit(0)
result = (d.get("result") or "").strip()
with open(report, "w") as f:
    f.write(result + "\n\n---\n")
with open(footer, "w") as f:
    f.write(f"{d.get('session_id')}\n{d.get('num_turns')}\n{round((d.get('duration_ms') or 0) / 60000, 1)}\n")
print(f"session_id={d.get('session_id', '')}")
print(f"notify={'yes' if result.startswith('🔔') else 'no'}")
print(f"summary={result.splitlines()[0][:200] if result else ''}")
if d.get("is_error"):
    # llm_error: why llm.py refused or failed the run (e.g. it could not tell which tool servers the run would load)
    print("error=" + " ".join(str(d.get("llm_error") or d.get("subtype", "is_error")).split())[:300])
PY
if [ -f "$OUT/.footer" ]; then
  { read -r sid; read -r turns; read -r minutes; } <"$OUT/.footer"
  { hub_say "job: {name} · workdir: {cwd} · session: {session} · {turns} {turns:turn|turns} · {minutes} min" \
      name="$NAME" cwd="$CWD" session="$sid" turns="$turns" minutes="$minutes"
    hub_say "(to continue the discussion: on the hub machine, cd {cwd} && claude --resume {session})" cwd="$CWD" session="$sid"
  } >>"$report"
  rm -f "$OUT/.footer"
fi

mapfile_args=()
while IFS= read -r line; do mapfile_args+=("$line"); done <"$OUT/.parsed"
ok=1
if [ $rc -eq 0 ] && ! grep -q '^error=' "$OUT/.parsed"; then
  status ok "finished_at=$(date -Iseconds)" "report=$report" "${mapfile_args[@]}"
  rm -f "$raw" "$raw.err"
else
  ok=0
  status failed "finished_at=$(date -Iseconds)" "exit=$rc" "stderr=$(tail -c 500 "$raw.err" | tr '\n' ' ')" "${mapfile_args[@]}"
fi
rm -f "$OUT/.parsed"
# the status page reflects the result right away (no waiting for the next scheduler tick)
"$HUB/bin/py" "$HUB/bin/gen-status-page.py" >/dev/null 2>&1 || true
# keep old reports for 90 days
find "$OUT" -name '20*.md' -mtime +90 -delete 2>/dev/null
# run in place (the scheduler runs agent event jobs this way, as its own background job): the exit code is the run's
# outcome, so a failed run doesn't count as having taken its events
[ $ok -eq 1 ] || exit 1
