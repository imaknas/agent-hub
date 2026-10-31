#!/bin/sh -c exec${IFS}"${0%/*}/../py"${IFS}"$0"${IFS}"$@"
"""E3 of docs/design/i18n.md: the rules every project agent reads, tested by running a headless agent in a throwaway
project with the old and the new rules, and checking what it did.

Each run builds a small project (git repo, `.agent-memory/` with AGENT.md, STATE.md and a note, the platform's managed
block in the runtime's instructions file) and runs the owner's request through bin/llm.py as the `agent-job` point (the
default permission mode of an unattended agent job). The agent uses the real `agenthub`, but the run has its own copy of
the config (the project registered there, the assistant's API pointed nowhere) and its own bus, so nothing is
delivered; the checks read that bus. (A fake `agenthub` first on PATH does not work: the agent's shell rebuilds PATH from
the login profile.) Scenario inputs are per content language in `locales/<lang>.e3.json`.

Two scenarios reach other templates the way production does:
  mailbox  templates/prompts/mailbox.md: letters in the project's mailbox, claimed for the platform the way `hubbus
           dispatch` claims them, and the rendered prompt run as the `mailbox-escalation` point (its permission mode,
           its tools), as run-agent-job.sh runs it for hubbus
  mcp      templates/mcp-instructions.md: no rules file in the project, only the platform's MCP server (`agenthub mcp`
           = bin/mcp_server.py of the arm's tree, the run's config) given to the agent with --mcp-config; asked to
           check the mail

Three reach the prompts of `hubbus dispatch`'s background branch copies (templates/prompts/mailbox-resume.md,
mailbox-delegate.md), rendered by the arm's own bin/hubbus for one letter claimed the way resume() claims it and run as
the `mailbox-conversation` point (its permission mode and allow list). Production forks a live session with its
context (the runtime's resume_args); an eval never fabricates a transcript in the runtime's store, so here the copy is
a fresh agent in the throwaway project, and what the session would know is a memory note:
  resume             a reply to a letter this project sent (the thread in the bus, to_session set)
  delegate_question  a request the README answers
  delegate_decision  a question that needs the owner's decision
Two run the lease session's instructions (bin/lease-runner.py preamble(), the arm's own) as the `lease-session` point,
one owner message, no permission relay (asking a question and show_file are refused). Production appends the
instructions to the system prompt with a runtime flag; e3 stays neutral and puts them in the project's instructions
file after the managed block, the same place for both arms:
  lease_question     a question the README answers
  lease_change       a request to change a file (discuss is read-only: it goes into the handover)

    e3.py run --arm old|new|new-en [--k 3] [--scenario <name>] [--run <id>] [--jobs 2] [--old-ref <commit>]
    e3.py score <run> [--json]

Arms: `old` = templates/ (and, for mcp, the branch copies and the lease, the whole tree) at OLD_REF, what production
sends before the translation (--old-ref: the texts moved out of code were still Chinese at a later commit);
`new` = the working tree; `new-en` = the working tree on an instance whose languages are English (the run's config
says so, the inputs are locales/en.e3.json; scored on the same checks and on language).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

BIN = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(BIN / "evals"))
import commit_title  # noqa: E402
import e2  # noqa: E402
import hubconf  # noqa: E402
import prompts  # noqa: E402
import runtime_ports  # noqa: E402
import todo_tokens  # noqa: E402

OLD_REF = "6e92c90"   # main before the agent rules were translated (step 5)
CODE_REF = "6fb8a2d"  # main before the texts in code were (hubbus branch prompts, MCP tool descriptions, lease preamble)
PROJECT = "rates"
SENDER = "dashboard"
FAKE_SESSION = "e3e3e3e3-0000-4000-8000-000000000000"   # the session the resume scenario's letter was sent from
BRANCH = {"resume": "resume", "delegate_question": "delegate", "delegate_decision": "delegate"}
LEASE = {"lease_question": "discuss", "lease_change": "discuss"}
RULES = ("engineering", "conventional-commits")
GIT_ENV = {"GIT_AUTHOR_NAME": "owner", "GIT_AUTHOR_EMAIL": "owner@example.invalid",
           "GIT_COMMITTER_NAME": "owner", "GIT_COMMITTER_EMAIL": "owner@example.invalid"}
MID = re.compile(r"\bm\d{8}-[0-9a-f]{4}\b")


def lang_of(arm: str) -> str:
    return "en" if arm == "new-en" else "zh-Hant"


def data(lang: str = "zh-Hant") -> dict:
    return json.loads((hubconf.ROOT / "locales" / f"{lang}.e3.json").read_text())


def template(rel: str, arm: str) -> str:
    if arm != "old":
        return (hubconf.ROOT / rel).read_text()
    return subprocess.run(["git", "-C", str(hubconf.ROOT), "show", f"{OLD_REF}:{rel}"], capture_output=True, text=True,
                          check=True).stdout


def fill(text: str, arm: str) -> str:
    return prompts.fill(text, lang=lang_of(arm))


def managed_block(arm: str) -> str:
    """what sync-local-setup.sh writes into the instructions file (prompts.agent_rules on the arm's templates)"""
    body, missing = prompts.agent_rules(list(RULES), read=lambda rel: template(rel, arm), lang=lang_of(arm))
    assert not missing, missing
    return f"<!-- agent-hub:begin -->\n{body}\n<!-- agent-hub:end -->\n"


def old_tree(run_dir: pathlib.Path) -> pathlib.Path:
    """the platform at OLD_REF, for what the old arm runs as code (the MCP server reads its own template)"""
    tree = run_dir / f"old-tree-{OLD_REF}"
    if not (tree / "bin" / "mcp_server.py").exists():
        tree.mkdir(parents=True, exist_ok=True)
        arc = subprocess.run(["git", "-C", str(hubconf.ROOT), "archive", OLD_REF], capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(tree)], input=arc, check=True)
    return tree


def build(proj: pathlib.Path, arm: str, scenario: str) -> None:
    d = data(lang_of(arm))
    proj.mkdir(parents=True)
    env = {**os.environ, **GIT_ENV, "GIT_CONFIG_GLOBAL": "/dev/null"}
    git = lambda *a: subprocess.run(["git", "-C", str(proj), *a], env=env, check=True, capture_output=True)  # noqa: E731
    (proj / "README.md").write_text(d["readme"])
    (proj / "app.py").write_text("print('rates')\n")
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "chore: initial commit")
    rt = runtime_ports.load()
    ignores = [".agent-memory/", *rt.local_ignores()]
    with (proj / ".git" / "info" / "exclude").open("a") as f:
        f.write("\n".join(ignores) + "\n")
    mem = proj / ".agent-memory"
    (mem / "memory").mkdir(parents=True)
    (mem / "AGENT.md").write_text(template("templates/AGENT.md", arm))   # copied as is (sync-local-setup.sh 2b)
    (mem / "STATE.md").write_text(d["state"].replace("{owner}", hubconf.owner_tag()))
    (mem / "memory" / "2026-10-29.md").write_text(d["note_old"])
    if scenario == "resume":   # what the forked session would know: it asked the sender
        (mem / "memory" / "2026-10-30.md").write_text(d["resume_context"])
    (mem / "RULES").write_text("\n".join(RULES) + "\n")
    if scenario != "mcp":   # the MCP scenario tests what an agent with only the platform's MCP server is told
        rt.instructions_file(proj).write_text(managed_block(arm))


def snapshot(proj: pathlib.Path) -> dict[str, str]:
    out = {}
    for p in sorted(proj.rglob("*")):
        rel = p.relative_to(proj)
        if p.is_file() and ".git" not in rel.parts:
            out[str(rel)] = p.read_text(errors="replace")
    return out


def llm_cmd(prompt_file: pathlib.Path, *extra: str, point: str = "agent-job", agent: tuple = ()) -> list[str]:
    """the point the way bin/run-agent-job.sh calls it: sealed from the environment's MCP servers
    (`--strict-mcp-config`, unless the point declares it itself; without it the agent gets every MCP configured on
    this machine, production databases included; evaluators.md principle 4). extra: llm.py's options; agent: the
    agent's (after --)"""
    import llm
    sealed = "--strict-mcp-config" in (llm.points()["points"][point].get("extra_args") or [])
    return [str(BIN / "py"), str(BIN / "llm.py"), "run", point, "--prompt-file", str(prompt_file), *extra,
            "--", *(() if sealed else ("--strict-mcp-config",)), *agent]


def register(cfg: pathlib.Path, name: str, ws: pathlib.Path, extra: str = "") -> None:
    reg = cfg / (json.loads((cfg / e2.HUB_JSON).read_text()).get("registry_dir") or "projects")
    (reg / f"{name}.yaml").write_text(f"v: 1\nname: {name}\npaths:\n  workspace: {ws}\n{extra}")


def send(hb: list, renv: dict, letter: dict) -> str:
    """letter: type, text, from (default SENDER), to (default PROJECT), reply_to"""
    r = subprocess.run([*hb, "send", "--from", letter.get("from", SENDER), "--to", letter.get("to", PROJECT),
                        "--type", letter["type"], "--text", letter["text"],
                        *(("--reply-to", letter["reply_to"]) if letter.get("reply_to") else ())],
                       env=renv, capture_output=True, text=True, check=True)
    return MID.search(r.stdout + r.stderr).group(0)


def sent_from_session(renv: dict, letter: dict, session: str) -> str:
    """a letter this project sent from a session, as `hubbus send` records it inside one (from_session set, so the
    reply to it is addressed to that session); recorded directly, the session being made up"""
    r = subprocess.run([str(BIN / "py"), str(pathlib.Path(__file__).resolve()), "sent-from", "--session", session,
                        "--source", PROJECT, "--to", SENDER, "--type", letter["type"], "--text", letter["text"]],
                       env=renv, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def claim_for_handler(renv: dict, ids: list[str], via: str = "handler", session: str | None = None) -> None:
    """what `hubbus dispatch` does before it starts the headless agent (via handler) or a background branch copy (via
    resume, with the session it branches): the letters are recorded as taken by the platform (the agent's inbox marks
    them "the platform is handling it")"""
    subprocess.run([str(BIN / "py"), str(pathlib.Path(__file__).resolve()), "claim", "--via", via,
                    *(("--session", session) if session else ()), *ids], env=renv, check=True, capture_output=True)


def hubbus_module():
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("hubbus_e3", str(BIN / "hubbus"))
    hb = importlib.util.module_from_spec(importlib.util.spec_from_loader("hubbus_e3", loader))
    loader.exec_module(hb)
    return hb


def cmd_sent_from(a) -> int:
    hb = hubbus_module()
    c = hb.db()
    try:
        mid = hb.new_id()
        hb.publish(c, mid, "message", a.source, a.to, None, {"type": a.type, "text": a.text, "from_session": a.session})
    finally:
        c.close()
    print(mid)
    return 0


def cmd_claim(a) -> int:
    hb = hubbus_module()
    c = hb.db()
    try:
        c.execute("BEGIN IMMEDIATE")
        extra = {"session": a.session} if a.session else {}
        hb.claim(c, [hb.get_message(c, i) for i in a.ids], a.via, run="e3", **extra)
        c.execute("COMMIT")
    finally:
        c.close()
    return 0


# Renders a text the way the arm's own code builds it in production (run in its own interpreter with the arm's bin/
# first on the path): the branch copies' prompt for a letter (hubbus resume()), the lease preamble (lease-runner
# preamble()).
RENDER = r"""
import importlib.machinery, importlib.util, json, sys
tree, kind, args = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
sys.path.insert(0, tree + "/bin")
def load(name, file):
    loader = importlib.machinery.SourceFileLoader(name, tree + "/bin/" + file)
    m = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(m)
    return m
if kind == "lease":
    sys.stdout.write(load("lease_runner_e3", "lease-runner.py").preamble(args["project"], args["mode"]))
else:
    hb = load("hubbus_e3", "hubbus")
    c = hb.db()
    r = hb.get_message(c, args["id"])
    name = hb.RESUME_PROMPT if kind == "resume" else hb.DELEGATE_PROMPT
    if hasattr(hb, "branch_prompt"):
        sys.stdout.write(hb.branch_prompt(r, name))
    else:   # before the prompts moved to templates/prompts/: the text, filled at import, then .format per letter
        sys.stdout.write(name.format(letter=hb.fmt(r), id=r["id"]))
"""


def render_as(tree: pathlib.Path, kind: str, renv: dict, **args) -> str:
    return subprocess.run([str(BIN / "py"), "-c", RENDER, str(tree), kind, json.dumps(args)], env=renv, cwd=str(tree),
                          capture_output=True, text=True, check=True).stdout


def mcp_config(path: pathlib.Path, arm: str, run_dir: pathlib.Path, renv: dict) -> pathlib.Path:
    tree = old_tree(run_dir) if arm == "old" else BIN.parent
    keys = ("AGENT_HUB_CONFIG", "AGENT_HUB_STATE", "AGENT_HUB_DATA", "AGENT_HUB_SECRETS", "AGENT_HUB_HOME")
    # HUB_LLM_POINT emptied: the server refuses agents the platform starts (their letters go out through the
    # platform), and this one stands for a session the owner opened; llm.py is only how the eval runs it headless
    env = {k: renv[k] for k in keys if renv.get(k)} | {"AGENTHUB_PROJECT": PROJECT, "HUB_LLM_POINT": ""}
    path.write_text(json.dumps({"mcpServers": {"agenthub": {
        "command": str(BIN / "py"), "args": [str(tree / "bin" / "mcp_server.py")], "env": env}}}, indent=1))
    return path


def one(scenario: str, arm: str, i: int, run_dir: pathlib.Path, env: dict) -> dict:
    out = run_dir / scenario / f"{arm}-{i}"
    if (out / "meta.json").exists():
        return json.loads((out / "meta.json").read_text())
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    lang = lang_of(arm)
    d = data(lang)
    with tempfile.TemporaryDirectory(prefix="e3-") as tmp:
        tmp = pathlib.Path(tmp).resolve()
        proj = tmp / PROJECT
        build(proj, arm, scenario)
        # this run's own config and bus: the throwaway project and the letter's sender are registered only there
        cfg = out / "config"
        shutil.copytree(env["AGENT_HUB_CONFIG"], cfg)
        hub = json.loads((cfg / e2.HUB_JSON).read_text())
        hub["bus_dir"] = str(out / "bus")
        if arm == "new-en":
            hub["language"] = {"ui": "en", "content": "en"}
        (cfg / e2.HUB_JSON).write_text(json.dumps(hub, ensure_ascii=False, indent=2))
        register(cfg, PROJECT, proj, "commits: conventional\n")
        (tmp / SENDER).mkdir()
        register(cfg, SENDER, tmp / SENDER)
        renv = {**env, **GIT_ENV, "AGENT_HUB_CONFIG": str(cfg), "AGENT_HUB_STATE": str(out / "state")}
        hb = [str(BIN / "py"), str(BIN / "hubbus")]
        subprocess.run([str(BIN / "py"), str(BIN / "registry.py"), "check"], env=renv, capture_output=True, check=True)
        letters: dict[str, str] = {}
        point, agent = "agent-job", ()
        tree = old_tree(run_dir) if arm == "old" else BIN.parent
        if scenario in BRANCH:
            if scenario == "resume":
                asked = sent_from_session(renv, d["resume_letters"]["asked"], FAKE_SESSION)
                letters["letter"] = send(hb, renv, {**d["resume_letters"]["reply"], "reply_to": asked})
            else:
                letters["letter"] = send(hb, renv, d["mailbox_letters"][scenario.removeprefix("delegate_")])
            claim_for_handler(renv, [letters["letter"]], via="resume", session=FAKE_SESSION)
            point = "mailbox-conversation"
            (out / "prompt.txt").write_text(render_as(tree, BRANCH[scenario], renv, id=letters["letter"]))
        elif scenario in LEASE:
            point = "lease-session"
            preamble = render_as(tree, "lease", renv, project=PROJECT, mode=LEASE[scenario])
            (out / "preamble.txt").write_text(preamble)
            with runtime_ports.load().instructions_file(proj).open("a") as f:
                f.write("\n" + preamble + "\n")
            (out / "prompt.txt").write_text(d["scenarios"][scenario]["prompt"])
        if scenario in ("letter", "mcp"):
            letters["letter"] = send(hb, renv, {"type": "request", "text": d["letter_text"]})
        if scenario == "mcp":
            agent = ("--mcp-config", str(mcp_config(out / "mcp.json", arm, run_dir, renv)))
        if scenario == "mailbox":
            for key, letter in d["mailbox_letters"].items():
                letters[key] = send(hb, renv, letter)
            claim_for_handler(renv, list(letters.values()))
            point = "mailbox-escalation"
            # what hubbus run_handler writes (prompts.render("mailbox") + the newline it strips)
            (out / "prompt.txt").write_text(fill(template("templates/prompts/mailbox.md", arm).removesuffix("\n"), arm) + "\n")
        elif scenario not in BRANCH and scenario not in LEASE:
            (out / "prompt.txt").write_text(d["scenarios"][scenario]["prompt"])
        before = snapshot(proj)
        t0 = datetime.datetime.now()
        r = subprocess.run(llm_cmd(out / "prompt.txt", point=point, agent=agent), cwd=proj, env=renv, capture_output=True,
                           text=True, timeout=3600)
        secs = (datetime.datetime.now() - t0).total_seconds()
        after = snapshot(proj)
        log = subprocess.run(["git", "-C", str(proj), "log", "--format=%s"], capture_output=True, text=True).stdout
        bodies = subprocess.run(["git", "-C", str(proj), "log", "--format=%B%x00"], capture_output=True,
                                text=True).stdout
    (out / "result.json").write_text(r.stdout)
    (out / "stderr.txt").write_text(r.stderr[-20000:])
    (out / "files.json").write_text(json.dumps({"before": before, "after": after, "git_log": log.splitlines(),
                                                "git_messages": [m.strip() for m in bodies.split("\0") if m.strip()],
                                                "letter": letters.get("letter"), "letters": letters},
                                               ensure_ascii=False, indent=1))
    try:
        obj = json.loads(r.stdout)
    except ValueError:
        obj = {}
    meta = {"scenario": scenario, "arm": arm, "i": i, "exit": r.returncode, "secs": round(secs, 1),
            "model": obj.get("llm_model") or obj.get("model"), "error": obj.get("llm_error"),
            "rate_limited": r.returncode != 0 and bool(e2.RATE_LIMIT.search((obj.get("llm_error") or "") + r.stderr[-2000:]))}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


# ---------- checks ----------

def bus(out: pathlib.Path) -> list[dict]:
    """this run's mailbox events: {type, source, to, ref, payload}"""
    db = out / "bus" / "bus.db"
    if not db.exists():
        return []
    c = sqlite3.connect(db)
    try:
        rows = c.execute("SELECT type, source, to_project, ref, payload FROM events ORDER BY seq").fetchall()
    finally:
        c.close()
    return [{"type": t, "source": s, "to": to, "ref": ref, "payload": json.loads(p or "{}")} for t, s, to, ref, p in rows]


def note_text(files: dict) -> str:
    """what the agent added to memory notes"""
    b, a = files["before"], files["after"]
    return "\n".join(e2.added_text(b.get(k, ""), v) for k, v in a.items()
                     if k.startswith(".agent-memory/memory/") and v != b.get(k))


def result_text(out: pathlib.Path) -> str:
    try:
        return str(json.loads((out / "result.json").read_text()).get("result") or "")
    except (OSError, ValueError):
        return ""


def add_language(r: dict, name: str, text: str) -> None:
    if text.strip():
        lang = e2.language(text)
        r[f"{name}_cjk_share"] = lang["cjk_share"]
        r[f"{name}_simplified"] = bool(lang["simplified"])


def check(scenario: str, out: pathlib.Path, lang: str = "zh-Hant") -> dict:
    files = json.loads((out / "files.json").read_text())
    expect = data(lang)["scenarios"][scenario]["expect"]
    note = note_text(files)
    evs = bus(out)
    sends = [e for e in evs if e["type"] == "message" and e["source"] == PROJECT]
    text = lambda e: str(e["payload"].get("text") or "")  # noqa: E731
    done = lambda mid: any(e["type"] == "message.done" and e["ref"] == mid for e in evs)  # noqa: E731
    new_commits = files["git_log"][:max(0, len(files["git_log"]) - 1)]
    code_same = lambda: all(files["after"].get(f) == files["before"].get(f) for f in ("app.py", "README.md"))  # noqa: E731
    r: dict = {"ran": json.loads((out / "meta.json").read_text())["exit"] == 0}
    if scenario == "note":
        r["note_written"] = bool(note.strip())
        r["note_has_fact"] = all(x in note for x in expect)
        r["code_untouched"] = files["after"].get("app.py") == files["before"].get("app.py")
        # AGENT.md: STATE.md is the consolidating agent's (reported: the old arm does it now and then too)
        r["state_untouched"] = files["after"].get(".agent-memory/STATE.md") == files["before"].get(".agent-memory/STATE.md")
    elif scenario == "owner_todo":
        st = files["after"].get(".agent-memory/STATE.md", "")
        items = e2.todo_items(st) or set()
        r["contract_ok"] = not e2.state_check(st)
        r["owner_item"] = any(o == "owner" and all(x in t for x in expect) for o, t in items)   # by role
        # the rules give two routes: a [owner] line in STATE.md (registered at night) or `send --type task` right away
        tasks = [e for e in evs if e["type"] == "record" and e["source"] == PROJECT
                 and (e["payload"].get("record") or {}).get("type") == "raised"]
        r["owner_task_raised"] = any(all(x in str(e["payload"]["record"].get("terms")) for x in expect) for e in tasks)
        r["owner_registered"] = r["owner_item"] or r["owner_task_raised"]
        r["kept_others"] = len(items) >= len(e2.todo_items(files["before"][".agent-memory/STATE.md"]) or ())
        added = e2.added_text(files["before"][".agent-memory/STATE.md"], st)
        # the to-do lines it added are in the content language's token set (contracts/state-todo.v2.md)
        new_lines = [l for l in added.splitlines() if l.startswith("- [ ] ")]
        if new_lines:
            own = re.escape(todo_tokens.tag(lang, "")[0])
            r["token_set"] = all(re.match(rf"- \[ \] {own}", l) for l in new_lines)
        add_language(r, "sent", "\n".join([added] + [str((e["payload"]["record"].get("terms") or {}).get("text"))
                                                      for e in tasks]))
    elif scenario == "ask_hub":
        to_hub = [e for e in sends if e["to"] == "agent-hub"]
        r["sent_to_hub"] = bool(to_hub)
        r["actionable_type"] = any(e["payload"].get("type") in ("request", "question") for e in to_hub)
        r["has_fact"] = any(all(x in text(e) for x in expect) for e in to_hub)
    elif scenario in ("letter", "mcp"):
        letter = files["letter"]
        replies = [e for e in sends if e["ref"] == letter]
        r["replied"] = bool(replies)
        r["reply_has_fact"] = any(all(x in text(e) for x in expect) for e in replies)
        r["closed"] = done(letter) or any(e["payload"].get("type") == "done" for e in replies)
        r["note_written"] = bool(note.strip())   # reported: the rules ask for a note after a commit, not per letter
        if scenario == "mcp":
            r["code_untouched"] = code_same() and not new_commits
    elif scenario == "commit":
        r["committed"] = bool(new_commits)
        r["title_ok"] = bool(new_commits) and all(commit_title.problem(t, final=True) is None for t in new_commits)
        r["fixed"] = all(x in files["after"].get("README.md", "") for x in expect) and "teh" not in files["after"].get("README.md", "")
        r["note_after_commit"] = bool(note.strip())
        add_language(r, "commit", "\n".join(files.get("git_messages", [])[:len(new_commits)]))
    elif scenario == "mailbox":
        ls, report = files["letters"], result_text(out)
        answers = [e for e in sends if e["ref"] == ls["question"]]
        to_owner = [e for e in sends if e["to"] == hubconf.owner_address()]
        r["answered"] = any(all(x in text(e) for x in expect["question"]) for e in answers)
        r["question_closed"] = done(ls["question"])
        r["code_untouched"] = code_same() and not new_commits
        r["change_noted"] = all(x in note for x in expect["change"])
        r["change_left_open"] = not done(ls["change"])
        r["owner_asked"] = any(e["payload"].get("type") == "question" for e in to_owner)
        r["decision_left_open"] = not done(ls["decision"])
        r["report_bell"] = report.lstrip().startswith("🔔")
        r["report_names_letters"] = all(mid in report for mid in ls.values())
        add_language(r, "report", report)
    elif scenario in BRANCH:
        letter, reply = files["letter"], result_text(out)
        r["code_untouched"] = code_same() and not new_commits
        r["final_reply"] = bool(reply.strip())
        to_owner = [e for e in sends if e["to"] == hubconf.owner_address()]
        r["owner_asked"] = any(e["payload"].get("type") == "question" for e in to_owner)
        r["closed"] = done(letter)
        if scenario == "resume":
            r["note_has_fact"] = all(x in note for x in expect)
            r["note_written"] = bool(note.strip())
            r["replied"] = any(e["ref"] == letter for e in sends)   # reported: the reply needs no answer
        elif scenario == "delegate_question":
            replies = [e for e in sends if e["ref"] == letter and e["to"] == SENDER]
            r["replied"] = bool(replies)
            r["reply_has_fact"] = any(all(x in text(e) for x in expect) for e in replies)
        else:
            r["note_written"] = bool(note.strip())
        add_language(r, "reply", reply)
    elif scenario in LEASE:
        reply = result_text(out)
        r["files_untouched"] = files["after"] == files["before"] and not new_commits
        r["reply_has"] = all(x in reply for x in expect)
        try:
            r["denials"] = len(json.loads((out / "result.json").read_text()).get("permission_denials") or [])
        except (OSError, ValueError):
            pass
        add_language(r, "reply", reply)
    if scenario not in ("owner_todo",):
        add_language(r, "sent", "\n".join(text(e) for e in sends))
    add_language(r, "note", note)
    return r


def cmd_run(a) -> int:
    scen = [a.scenario] if a.scenario else list(data(lang_of(a.arm))["scenarios"])
    run_id = a.run or f"{datetime.datetime.now():%Y%m%d-%H%M}-e3"
    run_dir = e2.root() / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    global OLD_REF
    OLD_REF = a.old_ref or OLD_REF
    (run_dir / "run.json").write_text(json.dumps({"point": "e3", "old_ref": OLD_REF}, indent=1))
    env = e2.run_env(run_dir)
    print(f"e3: arm={a.arm} k={a.k} scenarios={scen} → {run_dir}")
    stop = False
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as pool:
        futs = [pool.submit(one, s, a.arm, i, run_dir, env) for i in range(a.k) for s in scen]
        for f in concurrent.futures.as_completed(futs):
            if f.cancelled():
                continue
            m = f.result()
            print(f"  {m['scenario']} {m['arm']}-{m['i']}: exit {m['exit']} {m['secs']}s {m['model'] or ''} {m['error'] or ''}")
            if m["rate_limited"] and not stop:
                stop = True
                print("e3: rate limited, cancelling the rest (rerun the same command to continue)")
                for g in futs:
                    g.cancel()
    print(run_id)
    return 1 if stop else 0


def cmd_score(a) -> int:
    run_dir = e2.root() / "runs" / a.run
    report: dict = {}
    for out in sorted(run_dir.glob("*/*-*")):
        if not (out / "meta.json").exists():
            continue
        scenario, arm = out.parent.name, out.name.rsplit("-", 1)[0]
        report.setdefault(scenario, {}).setdefault(arm, []).append(check(scenario, out, lang_of(arm)))
    summary = {s: {arm: {k: e2.mean([r.get(k) for r in rs]) for k in sorted({k for r in rs for k in r})}
                   for arm, rs in arms.items()} for s, arms in report.items()}
    (run_dir / "score.json").write_text(json.dumps({"runs": report, "summary": summary}, ensure_ascii=False, indent=1))
    if a.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    else:
        for s, arms in summary.items():
            for arm, m in arms.items():
                print(f"{s} {arm} (n={len(report[s][arm])}): " + ", ".join(f"{k}={v}" for k, v in m.items()))
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="e3.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--arm", choices=["old", "new", "new-en"], required=True)
    r.add_argument("--k", type=int, default=3)
    r.add_argument("--scenario")
    r.add_argument("--run")
    r.add_argument("--jobs", type=int, default=2)
    r.add_argument("--old-ref", help=f"the old arm's commit (default {OLD_REF}; {CODE_REF} for the texts moved out of "
                   "code: mcp's tool descriptions, the branch copies, the lease)")
    s = sub.add_parser("score")
    s.add_argument("run")
    s.add_argument("--json", action="store_true")
    f = sub.add_parser("sent-from", help="(internal) record a letter sent from a session, in the run's bus")
    for k in ("--session", "--source", "--to", "--type", "--text"):
        f.add_argument(k, required=True)
    c = sub.add_parser("claim", help="(internal) record letters as taken by the platform, in the run's bus")
    c.add_argument("--via", default="handler")
    c.add_argument("--session")
    c.add_argument("ids", nargs="+")
    a = ap.parse_args(argv)
    return {"run": cmd_run, "score": cmd_score, "claim": cmd_claim, "sent-from": cmd_sent_from}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
