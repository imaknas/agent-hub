"""E1 goldens (docs/design/i18n.md) for the platform's shell scripts: what they print or log, on zh-Hant.

Captured from the scripts before their messages moved to hub_msg / hub_say (bin/hubpaths.sh). Each case runs the real
script in a temp instance (temp config, data, HOME); helpers it calls come from a temp hub's bin (links to the real
files, plus fakes for the model and for slow or remote steps), or from fake commands put first on PATH. Temp paths,
times, shas and bash's own `line N:` are normalized. One fixture per script: tests/fixtures/i18n-golden/shell-<name>.txt.

    bin/py -m unittest tests.test_i18n_golden_shell
    I18N_GOLDEN_UPDATE=1 bin/py -m unittest tests.test_i18n_golden_shell    # rewrite the goldens (only on purpose)
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_lang  # noqa: E402

GOLDEN = golden_lang.GOLDEN
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
PY = os.environ.get("AGENT_HUB_PYTHON") or sys.executable


def utf8_locale() -> str:
    """A UTF-8 locale this machine has (macOS: en_US.UTF-8; Debian/Ubuntu often only C.UTF-8): an LC_ALL it lacks makes
    bash print a setlocale warning into every script's stderr."""
    have = subprocess.run(["locale", "-a"], capture_output=True, text=True).stdout.split()
    norm = {x.lower().replace("-", ""): x for x in have}
    return next((norm[k] for k in ("en_us.utf8", "c.utf8") if k in norm), "C")


LOCALE = utf8_locale()


class ShellGolden(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for d in ("home", "config/projects", "data", "secrets", "state", "copies", "ws", "fakebin"):
            (self.tmp / d).mkdir(parents=True)
        self.cfg = {"owner": {"name": "alex", "tag": "Alex"}, "registry_dir": "projects",
                    "memory_copies": str(self.tmp / "copies"), "workspace_root": str(self.tmp / "ws"),
                    "git_origin": str(self.tmp / "origin"), "language": {"ui": golden_lang.LANG, "content": golden_lang.LANG}}
        self.write_cfg()
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("AGENT_HUB_", "GIT_"))},
                    "AGENT_HUB_PYTHON": PY, "HOME": str(self.tmp / "home"), "AGENT_HUB_CONFIG": str(self.tmp / "config"),
                    "AGENT_HUB_DATA": str(self.tmp / "data"), "AGENT_HUB_SECRETS": str(self.tmp / "secrets"),
                    "AGENT_HUB_STATE": str(self.tmp / "state"), "AGENT_HUB_LANG_UI": golden_lang.LANG,
                    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t", "LANG": LOCALE, "LC_ALL": LOCALE, "TZ": "Asia/Taipei",
                    "PATH": f"{self.tmp / 'fakebin'}:{os.environ.get('PATH', '/usr/bin:/bin')}"}

    # --- helpers ------------------------------------------------------------------------------------------------
    def write_cfg(self):
        (self.tmp / "config" / "hub.json").write_text(json.dumps(self.cfg))

    def git(self, cwd, *args):
        subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True, capture_output=True)

    def repo(self, path, files=None) -> pathlib.Path:
        path = pathlib.Path(path)
        path.mkdir(parents=True, exist_ok=True)
        self.git(path, "init", "-q", "-b", "main")
        for name, text in (files or {"README": "x\n"}).items():
            (path / name).parent.mkdir(parents=True, exist_ok=True)
            (path / name).write_text(text)
        self.git(path, "add", "-A")
        self.git(path, "commit", "-q", "-m", "init")
        return path

    def bare_remote(self, repo, name):
        bare = self.tmp / "remotes" / f"{name}.git"
        bare.parent.mkdir(exist_ok=True)
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", str(bare))
        self.git(repo, "remote", "add", "origin", str(bare))
        self.git(repo, "push", "-q", "-u", "origin", "main")
        return bare

    def fake(self, path, body):
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        path.chmod(0o755)

    def fake_cmd(self, name, body):
        self.fake(self.tmp / "fakebin" / name, "#!/bin/bash\n" + body)

    def hub(self, name, links=(), fakes=None) -> pathlib.Path:
        """a temp hub whose bin has links to the real scripts named in links and the given fakes"""
        h = self.tmp / name
        (h / "bin").mkdir(parents=True)
        for f in ("py", "hubpaths.sh", "i18n.py", "hubconf.py", *links):
            (h / "bin" / f).symlink_to(BIN / f)
        for f, body in (fakes or {}).items():
            self.fake(h / "bin" / f, body)
        return h

    def register(self, name, extra=""):
        ws = self.tmp / "ws" / name
        ws.mkdir(parents=True, exist_ok=True)
        (self.tmp / "config" / "projects" / f"{name}.yaml").write_text(
            f"v: 1\nname: {name}\npaths:\n  workspace: {ws}\n  origin: (native)\n{extra}")
        subprocess.run([PY, str(BIN / "registry.py"), "check"], env=self.env, capture_output=True, timeout=60)
        return ws

    def run_(self, *argv, cwd=None, stdin="", label=None, **env):
        r = subprocess.run([str(a) for a in argv], input=stdin, env={**self.env, **env}, cwd=cwd or self.tmp,
                           capture_output=True, text=True, timeout=180)
        shown = label or " ".join(pathlib.Path(str(argv[0])).name if i == 0 else str(a) for i, a in enumerate(argv))
        return f"$ {shown}\n[exit {r.returncode}]\n--- stdout\n{r.stdout}--- stderr\n{r.stderr}"

    def file(self, path) -> str:
        path = pathlib.Path(path)
        return f"--- file {path.name}\n{path.read_text() if path.exists() else '(missing)'}"

    def normalize(self, text: str) -> str:
        for real, tag in ((str(self.tmp), "<TMP>"), (str(ROOT), "<HUB>"), (PY, "<PY>"), (sys.executable, "<PY>")):
            text = text.replace(real, tag)
        text = re.sub(r"(/private)?/(var|tmp)/[\w/.-]*?<TMP>", "<TMP>", text)
        text = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?([+-]\d{2}:?\d{2}|Z)?", "<TS>", text)
        text = re.sub(r"\b\d{4}-\d{2}-\d{2}(-\d{4})?\b", "<DATE>", text)
        text = re.sub(r"\[\d{2}:\d{2}:\d{2}\]", "[<T>]", text)
        text = re.sub(r"\bline \d+:", "line <N>:", text)
        text = re.sub(r"\b[0-9a-f]{7,40}\b", "<SHA>", text)
        text = re.sub(r"\bin \d+\.\d+s\b", "in <S>s", text)
        text = re.sub(r"\brun\.\w{6}\b", "run.<X>", text)
        return text

    def check(self, name: str, parts: list[str]):
        text = self.normalize("\n".join(parts))
        f = GOLDEN / f"shell-{name}.txt"
        if UPDATE or not f.exists():
            GOLDEN.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text(), f"output differs from {f.relative_to(ROOT)}")

    # --- cases --------------------------------------------------------------------------------------------------
    def test_sync_local_setup(self):
        s, out = BIN / "sync-local-setup.sh", []
        plain = self.tmp / "ws" / "plain"
        plain.mkdir(parents=True)
        out.append(self.run_(s, plain))
        # moved away (migrate-to-mini.py's marker): printed even with --quiet
        moved = self.repo(self.tmp / "ws" / "moved")
        (moved / ".agent-memory").mkdir()
        (moved / ".git" / "agent-hub-migrated-to-mini").write_text("mini:~/workspace/moved\t2026-10-15\n")
        self.cfg["hosts"] = {"server": "hubhost"}
        self.write_cfg()
        out.append(self.run_(s, "--quiet", moved))
        # leaks, a tracked local file, an unknown rule, AGENT.md that cannot be pushed
        p = self.repo(self.tmp / "ws" / "proj", {"README": "see ~/agent-hub/bin\n", "CLAUDE.local.md": "mine\n"})
        self.repo(p / ".agent-memory", {"AGENT.md": "old\n", "RULES": "engineering no-such-rule\n"})
        out.append(self.run_(s, p))
        # a clean project: AGENT.md pushed, then nothing to say with --quiet
        q = self.repo(self.tmp / "ws" / "clean")
        self.bare_remote(self.repo(q / ".agent-memory", {"AGENT.md": "old\n"}), "clean-memory")
        out.append(self.run_(s, q))
        out.append(self.run_(s, "--quiet", q))
        # a runtime without a rules file or session events
        self.cfg["runtime"] = {"default": "none"}
        self.write_cfg()
        out.append(self.run_(s, q))
        self.check("sync-local-setup", out)

    def test_gen_index_roster(self):
        s, out = BIN / "gen-index-roster.sh", []
        for name, extra in (("beta", ""), ("alpha", ""), ("idea-1", "type: idea\npurpose: x\n")):
            self.register(name, extra)
        (self.tmp / "data" / "index.md").write_text("# index\n\n## other\n")
        out.append(self.run_(s))
        out.append(self.file(self.tmp / "data" / "index.md"))
        out.append(self.run_(s))
        (self.tmp / "data" / "index.md").write_text("# index\n")
        out.append(self.run_(s))
        out.append(self.file(self.tmp / "data" / "index.md"))
        (self.tmp / "empty").mkdir()
        (self.tmp / "empty" / "hub.json").write_text(json.dumps({"registry_dir": "projects"}))
        (self.tmp / "empty" / "projects").mkdir()
        out.append(self.run_(s, AGENT_HUB_CONFIG=str(self.tmp / "empty")))
        self.check("gen-index-roster", out)

    def test_run_agent_job(self):
        s, out = BIN / "run-agent-job.sh", []
        llm = ("import os, sys\nmode = os.environ.get('FAKE_LLM', 'ok')\n"
               "if mode == 'ok':\n"
               "    print('{\"result\": \"\\\\ud83d\\\\udd14 needs a look\\\\nsecond line\", \"session_id\": \"s-1\", '\n"
               "          '\"num_turns\": 3, \"duration_ms\": 90000}')\n"
               "else:\n    print('not json')\n    sys.exit(1)\n")
        h = self.hub("hub", fakes={"llm.py": llm, "gen-status-page.py": "pass\n"})
        (h / "agent-jobs").mkdir()
        (h / "agent-jobs" / "job-a.md").write_text("do it\n")
        out.append(self.run_(s, AGENT_HUB_ROOT=str(h)))
        out.append(self.run_(s, "no-prompt", AGENT_HUB_ROOT=str(h)))
        stopped = h / ".state" / "agent-jobs" / "job-a"
        stopped.mkdir(parents=True)
        (stopped / "STOP").write_text("condition met\n")
        out.append(self.run_(s, "job-a", AGENT_HUB_ROOT=str(h)))
        (stopped / "STOP").unlink()
        out.append(self.run_(s, "job-a", self.tmp / "nowhere", AGENT_HUB_ROOT=str(h)))
        out.append(f"error={json.loads((stopped / 'status.json').read_text())['error']}")
        ws = self.tmp / "ws" / "job"
        ws.mkdir(parents=True)
        out.append(self.run_(s, "job-a", ws, AGENT_HUB_ROOT=str(h), RUN_AGENT_JOB_FOREGROUND="1"))
        st = json.loads((stopped / "status.json").read_text())
        out.append("\n".join(f"{k}={st.get(k)}" for k in ("state", "notify", "summary", "error")))
        out += [self.file(r) for r in sorted(stopped.glob("20*.md"))]
        out.append(self.run_(s, "job-a", ws, AGENT_HUB_ROOT=str(h), RUN_AGENT_JOB_FOREGROUND="1", FAKE_LLM="bad"))
        st = json.loads((stopped / "status.json").read_text())
        out.append("\n".join(f"{k}={st.get(k)}" for k in ("state", "error")))
        # in the background: the start message, then wait for the detached run to finish
        (stopped / "status.json").unlink()
        out.append(self.run_(s, "job-a", ws, AGENT_HUB_ROOT=str(h)))
        for _ in range(100):
            if (stopped / "status.json").exists() and json.loads((stopped / "status.json").read_text())["state"] != "running":
                break
            time.sleep(0.1)
        out.append(f"state={json.loads((stopped / 'status.json').read_text())['state']}")
        self.check("run-agent-job", out)

    def test_worktree_memory(self):
        s, out = BIN / "worktree-memory.sh", []
        main = self.repo(self.tmp / "ws" / "wt-main")
        (main / ".agent-memory").mkdir()
        (main / "CLAUDE.local.md").write_text("project rules\n")
        out.append(self.run_(s, "start", cwd=main, label="worktree-memory.sh start (main checkout)"))
        wt = self.tmp / "ws" / "wt-linked"
        self.git(main, "worktree", "add", "-q", "-b", "side", str(wt))
        gd = main / ".git" / "worktrees" / "wt-linked"
        (gd / "agent-hub-deps-pruned").write_text("npm install\n")
        out.append(self.run_(s, "start", cwd=wt, label="worktree-memory.sh start (linked worktree)"))
        self.check("worktree-memory", out)

    def test_overview(self):
        s, out = BIN / "overview.sh", []
        a = self.register("alpha")
        (a / ".agent-memory").mkdir()
        (a / ".agent-memory" / "STATE.md").write_text("# alpha state\n")
        self.register("beta")
        self.register("gamma")
        (self.tmp / "copies" / "gamma").mkdir()
        (self.tmp / "copies" / "gamma" / "STATE.md").write_text("# gamma copy\n")
        (self.tmp / "config" / "services.tsv").write_text("local.alpha-web\talpha\tagent-hub\tx.plist\tweb\t-\n")
        self.fake_cmd("launchctl", "exit 0\n")
        out.append(self.run_(s))
        out.append(self.run_(s, "beta"))
        self.check("overview", out)

    def test_commit_hub_files(self):
        s, out = BIN / "commit-hub-files.sh", []
        h = self.repo(self.tmp / "hub")
        (h / "bin").symlink_to(BIN)
        env = {"AGENT_HUB": str(h)}
        out.append(self.run_(s, "only-message", **env))
        out.append(self.run_(s, "m", "hub.json", "todo.md", **env))
        self.repo(self.tmp / "config", {"hub.json": (self.tmp / "config" / "hub.json").read_text()})
        (self.tmp / "config" / "hub.json").write_text((self.tmp / "config" / "hub.json").read_text() + "\n")
        out.append(self.run_(s, "m", "hub.json", "todo.md", **env))
        out.append(self.run_(s, "m", "hub.json", **env))
        # the old layout: config is the hub itself
        out.append(self.run_(s, "m", "nope.txt", "nope2.txt", AGENT_HUB_CONFIG=str(h), **env))
        self.check("commit-hub-files", out)

    def test_sync_all_memory(self):
        s, out = BIN / "sync-all-memory.sh", []
        h = self.hub("hub", links=("with-lock.sh", "git-sync-memory.sh", "git-safe-push.sh", "project_git.py",
                                   "isolation.py", "project-git-job.py"))
        log = h / ".logs"
        a = self.register("alpha")
        self.repo(a)
        mem = self.repo(a / ".agent-memory", {"STATE.md": "s\n"})
        self.bare_remote(mem, "alpha-memory")
        (mem / "memory").mkdir()
        (mem / "memory" / "note.md").write_text("n\n")
        b = self.register("beta")
        self.repo(b)
        bmem = self.repo(b / ".agent-memory", {"STATE.md": "s\n"})
        bare = self.bare_remote(bmem, "beta-memory")
        (bmem / "STATE.md").write_text("s2\n")
        self.git(bmem, "commit", "-q", "-am", "local")       # committed earlier, not pushed yet
        c = self.register("gamma")
        self.repo(c)
        cmem = self.repo(c / ".agent-memory", {"STATE.md": "s\n"})
        (cmem / "STATE.md").write_text("s2\n")
        self.git(cmem, "add", "STATE.md")
        (cmem / ".git" / "index.lock").write_text("")        # another git holds the index: the sync fails

        def logged():
            """the script's own log lines (git's output in between is not ours)"""
            lines = [l for f in sorted(log.glob("*.log")) for l in f.read_text().splitlines(keepends=True)
                     if re.match(r"\[\d{2}:\d{2}:\d{2}\] ", l)]
            for f in log.glob("*.log"):
                f.unlink()
            return "--- log\n" + "".join(lines)
        out.append(self.run_(s, AGENT_HUB_ROOT=str(h)))
        out.append(logged())
        # the push target is gone: the catch-up push fails
        (bmem / "STATE.md").write_text("s3\n")
        self.git(bmem, "commit", "-q", "-am", "local 2")
        shutil.rmtree(bare)
        out.append(self.run_(s, AGENT_HUB_ROOT=str(h)))
        out.append(logged())
        # the hub machine cannot be reached: commit only
        self.cfg["hosts"] = {"server": "hubhost"}
        self.write_cfg()
        self.fake_cmd("ssh", "exit 255\n")
        (mem / "memory" / "note2.md").write_text("n\n")
        out.append(self.run_(s, AGENT_HUB_ROOT=str(h)))
        out.append(logged())
        self.check("sync-all-memory", out)

    def test_mirror_backup(self):
        s, out = BIN / "mirror-backup.sh", []
        h = self.hub("hub")
        out.append(self.run_(s, AGENT_HUB_ROOT=str(h)))
        src = self.repo(self.tmp / "src" / "proj")
        (self.tmp / "config" / "mirror-list.txt").write_text(f"# comment\n\n{src}\n{self.tmp / 'src' / 'missing'}\n")
        out.append(self.run_(s, AGENT_HUB_ROOT=str(h)))
        out.append(self.run_(s, AGENT_HUB_ROOT=str(h)))
        self.git(self.tmp, "--git-dir", str(self.tmp / "origin" / "mirrors" / "proj.git"), "remote", "set-url", "origin",
                 str(self.tmp / "src" / "gone"))
        out.append(self.run_(s, AGENT_HUB_ROOT=str(h)))
        self.check("mirror-backup", out)

    @unittest.skipUnless((pathlib.Path(PY).parent / "ruff").exists(), "no ruff next to the platform's Python")
    def test_test_gate(self):
        out = []
        h = self.repo(self.tmp / "gate", {"README": "x\n"})
        (h / "bin").mkdir()
        for f in ("test-gate", "run-tests", "lint", "py", "hubpaths.sh", "i18n.py", "hubconf.py"):
            (h / "bin" / f).symlink_to(BIN / f)
        (h / "ruff.toml").write_text((ROOT / "ruff.toml").read_text())
        (h / "tests").mkdir()
        g = h / "bin" / "test-gate"
        self.git(h, "add", "-A")
        self.git(h, "commit", "-q", "-m", "bin")
        (h / "notes.md").write_text("n\n")
        self.git(h, "add", "notes.md")
        out.append(self.run_(g, cwd=h, label="test-gate (data only)"))
        (h / "tests" / "test_ok.py").write_text("import unittest\n\n\nclass T(unittest.TestCase):\n"
                                                "    def test_ok(self):\n        pass\n")
        self.git(h, "add", "tests")
        out.append(self.run_(g, cwd=h, label="test-gate (passes)"))
        (h / "tests" / "test_bad.py").write_text(
            "import os\nimport pathlib\nimport unittest\n\n\nclass T(unittest.TestCase):\n"
            "    def test_writes_home(self):\n        (pathlib.Path.home() / 'leak.txt').write_text('x')\n\n"
            "    def test_fails(self):\n        self.fail('boom')\n")
        self.git(h, "add", "tests")
        r = self.run_(g, cwd=h, label="test-gate (fails)")
        # unittest's own tail (tracebacks) is not the gate's text: keep the gate's lines and the summary
        keep = [l for l in r.splitlines(keepends=True)
                if l.startswith(("$", "[exit", "---", "test-gate", "~/", "tests/", "Found", "FAIL:", "ERROR:"))]
        out.append("".join(keep))
        self.check("test-gate", out)

    def test_small_scripts(self):
        """librarian-publish, openclaw-session-backup, git-safe-push, with-lock, weekly-reflect, lease-resume, lint"""
        out = []
        ok, bad = "#!/bin/bash\nexit 0\n", "#!/bin/bash\nexit 1\n"
        # librarian-publish: the roster and the commit step are faked (each has its own case)
        for name, roster, commit in (("pub-ok", ok, ok), ("pub-bad", bad, bad)):
            h = self.hub(name, fakes={"gen-index-roster.sh": roster, "commit-hub-files.sh": commit})
            self.repo(h, {"README": "x\n"})
            self.bare_remote(h, name)
            out.append(self.run_(BIN / "librarian-publish.sh", AGENT_HUB_ROOT=str(h), label=f"librarian-publish.sh ({name})"))
        # openclaw-session-backup: rsync faked
        for code in (0, 1):
            self.fake_cmd("rsync", f"exit {code}\n")
            out.append(self.run_(BIN / "openclaw-session-backup.sh", label=f"openclaw-session-backup.sh (rsync exit {code})"))
        # git-safe-push: git faked
        self.fake_cmd("git", 'case "$1:$FAKE_GIT" in\n'
                             '  push:protected) echo "remote: error: GH006: Protected branch update failed" >&2; exit 1 ;;\n'
                             '  push:*) echo " ! [rejected] main -> main (fetch first)" >&2; exit 1 ;;\n'
                             '  pull:conflict) echo "CONFLICT (content): Merge conflict in a.txt" >&2; exit 1 ;;\n'
                             '  pull:*) exit 0 ;;\nesac\n')
        for mode in ("protected", "conflict", "diverged"):
            out.append(self.run_(BIN / "git-safe-push.sh", FAKE_GIT=mode, label=f"git-safe-push.sh ({mode})"))
        (self.tmp / "fakebin" / "git").unlink()
        # with-lock
        out.append(self.run_(BIN / "with-lock.sh"))
        out.append(self.run_(BIN / "with-lock.sh", "true", cwd=self.tmp / "home"))
        r = self.repo(self.tmp / "ws" / "locked")
        out.append(self.run_(BIN / "with-lock.sh", "echo", "inside", cwd=r))
        # weekly-reflect: the analysis and the commit faked
        wr = ("import os, sys\nif os.environ.get('FAKE_REFLECT') == 'bad':\n    sys.exit(1)\n"
              "print('reflect/2026-W44.json')\nprint('reflect/index.html')\n")
        h = self.hub("reflect-hub", fakes={"weekly-reflect.py": wr, "commit-hub-files.sh": '#!/bin/bash\necho "commit $*"\n'})
        self.repo(h, {"README": "x\n"})
        self.bare_remote(h, "reflect-hub")
        out.append(self.run_(BIN / "weekly-reflect.sh", AGENT_HUB_ROOT=str(h)))
        out.append(self.run_(BIN / "weekly-reflect.sh", AGENT_HUB_ROOT=str(h), FAKE_REFLECT="bad",
                             label="weekly-reflect.sh (fails)"))
        # lease-resume: the usage line
        out.append(self.run_(BIN / "lease-resume"))
        # lint without ruff: a platform Python with no ruff next to it
        (self.tmp / "nopy" / "bin").mkdir(parents=True)
        (self.tmp / "nopy" / "bin" / "python3").symlink_to(PY)
        out.append(self.run_(BIN / "lint", AGENT_HUB_PYTHON=str(self.tmp / "nopy" / "bin" / "python3")))
        self.check("small-scripts", out)


if __name__ == "__main__":
    unittest.main()
