"""E1 of docs/design/i18n.md: golden outputs of the mechanical (no model) paths the owner sees.

Captured from the code as it was before any message moved behind a catalog. While strings move to English msgids with a
verbatim zh-Hant catalog, an instance on zh-Hant must keep producing exactly these outputs; any difference has to be
listed and approved. Each case runs real commands in a temp instance (temp config, data, bus, HOME) and normalizes
temp paths, ids and timestamps.

    bin/py -m unittest tests.test_i18n_golden
    I18N_GOLDEN_UPDATE=1 bin/py -m unittest tests.test_i18n_golden    # rewrite the goldens (only on purpose)
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_lang  # noqa: E402

GOLDEN = golden_lang.GOLDEN
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
# The instance language the goldens are for: zh-Hant must stay byte-identical to the output before translation
LANGUAGE = {"ui": golden_lang.LANG, "content": golden_lang.LANG}

# Chinese inputs live in the fixture (tests/fixtures may hold Chinese; this file may not)
IN = json.loads((golden_lang.INPUTS / "inputs.json").read_text())


class Golden(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        for d in ("home", "config/projects", "data", "secrets", "bus", "state", "copies", "ws"):
            (self.tmp / d).mkdir(parents=True)
        self.cfg = {"owner": {"name": "alex", "tag": "Alex", "principal_id": "person:alex"}, "owner_side": "builtin",
                    "bus_dir": str(self.tmp / "bus"), "registry_dir": "projects", "memory_copies": str(self.tmp / "copies"),
                    "workspace_root": str(self.tmp / "ws"), "services_root": str(self.tmp / "svc"), "language": LANGUAGE}
        self.write_cfg()
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("AGENT_HUB_", "GIT_")) or k == "AGENT_HUB_PYTHON"},
                    "AGENT_HUB_ROOT": str(ROOT),   # the tree under test (no lookup of a main checkout from a worktree)
                    "HOME": str(self.tmp / "home"), "AGENT_HUB_CONFIG": str(self.tmp / "config"),
                    "AGENT_HUB_DATA": str(self.tmp / "data"), "AGENT_HUB_SECRETS": str(self.tmp / "secrets"),
                    "AGENT_HUB_STATE": str(self.tmp / "state"), "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                    "COLUMNS": "120", "LANG": "en_US.UTF-8", "TZ": "Asia/Taipei"}

    def write_cfg(self):
        (self.tmp / "config" / "hub.json").write_text(json.dumps(self.cfg))

    def run_(self, *argv, stdin="", cwd=None):
        r = subprocess.run([sys.executable, *map(str, argv)], input=stdin, env=self.env, cwd=cwd or self.tmp,
                           capture_output=True, text=True, timeout=120)
        return f"$ {' '.join(map(str, argv[:1]))}{' ' if len(argv) > 1 else ''}{' '.join(map(str, argv[1:]))}\n" \
               f"[exit {r.returncode}]\n--- stdout\n{r.stdout}--- stderr\n{r.stderr}"

    def register(self, name, extra=""):
        ws = self.tmp / "ws" / name
        ws.mkdir(parents=True, exist_ok=True)
        (self.tmp / "config" / "projects" / f"{name}.yaml").write_text(
            f"v: 1\nname: {name}\npaths:\n  workspace: {ws}\n{extra}")
        self.refresh()
        return ws

    def refresh(self):
        """rebuild the registry index (what hubconf.projects() reads)"""
        subprocess.run([sys.executable, str(BIN / "registry.py"), "check"], env=self.env, capture_output=True, timeout=60)

    def normalize(self, text: str) -> str:
        text = text.replace(str(self.tmp), "<TMP>").replace(str(ROOT), "<HUB>").replace(sys.executable, "<PY>")
        text = re.sub(r"\bm\d{8}-[0-9a-f]{4}\b", "<ID>", text)
        text = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?([+-]\d{2}:\d{2}|Z)?", "<TS>", text)
        text = re.sub(r"\b\d{2}-\d{2} \d{2}:\d{2}\b", "<TS>", text)
        text = re.sub(r"\b\d{8}-\d{6}-", "<STAMP>-", text)   # agenthub test's output files
        text = re.sub(r"\b[0-9a-f]{7,40}\b", "<SHA>", text)
        return text

    def check(self, name: str, text: str):
        text = self.normalize(text)
        f = GOLDEN / f"{name}.txt"
        if UPDATE or not f.exists():
            GOLDEN.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text(), f"output differs from {f.relative_to(ROOT)}")

    # --- help texts ---------------------------------------------------------------------------------------------
    def test_help(self):
        out = [self.run_(BIN / "agenthub", "--help"), self.run_(BIN / "hubbus", "--help"),
               self.run_(BIN / "schedulectl", "--help"), self.run_(BIN / "merge-branch", "--help")]
        self.check("help", "\n".join(out))

    # --- validation messages ------------------------------------------------------------------------------------
    def test_commit_titles(self):
        out = [self.run_(BIN / "commit_title.py", t) for t in ("Add LICENSE", "feature: x", "feat:x", "")]
        self.check("commit-title", "\n".join(out))

    def test_registry_errors(self):
        self.register("ok-proj", "commits: conventional\n")
        (self.tmp / "config" / "projects" / "bad-one.yaml").write_text(
            "v: 2\nname: other\ntype: thing\ncommits: angular\ngate: loose\nnope: 1\nprivacy:\n  reflection: maybe\n")
        (self.tmp / "config" / "projects" / "bad-idea.yaml").write_text("v: 1\nname: bad-idea\ntype: idea\n")
        self.check("registry-check", self.run_(BIN / "registry.py", "check"))

    def test_config_check(self):
        self.cfg["unknown_key"] = 1
        self.cfg["modules"] = {"no-such-module": True}
        self.write_cfg()
        self.check("config-check", self.run_(BIN / "config-check"))

    # --- to-do pipeline -----------------------------------------------------------------------------------------
    def test_todo(self):
        for name, state in (("alpha", IN["state_ok"]), ("beta", IN["state_bad"])):
            self.register(name)
            (self.tmp / "copies" / name).mkdir()
            (self.tmp / "copies" / name / "STATE.md").write_text(state)
        self.register("gamma")   # no STATE.md in the memory copy
        out = self.tmp / "data" / "todo.md"
        log = self.run_(BIN / "gen-todo.py", out)
        bad = self.tmp / "copies" / "beta" / "STATE.md"
        self.check("todo", log + "\n=== todo.md\n" + (out.read_text() if out.exists() else "(none)\n")
                   + "\n" + self.run_(BIN / "check-state-todo.py", bad))

    # --- mailbox ------------------------------------------------------------------------------------------------
    def test_mailbox(self):
        self.register("other")
        ws = self.register("demo")
        hb = BIN / "hubbus"
        out = [self.run_(hb, "send", "--from", "other", "--to", "demo", "--type", "request", "--text", IN["request"]),
               self.run_(hb, "send", "--from", "other", "--to", "demo", "--type", "info", "--text", IN["info"])]
        out.append(self.run_(hb, "inbox", "--project", "demo"))
        out.append(self.run_(hb, "hook", "prompt", cwd=ws,
                             stdin=json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": str(ws), "session_id": "s1"})))
        out.append(self.run_(hb, "open-questions", "--project", "demo"))
        self.check("mailbox", "\n".join(out))

    def test_owner_builtin(self):
        rec = {"ph": "0.3", "ext": "acts/0.3", "id": "r:task-demo-0001", "type": "raised", "origin": "requester",
               "by": "agent-hub:registrar", "key": "task-demo-0001", "project": "demo", "ts": "2026-10-29T01:00:00Z",
               "terms": {"text": IN["task"]}, "terms_rev": 1}
        self.register("demo")
        out = [self.run_(BIN / "hubbus", "record-publish", "--json", "-", stdin=json.dumps(rec, ensure_ascii=False)),
               self.run_(BIN / "owner.py", "list"),
               self.run_(BIN / "owner.py", "done", "task-demo-0001", "--note", IN["note"]),
               self.run_(BIN / "owner.py", "done", "no-such-key")]
        self.check("owner", "\n".join(out))

    # --- merge gate ---------------------------------------------------------------------------------------------
    def git(self, cwd, *args):
        subprocess.run(["git", "-C", str(cwd), *args], env=self.env, capture_output=True, text=True, check=True)

    def test_merge_branch(self):
        (self.tmp / "svc" / "demo").mkdir(parents=True)      # a registered runtime needs its services dir
        ws = self.register("demo", "commits: conventional\nruntime:\n  test: \"false\"\n")
        self.git(self.tmp, "init", "-q", "-b", "main", str(ws))
        (ws / "a").write_text("a")
        self.git(ws, "add", "a")
        self.git(ws, "commit", "-q", "-m", "chore: initial commit")
        out = [self.run_(BIN / "merge-branch", cwd=ws)]                     # on main, in the main checkout
        wt = self.tmp / "wt"
        self.git(ws, "worktree", "add", "-q", "-b", "feature", str(wt))
        out.append(self.run_(BIN / "merge-branch", cwd=wt))                 # nothing to merge
        (wt / "b").write_text("b")
        out.append(self.run_(BIN / "merge-branch", cwd=wt))                 # dirty worktree
        self.git(wt, "add", "b")
        self.git(wt, "commit", "-q", "-m", "Update stuff")
        out.append(self.run_(BIN / "merge-branch", cwd=wt))                 # bad title
        self.git(wt, "commit", "-q", "--amend", "-m", "feat: b")
        out.append(self.run_(BIN / "merge-branch", cwd=wt))                 # tests fail
        out.append(self.run_(BIN / "merge-branch", cwd=self.tmp))           # not a project repo
        self.check("merge-branch", "\n".join(out))

    # --- alerts -------------------------------------------------------------------------------------------------
    def test_alert_texts(self):
        import importlib.util
        sys.path.insert(0, str(BIN))
        spec = importlib.util.spec_from_file_location("alert_golden", BIN / "alert.py")   # a private copy: other tests
        alert = importlib.util.module_from_spec(spec)                                     # patch the shared module
        spec.loader.exec_module(alert)
        sent = []
        with mock.patch.object(alert, "SENT", self.tmp / "state" / "alerts-sent.json"), \
             mock.patch.object(alert, "FAILED", self.tmp / "state" / "alerts-failed.json", create=True), \
             mock.patch.object(alert, "send", side_effect=lambda t: sent.append(t) or True), \
             mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": LANGUAGE["ui"]}):
            alert.once("k", [IN["problem"]], IN["recovered"])
            data = json.loads((self.tmp / "state" / "alerts-sent.json").read_text())
            data["k"]["at"] = "2026-10-01T00:00:00"
            (self.tmp / "state" / "alerts-sent.json").write_text(json.dumps(data))
            alert.once("k", [IN["problem"]], IN["recovered"])
            alert.once("k", [], IN["recovered"])
        self.check("alerts", "\n---\n".join(sent) + "\n")

    # --- agenthub: doctor, modules, config, llm, init, move-instance, install-cli --------------------------------
    def valid_cfg(self, **extra):
        self.cfg.update(v=1, **extra)
        self.cfg["owner"]["address"] = "owner"
        self.write_cfg()

    def fake_hub(self) -> pathlib.Path:
        """a hub root (AGENT_HUB_ROOT) whose platform files are the real ones, with no .hub-home, .state or .logs"""
        hub = self.tmp / "fakehub"
        if not hub.exists():
            hub.mkdir()
            (hub / ".git").mkdir()
            for name in ("bin", "launchd", "hub.example.json", "projects", "modules", "contracts", "githooks", "capabilities",
                         "templates"):
                (hub / name).symlink_to(ROOT / name)
        return hub

    def agenthub(self, *argv, sc=None, stdin="", env=None, label=""):
        """bin/agenthub in-process under DRIVER: machine state (launchd, systemd, setup-python, PATH, hooksPath, the
        appliers, the bus) comes from the scenario `sc`, so the output depends only on the code"""
        hub = self.fake_hub()
        sc = {"bin": str(BIN), "fakehub": str(hub), **(sc or {})}
        # the language also for the runs with no hub.json (doctor without one, init making one from the template)
        e = {**self.env, "AGENT_HUB_ROOT": str(hub), "AGENT_HUB_PYTHON": sys.executable, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
             "AGENT_HUB_LANG_UI": LANGUAGE["ui"], **(env or {})}
        e = {k: v for k, v in e.items() if v is not None}
        r = subprocess.run([sys.executable, "-c", DRIVER, json.dumps(sc), *map(str, argv)], input=stdin, env=e,
                           cwd=self.tmp, capture_output=True, text=True, timeout=120)
        return f"$ agenthub {' '.join(map(str, argv))}{f'    # {label}' if label else ''}\n" \
               f"[exit {r.returncode}]\n--- stdout\n{r.stdout}--- stderr\n{r.stderr}"

    def heartbeat(self, minutes_ago: float | None):
        hb = self.tmp / "state" / "scheduler-heartbeat"
        if minutes_ago is None:
            hb.unlink(missing_ok=True)
            return
        hb.write_text("")
        t = time.time() - minutes_ago * 60
        os.utime(hb, (t, t))

    def test_agenthub_doctor(self):
        out = []
        hub_json = self.tmp / "config" / "hub.json"
        hub_json.unlink()
        out.append(self.agenthub("doctor", label="no hub.json", sc={"python": [1, "", "no venv\n"]}))
        # launchd; an invalid config; nothing loaded right; claude without a token; an API provider without its key
        self.valid_cfg(unknown_key=1)
        self.cfg["llm"] = {"providers": {"anthropic": {"impl": "anthropic", "key": {"secret": "anthropic-api-key"},
                                                       "models": {"fast": "m1", "standard": "m1", "strong": "m1"}}},
                           "use": {"standard": "anthropic"}}
        self.write_cfg()
        out.append(self.agenthub("doctor", label="launchd, problems", sc={
            "sm": "launchd", "launchd": {"local.agent-hub-scheduler-watchdog": "path = /elsewhere/bin/x"},
            "plan": {"services": ["a", "b"], "scheduler": ["c"]}, "hooks": ""}))
        # launchd; everything in order; an assistant; claude with its token
        del self.cfg["llm"], self.cfg["unknown_key"]
        self.valid_cfg(owner_side="assistant", assistant={"project": "helper", "api": "http://127.0.0.1:9", "name": "Robin"},
                       alerts={"discord_token_file": "a", "discord_channel_file": "b"})
        (self.tmp / "home" / ".claude").mkdir()
        (self.tmp / "home" / ".claude" / "headless-oauth-token").write_text("t\n")
        self.heartbeat(0)
        out.append(self.agenthub("doctor", label="launchd, all fine", sc={
            "sm": "launchd", "launchd": {"local.agent-hub-scheduler": "path = {bin}/scheduler.py",
                                         "local.agent-hub-scheduler-watchdog": "path = {bin}/scheduler-watchdog.py"},
            "plan": {}, "hooks": "githooks", "which": {"agenthub": "/usr/local/bin/agenthub"},
            "python": [0, "python 3.13 ok\n", ""]}))
        # systemd; no coding agent; an old heartbeat
        self.cfg.pop("assistant")
        self.cfg.pop("owner_side")
        self.valid_cfg(runtime={"default": "none"})
        self.heartbeat(120)
        out.append(self.agenthub("doctor", label="systemd, runtime none", sc={
            "sm": "systemd", "systemd": {"local.agent-hub-scheduler": "running", "local.agent-hub-scheduler-watchdog": None},
            "linger": False, "plan": {"services": ["a"]}, "hooks": "githooks", "adapter": "landlock",
            "no_sandbox": "the kernel's Landlock ABI is 3, ports need ≥ 4"}))
        self.valid_cfg(owner_side="assistant", assistant={"project": "helper", "api": "http://127.0.0.1:9"})
        out.append(self.agenthub("doctor", label="no service manager, unnamed assistant",
                                 sc={"sm": None, "plan": {}, "hooks": "githooks"}))
        self.check("agenthub-doctor", "\n".join(out))

    def test_agenthub_modules(self):
        self.valid_cfg(modules={"backup": True, "reflect": True, "mirror": False})
        out = [self.agenthub("modules"),
               self.agenthub("modules", "enable", "no-such-module"),
               self.agenthub("modules", "enable", "mirror", sc={"apply_rc": 0}),
               self.agenthub("modules", "disable", "backup", sc={"apply_rc": 1})]
        out.append("=== hub.json modules\n" + json.dumps(json.loads((self.tmp / "config" / "hub.json").read_text())["modules"])
                   + "\n")
        self.check("agenthub-modules", "\n".join(out))

    def test_agenthub_config(self):
        out = [self.agenthub("config", "apply", label="config invalid")]
        self.valid_cfg()
        out.append(self.agenthub("config", "plan", sc={"plan": {}}))
        out.append(self.agenthub("config", "plan", sc={"plan": {"services": ["install x", "reload y"]}}))
        out.append(self.agenthub("config", "apply", label="applied, one left, bus down", sc={
            "applied": {"services": ["install x"]}, "plan": {"services": ["reload y"]}}))
        out.append(self.agenthub("config", "apply", label="applied, nothing left", sc={
            "applied": {"services": ["install x"]}, "plan": {}, "bus_rc": 0}))
        out.append(self.agenthub("config", "check"))
        self.check("agenthub-config", "\n".join(out))

    def test_agenthub_llm(self):
        self.valid_cfg(runtime={"default": "none"})
        out = [self.agenthub("llm"),
               self.agenthub("llm", "set-key", "nosuch"),
               self.agenthub("llm", "setup", "--llm", "bogus", "--yes"),
               self.agenthub("llm", "setup", "--llm", "none", "--yes"),
               self.agenthub("llm", "setup", "--llm", "runtime", "--yes", label="runtime none"),
               self.agenthub("llm", "setup", "--llm", "openai", "--yes"),
               self.agenthub("llm", "setup", "--llm", "anthropic", "--llm-model", "m1", "--yes"),
               self.agenthub("llm", "set-key", "anthropic", stdin=""),
               self.agenthub("llm", "set-key", "anthropic", stdin="k-123\n")]
        self.cfg = json.loads((self.tmp / "config" / "hub.json").read_text())
        self.cfg["llm"]["providers"]["anthropic"]["key"] = {"env": "ANTHROPIC_KEY"}
        self.cfg["runtime"] = {"default": "claude"}
        self.write_cfg()
        out.append(self.agenthub("llm", "set-key", "anthropic", stdin="k\n", label="key from env"))
        out.append(self.agenthub("llm", "setup", "--llm", "runtime", "--yes", label="runtime claude"))
        self.check("agenthub-llm", "\n".join(out))

    def test_agenthub_init(self):
        self.valid_cfg()
        out = [self.agenthub("init", "--dry-run", "--yes", "--no-launchd", label="old layout", sc={"hooks": ""}),
               self.agenthub("init", "--dry-run", "--yes", label="instance, launchd",
                             env={"AGENT_HUB_HOME": str(self.tmp / "inst0")}, sc={"sm": "launchd", "hooks": "githooks"}),
               self.agenthub("init", "--dry-run", "--yes", label="instance, systemd",
                             env={"AGENT_HUB_HOME": str(self.tmp / "inst0")},
                             sc={"sm": "systemd", "systemd": {}, "linger": False, "hooks": "githooks"}),
               self.agenthub("init", "--dry-run", "--yes", label="no service manager", sc={"sm": None, "hooks": "githooks"})]
        fresh = {"AGENT_HUB_CONFIG": None, "AGENT_HUB_DATA": None, "AGENT_HUB_SECRETS": None}
        args = ("--owner", "Sam", "--timezone", "UTC", "--workspace", self.tmp / "ws2", "--modules", "backup,reflect",
                "--yes", "--llm", "none")
        out.append(self.agenthub("init", "--home", self.tmp / "inst", *args, "--dry-run", label="new instance",
                                 env=fresh, sc={"hooks": "githooks"}))
        out.append(self.agenthub("init", "--home", self.tmp / "inst", *args, "--no-launchd", label="new instance",
                                 env=fresh, sc={"hooks": ""}))
        # the template's JSON is not a message: keep the line that announces it, not the template itself
        text = re.sub(r"(?m)^\{\n(.*\n)*?\}\n", "<hub.json from hub.example.json>\n", "\n".join(out))
        self.check("agenthub-init", text)

    def test_agenthub_move_instance(self):
        out = [self.agenthub("move-instance")]
        (self.tmp / "data" / "todo.md").write_text("x\n")
        (self.tmp / "secrets" / "k").write_text("x\n")
        out.append(self.agenthub("move-instance", "--to", self.tmp / "x", env={"AGENT_HUB_HOME": str(self.tmp / "inst")}))
        (self.tmp / "full").mkdir()
        (self.tmp / "full" / "f").write_text("")
        out.append(self.agenthub("move-instance", "--to", self.tmp / "full"))
        out.append(self.agenthub("move-instance", "--to", self.tmp / "new"))
        out.append(self.agenthub("move-instance", "--to", self.tmp / "new", "--apply", label="config invalid"))
        self.valid_cfg()
        out.append(self.agenthub("move-instance", "--to", self.tmp / "new2", "--apply"))
        self.check("agenthub-move-instance", "\n".join(out))

    def test_agenthub_install_cli(self):
        d = self.tmp / "cli"
        out = [self.agenthub("install-cli", "--dir", d), self.agenthub("install-cli", "--dir", d),
               self.agenthub("install-cli", "--dir", d, env={"PATH": f"{d}:/usr/bin:/bin"})]
        self.check("agenthub-install-cli", "\n".join(out))

    # --- config-check: every file it validates ------------------------------------------------------------------
    def test_config_check_files(self):
        cc = BIN / "config-check"
        out = [self.run_(cc, "--bogus")]
        self.valid_cfg()
        out.append(self.run_(cc))
        cfg = self.tmp / "config"
        self.cfg["assitant"] = {}
        self.cfg["llm"] = {"providers": {"x": {"impl": "nope"}}}
        self.write_cfg()
        (cfg / "models.env").write_text("A=b\n")
        (cfg / "mirror-list.txt").write_text("# c\ngit@github.com:a/b.git\nnot a remote\n")
        (cfg / "services.tsv").write_text("label\towner\nx\ty\nx\ty\nz\n")
        (cfg / "schedule.yaml").write_text("jobs:\n  - name: a\n  - name: a\n  - name: b\n")
        out.append(self.run_(cc))
        for f in ("models.env", "mirror-list.txt", "services.tsv"):
            (cfg / f).unlink()
        (cfg / "schedule.yaml").write_text("jobs: [\n")
        out.append(self.run_(cc))
        bad = self.tmp / "bad-config"      # what --staged hands its child: a config dir whose hub.json does not parse
        bad.mkdir()
        (bad / "hub.json").write_text("{")
        self.env["AGENT_HUB_CONFIG"] = str(bad)
        self.env["AGENT_HUB_LANG_UI"] = LANGUAGE["ui"]   # that hub.json cannot say which language it is
        out.append(self.run_(cc, "--child"))
        self.check("config-check-files", "\n".join(out))


# Runs bin/agenthub in-process with the machine replaced by a scenario (argv[1], JSON). Nothing here reaches launchd,
# systemd or the real bus: those calls are answered from the scenario.
DRIVER = r'''
import importlib.machinery, importlib.util, json, pathlib, shutil, subprocess, sys
sc = json.loads(sys.argv[1])
BIN = pathlib.Path(sc["bin"])
sys.path.insert(0, str(BIN))
import hubconf
if "sm" in sc:
    hubconf._SERVICE_MANAGER[:] = [sc["sm"]]
import isolation   # the sandbox adapter: the scenario's, not this machine's
isolation.adapter = lambda: (sc.get("adapter", "seatbelt"), None)
isolation.can_sandbox = lambda: sc.get("no_sandbox")
isolation.guarantees = lambda: [] if sc.get("no_sandbox") else list(isolation.REQUIRED)   # not this kernel's
real_run = subprocess.run

def done(cmd, rc=0, out="", err=""):
    return subprocess.CompletedProcess(cmd, rc, out, err)

def run(cmd, *a, **k):
    c = [str(x) for x in cmd]
    if c[0].endswith("setup-python"):
        return done(c, *sc.get("python", [0, "python ok\n", ""]))
    if c[0] in ("/bin/launchctl", "launchctl", "systemctl"):
        if c[1] == "print":
            st = sc.get("launchd", {}).get(c[2].rsplit("/", 1)[1])
            return done(c, 0 if st else 1, st.replace("{bin}", str(BIN)) if st else "")
        return done(c, 0)
    if c[:3] == ["git", "-C", sc["fakehub"]] and c[3:5] == ["config", "core.hooksPath"]:
        if len(c) == 5:
            return done(c, 0, sc.get("hooks", "") + "\n")
        print("[git config core.hooksPath " + c[5] + "]")
        return done(c, 0)
    if c[0] == "gh":
        return done(c, 1)
    if len(c) >= 3 and c[-3].endswith("agenthub") and c[-2:] == ["config", "apply"]:
        print("[agenthub config apply]")
        return done(c, sc.get("apply_rc", 0))
    if any(x.endswith("hubbus") for x in c) and "publish" in c:
        return done(c, sc.get("bus_rc", 1), "", "bus is down\n")
    return real_run(cmd, *a, **k)

subprocess.run = run
shutil.which = lambda name, *a, **k: sc.get("which", {}).get(name)
if sc.get("sm") == "systemd":
    import systemd_units
    systemd_units.state = lambda label: sc.get("systemd", {}).get(label)
    systemd_units.linger = lambda: sc.get("linger")
    systemd_units.installed = lambda label: None
    systemd_units.install = lambda plist: done([], 1, "", "no user bus\n")

class Change:
    def __init__(self, text):
        self.op, self.target, self.detail, self.text = "change", text, "", text
    def line(self):
        return self.text

import appliers
if "plan" in sc:
    appliers.plan_all = lambda only=None: {k: [Change(x) for x in v] for k, v in sc["plan"].items()}
if "applied" in sc:
    appliers.apply_all = lambda only=None: {k: [Change(x) for x in v] for k, v in sc["applied"].items()}
    appliers.config_commit = lambda: "c0ffee1"
    appliers.applied_path = lambda: hubconf.STATE / "config-applied.json"
loader = importlib.machinery.SourceFileLoader("agenthub", str(BIN / "agenthub"))
mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("agenthub", loader))
loader.exec_module(mod)
sys.exit(mod.main(sys.argv[2:]))
'''


if __name__ == "__main__":
    unittest.main()
