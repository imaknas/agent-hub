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
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
GOLDEN = ROOT / "tests" / "fixtures" / "i18n-golden"
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
# The instance language the goldens are for: zh-Hant must stay byte-identical to the output before translation
LANGUAGE = {"ui": "zh-Hant", "content": "zh-Hant"}

# Chinese inputs live in the fixture (tests/fixtures may hold Chinese; this file may not)
IN = json.loads((GOLDEN / "inputs.json").read_text())


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
             mock.patch.object(alert, "send", side_effect=lambda t: sent.append(t) or True):
            alert.once("k", [IN["problem"]], IN["recovered"])
            data = json.loads((self.tmp / "state" / "alerts-sent.json").read_text())
            data["k"]["at"] = "2026-10-01T00:00:00"
            (self.tmp / "state" / "alerts-sent.json").write_text(json.dumps(data))
            alert.once("k", [IN["problem"]], IN["recovered"])
            alert.once("k", [], IN["recovered"])
        self.check("alerts", "\n---\n".join(sent) + "\n")


if __name__ == "__main__":
    unittest.main()
