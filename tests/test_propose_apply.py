"""The agent proposes, the platform applies (bin/propose_apply.py), plus the command-template runtime
(runtime_command): any CLI agent can handle mailbox requests.

Isolated config / bus / project repo (with an .agent-memory worktree); the agent is a fake CLI: it writes a memory note
(allowed), changes README (not allowed) and finally returns JSON. Checks: only the note is taken, README is untouched,
the worktree is cleaned up, replies and closures are sent by the platform on its behalf.

    bin/py -m unittest discover -s tests
"""
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))

AGENT = r'''
import json, pathlib, re, sys
prompt = open(sys.argv[1]).read()
mid = re.search(r"- id ([^\s,]+),", prompt).group(1)
day = re.search(r"memory/(\d{4}-\d{2}-\d{2})\.md", prompt).group(1)
note = pathlib.Path(".agent-memory/memory") / f"{day}.md"
note.parent.mkdir(parents=True, exist_ok=True)
note.write_text("- answered other's question: use SQLite (décidé)\n")
pathlib.Path("README.md").write_text("the agent must not change this\n")
# sends and closes itself: a plain send, then with the env vars cleared (hubbus must block both; the platform sends
# from the output instead)
import os, subprocess
tries = []
for env in (dict(os.environ), {k: v for k, v in os.environ.items() if k != "HUB_MAIL_BY_PLATFORM"}):
    r = subprocess.run([sys.executable, "@HUBBUS@", "send", "--from", "demo", "--to", "other", "--type", "info",
                        "--text", "sent by the agent itself"], env=env, capture_output=True, text=True)
    tries.append(r.returncode)
r = subprocess.run([sys.executable, "@HUBBUS@", "done", mid], capture_output=True, text=True)
tries.append(r.returncode)
pathlib.Path(os.environ["PROPOSE_TEST_TRIES"]).write_text(json.dumps(tries))
print(json.dumps({"replies": [{"reply_to": mid, "type": "done", "text": "use SQLite (décidé)"},
                              {"reply_to": "not-this-one", "type": "info", "text": "must not be sent"}],
                  "done": [{"id": mid, "note": "answered"}, {"id": "not-this-one"}],
                  "ask_owner": [], "summary": f"{mid}: answered"}, ensure_ascii=False))
'''


# a stand-in agent that asks what a runtime that runs only in the throwaway copy asks, from inside the copy the platform really made
ASKS_ACP = r'''
import json, os, pathlib, re, sys
sys.path.insert(0, "@BIN@")
import runtime_acp, runtime_ports
here = os.getcwd()
cwd, why = runtime_acp._in_copy(runtime_ports.Task(point="mailbox-propose", prompt="", spec={}, cwd=here, env=dict(os.environ)))
pathlib.Path(os.environ["PROPOSE_TEST_TRIES"]).write_text(json.dumps({"here": here, "copy": cwd, "why": why}))
mid = re.search(r"- id ([^\s,]+),", open(sys.argv[1]).read()).group(1)
print(json.dumps({"replies": [], "done": [{"id": mid, "note": "seen"}], "ask_owner": [], "summary": "seen"}))
'''


def sh(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class ProposeApply(unittest.TestCase):
    def setUp(self):
        t = self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        for d in ("home", "config/projects", "data", "secrets", "bus"):
            (t / d).mkdir(parents=True)
        # project repo + memory bare repo + .agent-memory worktree
        ws = self.ws = t / "ws" / "demo"
        ws.mkdir(parents=True)
        sh("git", "init", "-q", "-b", "main", cwd=ws)
        (ws / "README.md").write_text("the original README\n")
        (ws / ".git" / "info" / "exclude").write_text(".agent-memory/\n")
        sh("git", "add", "README.md", cwd=ws)
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init", cwd=ws)
        bare = t / "bare.git"
        sh("git", "init", "-q", "--bare", str(bare))
        seed = t / "seed"
        sh("git", "init", "-q", "-b", "agent-memory", str(seed))
        (seed / "memory").mkdir()
        (seed / "memory" / "README.md").write_text("notes\n")
        (seed / "STATE.md").write_text("# STATE\n")
        sh("git", "add", "-A", cwd=seed)
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed", cwd=seed)
        sh("git", "push", "-q", str(bare), "agent-memory", cwd=seed)
        sh("git", "clone", "-q", "-b", "agent-memory", str(bare), str(ws / ".agent-memory"))
        script = t / "agent.py"
        script.write_text(AGENT.replace("@HUBBUS@", str(BIN / "hubbus")))
        cfg = {"owner": {"name": "alex", "tag": "Alex"}, "owner_side": "builtin", "bus_dir": str(t / "bus"),
               "registry_dir": "projects", "workspace_root": str(t / "ws"),
               "runtime": {"default": "command", "command": {"run": [sys.executable, str(script), "{prompt_file}"]}},
               "mailbox": {"handler": "propose"}}
        (t / "config" / "hub.json").write_text(json.dumps(cfg))
        doc = {"v": 1, "name": "demo", "paths": {"workspace": str(ws), "memory_bare": str(bare), "origin": None},
               "memory": True, "privacy": {"reflection": "include"}, "push_on_merge": False}
        (t / "config" / "projects" / "demo.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True))
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("AGENT_HUB_", "BRUNNA_")) or k in ("AGENT_HUB_PYTHON", "BRUNNA_PYTHON")},
                    "HOME": str(t / "home"), "AGENT_HUB_CONFIG": str(t / "config"),
                    "AGENT_HUB_DATA": str(t / "data"), "AGENT_HUB_SECRETS": str(t / "secrets"),
                    "AGENT_HUB_STATE": str(t / "state"), "PROPOSE_TEST_TRIES": str(t / "tries.json"),
                    # bin/py under the fake HOME (git-sync-memory.sh asks project_git.py whose memory this is)
                    "AGENT_HUB_PYTHON": os.environ.get("AGENT_HUB_PYTHON", sys.executable),
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                    # none of the machine's git config: Homebrew's git reads /opt/homebrew/etc/gitconfig, which
                    # defines filter.lfs where git-lfs is installed, and then the `*.md filter=lfs` a test plants turns
                    # an untouched README.md into a change (11-01, GitHub's macOS runner)
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}

    def run_(self, script, *args, stdin=""):
        return subprocess.run([sys.executable, str(BIN / script), *args], input=stdin, env=self.env, cwd=self.tmp,
                              capture_output=True, text=True, timeout=300)

    def test_any_cli_agent_handles_a_letter(self):
        r = self.run_("hubbus", "send", "--from", "agent-hub", "--to", "demo", "--type", "question", "--text", "which cache should we use?")
        self.assertEqual(r.returncode, 0, r.stderr)
        mid = r.stdout.strip().split()[-1]
        r = self.run_("propose_apply.py", "mailbox", "demo", mid)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        notes = [p for p in out["applied"] if p.startswith(".agent-memory/memory/")]
        self.assertEqual(len(notes), 1, out)
        self.assertIn("README.md", out["refused"])
        self.assertEqual((self.ws / "README.md").read_text(), "the original README\n", "a path not allowed is not taken")
        self.assertIn("use SQLite (décidé)", (self.ws / notes[0]).read_text())
        pushed = sh("git", "--git-dir", str(self.tmp / "bare.git"), "show", f"agent-memory:{notes[0].removeprefix('.agent-memory/')}")
        self.assertIn("use SQLite (décidé)", pushed, "the note is committed and pushed to the memory repo")
        self.assertEqual(out["done"], [mid], "closes only this request's letter")
        self.assertEqual(len(out["sent"]), 1, "replies only to this request's letter, to the original sender")
        tries = json.loads((self.tmp / "tries.json").read_text())
        self.assertTrue(all(code != 0 for code in tries),
                        f"the agent's own sends and closures are blocked (with env vars cleared too): {tries}")
        self.assertNotIn("sent by the agent itself", self.run_("hubbus", "inbox", "--project", "other").stdout)
        self.assertEqual(sh("git", "worktree", "list", "--porcelain", cwd=self.ws).count("worktree "), 1,
                         "the throwaway worktree is cleaned up")
        inbox = self.run_("hubbus", "inbox", "--project", "agent-hub").stdout
        self.assertIn("use SQLite (décidé)", inbox)
        status = json.loads((self.tmp / "state" / "agent-jobs" / "mailbox-demo" / "status.json").read_text())
        self.assertEqual(status["state"], "done", "dispatch reads status to decide whether to re-dispatch")

    def letter(self):
        r = self.run_("hubbus", "send", "--from", "agent-hub", "--to", "demo", "--type", "question", "--text", "which cache?")
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip().split()[-1]

    def test_the_copy_is_in_the_private_temp_folder_and_a_runtime_there_takes_it(self):
        (self.tmp / "agent.py").write_text(ASKS_ACP.replace("@BIN@", str(BIN)))
        mid = self.letter()
        r = self.run_("propose_apply.py", "mailbox", "demo", mid)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        got = json.loads((self.tmp / "tries.json").read_text())
        here = pathlib.Path(got["here"])
        self.assertEqual(here.parent.parent, (self.tmp / "secrets" / "tmp").resolve())
        self.assertTrue(here.parent.name.startswith("hub-propose-demo-"))
        self.assertEqual((got["copy"], got["why"]), (got["here"], ""))
        self.assertEqual(list((self.tmp / "secrets" / "tmp").iterdir()), [], "and gone")
        self.assertEqual(sh("git", "worktree", "list", "--porcelain", cwd=self.ws).count("worktree "), 1)

    def test_a_sandboxed_project_gets_no_copy_no_agent_and_its_letter_stays_open(self):
        path = self.tmp / "config" / "projects" / "demo.yaml"
        path.write_text(yaml.safe_dump({**yaml.safe_load(path.read_text()), "enforce": {"sandbox": True}}, allow_unicode=True))
        mid = self.letter()
        r = self.run_("propose_apply.py", "mailbox", "demo", mid)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("demo", r.stderr)
        self.assertFalse((self.tmp / "tries.json").exists(), "the agent never ran")
        self.assertFalse((self.tmp / "state" / "agent-jobs" / "mailbox-demo").exists(), "no status of a run that wasn't")
        self.assertFalse(list((self.tmp / "secrets").rglob("hub-propose-*")))
        self.assertEqual(sh("git", "worktree", "list", "--porcelain", cwd=self.ws).count("worktree "), 1)
        import sqlite3
        c = sqlite3.connect(next((self.tmp / "bus").rglob("*.db")))
        self.assertEqual(c.execute("SELECT count(*) FROM events WHERE ref=?", (mid,)).fetchone()[0], 0, "not closed, not held, no question to the owner")
        self.assertEqual(c.execute("SELECT count(*) FROM events WHERE type='job.failed'").fetchone()[0], 0)

    def test_note_a_hook_committed_is_still_adopted(self):
        """a session-end hook of the agent's runtime commits the memory notes inside the throwaway worktree (10-31): the
        note is still the agent's change, compared with the commit the worktree started from"""
        (self.tmp / "agent.py").write_text(
            "import json, pathlib, re, subprocess, sys\nprompt = open(sys.argv[1]).read()\n"
            "mid = re.search(r'- id ([^\\s,]+),', prompt).group(1)\n"
            "day = re.search(r'memory/(\\d{4}-\\d{2}-\\d{2})\\.md', prompt).group(1)\n"
            "pathlib.Path(f'.agent-memory/memory/{day}.md').write_text('- a note\\n')\n"
            "subprocess.run(['git', '-C', '.agent-memory', 'add', '-A'], check=True)\n"
            "subprocess.run(['git', '-C', '.agent-memory', 'commit', '-qm', 'hook'], check=True)\n"
            "print(json.dumps({'replies': [], 'done': [{'id': mid}], 'ask_owner': [], 'summary': mid}))\n")
        r = self.run_("hubbus", "send", "--from", "agent-hub", "--to", "demo", "--type", "question", "--text", "note it")
        mid = r.stdout.strip().split()[-1]
        r = self.run_("propose_apply.py", "mailbox", "demo", mid)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual([p for p in out["applied"] if p.startswith(".agent-memory/memory/")], [
            f".agent-memory/memory/{datetime.date.today().isoformat()}.md"], out)

    def test_a_note_in_a_plain_notes_folder_is_adopted(self):
        """The hub's notes are a folder of no repo of their own (memory: false): nothing in the copy lists them, and
        the agent's note must not vanish while its answer says it was written."""
        shutil.rmtree(self.ws / ".agent-memory" / ".git")
        r = self.run_("hubbus", "send", "--from", "agent-hub", "--to", "demo", "--type", "question", "--text", "which cache?")
        mid = r.stdout.strip().split()[-1]
        r = self.run_("propose_apply.py", "mailbox", "demo", mid)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        notes = [p for p in out["applied"] if p.startswith(".agent-memory/memory/")]
        self.assertEqual(len(notes), 1, out)
        self.assertIn("use SQLite", (self.ws / notes[0]).read_text())
        self.assertEqual((self.ws / "README.md").read_text(), "the original README\n")

    def test_a_failed_run_holds_the_letter_and_asks_the_owner(self):
        """claimed by dispatch, failed: held, the owner asked, never dispatched again (nothing loops)"""
        (self.tmp / "agent.py").write_text("import sys\nsys.exit(2)\n")
        r = self.run_("hubbus", "send", "--from", "agent-hub", "--to", "demo", "--type", "request", "--text", "do it")
        mid = r.stdout.strip().split()[-1]
        self.run_("propose_apply.py", "mailbox", "demo", mid)
        check = ("import importlib.machinery, importlib.util, json, sys\n"
                 f"l = importlib.machinery.SourceFileLoader('hb', '{BIN / 'hubbus'}')\n"
                 "s = importlib.util.spec_from_loader('hb', l); m = importlib.util.module_from_spec(s); l.exec_module(m)\n"
                 "c = m.db(); mid = sys.argv[1]\n"
                 "q = lambda sql: c.execute(sql, (mid,)).fetchone()[0]\n"
                 "print(json.dumps([[mid] if m.has_via(c, mid, 'held') else [],"
                 " q(\"SELECT COUNT(*) FROM events WHERE type='message' AND ref=? AND id LIKE '%propose-failed'\"),"
                 " q(\"SELECT COUNT(*) FROM events WHERE type='job.failed' AND ? IS NOT NULL\")]))\n")
        out = json.loads(subprocess.run([sys.executable, "-c", check, mid], env=self.env, capture_output=True,
                                        text=True, check=True).stdout)
        self.assertEqual(out, [[mid], 1, 1])

    def held_and_asked(self, mid):
        check = ("import importlib.machinery, importlib.util, json, sys\n"
                 f"l = importlib.machinery.SourceFileLoader('hb', '{BIN / 'hubbus'}')\n"
                 "s = importlib.util.spec_from_loader('hb', l); m = importlib.util.module_from_spec(s); l.exec_module(m)\n"
                 "c = m.db(); mid = sys.argv[1]\n"
                 "print(json.dumps([[mid] if m.has_via(c, mid, 'held') else [], c.execute(\"SELECT COUNT(*) FROM events WHERE "
                 "type='message' AND ref=? AND id LIKE '%propose-failed'\", (mid,)).fetchone()[0]]))\n")
        return json.loads(subprocess.run([sys.executable, "-c", check, mid], env=self.env, capture_output=True,
                                         text=True, check=True).stdout) == [[mid], 1]

    def test_prose_instead_of_the_structured_answer_is_held_not_silent(self):
        (self.tmp / "agent.py").write_text(
            "import pathlib, re, sys\nprompt = open(sys.argv[1]).read()\n"
            "day = re.search(r'memory/(\\d{4}-\\d{2}-\\d{2})\\.md', prompt).group(1)\n"
            "pathlib.Path(f'.agent-memory/memory/{day}.md').write_text('- a note\\n')\n"
            "print('I wrote a note.')\n")
        r = self.run_("hubbus", "send", "--from", "agent-hub", "--to", "demo", "--type", "question", "--text", "hm")
        mid = r.stdout.strip().split()[-1]
        r = self.run_("propose_apply.py", "mailbox", "demo", mid)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual((out["ok"], out["applied"]), (False, []), out)
        self.assertTrue(self.held_and_asked(mid))

    def test_a_letter_left_unanswered_is_held(self):
        (self.tmp / "agent.py").write_text(
            "import json\nprint(json.dumps({'replies': [], 'done': [], 'ask_owner': [], 'summary': 'nothing'}))\n")
        r = self.run_("hubbus", "send", "--from", "agent-hub", "--to", "demo", "--type", "question", "--text", "hm")
        mid = r.stdout.strip().split()[-1]
        self.run_("propose_apply.py", "mailbox", "demo", mid)
        self.assertTrue(self.held_and_asked(mid))

    def test_a_deferred_letter_stays_open_without_a_failure(self):
        """the prompt's own cases: waits for the owner (asked) or for a code change (a note): left open on purpose"""
        (self.tmp / "agent.py").write_text(
            "import json, pathlib, re, sys\nprompt = open(sys.argv[1]).read()\n"
            "mid = re.search(r'- id ([^\\s,]+),', prompt).group(1)\n"
            "day = re.search(r'memory/(\\d{4}-\\d{2}-\\d{2})\\.md', prompt).group(1)\n"
            "pathlib.Path(f'.agent-memory/memory/{day}.md').write_text('- waits for a code change\\n')\n"
            "print(json.dumps({'replies': [], 'done': [], 'ask_owner': [{'text': 'which one?'}],"
            " 'deferred': [{'id': mid, 'why': 'owner decides'}], 'summary': mid}))\n")
        r = self.run_("hubbus", "send", "--from", "agent-hub", "--to", "demo", "--type", "question", "--text", "hm")
        mid = r.stdout.strip().split()[-1]
        r = self.run_("propose_apply.py", "mailbox", "demo", mid)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertTrue(out["ok"], out)
        self.assertEqual(len([p for p in out["applied"] if p.startswith(".agent-memory/memory/")]), 1)
        self.assertFalse(self.held_and_asked(mid), "no second question, no failure")

    def test_dot_files_under_allowed_paths_are_never_applied(self):
        (self.tmp / "agent.py").write_text(
            "import json, re, sys\nprompt = open(sys.argv[1]).read()\n"
            "mid = re.search(r'- id ([^\\s,]+),', prompt).group(1)\n"
            "open('.agent-memory/memory/.gitignore', 'w').write('*.md\\n')\n"
            "open('.agent-memory/memory/.gitattributes', 'w').write('*.md filter=lfs\\n')\n"
            "print(json.dumps({'replies': [], 'done': [{'id': mid}], 'ask_owner': [], 'summary': mid}))\n")
        r = self.run_("hubbus", "send", "--from", "agent-hub", "--to", "demo", "--type", "question", "--text", "hm")
        mid = r.stdout.strip().split()[-1]
        r = self.run_("propose_apply.py", "mailbox", "demo", mid)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["applied"], [])
        self.assertFalse((self.ws / ".agent-memory" / "memory" / ".gitignore").exists())

    def test_an_attribute_the_agent_plants_reports_no_untouched_file(self):
        """a machine with git-lfs (GitHub's macOS runner, many dev Macs) defines filter.lfs; the `*.md filter=lfs` an
        agent plants then makes git report untouched *.md files as changed (11-01), and copy-back would write the
        worktree's copy over the real checkout's. Configured here at repo level, as a project's own .git/config can:
        still, only the note the agent wrote is applied, and no file whose bytes it left as they were is listed at all.
        The agent rewrites those files with the same bytes, so git looks at their content whatever the clock says (a
        file git saw written in the same instant as its index it rehashes anyway; one written earlier it would not)"""
        for repo in (self.ws, self.ws / ".agent-memory"):   # an lfs-style filter: clean turns the content into a pointer
            sh("git", "config", "filter.lfs.clean", "sed 's/^/pointer /'", cwd=repo)
            sh("git", "config", "filter.lfs.smudge", "cat", cwd=repo)
            sh("git", "config", "filter.lfs.required", "true", cwd=repo)
        edit = self.ws / ".agent-memory" / "memory" / "README.md"   # the owner's session is writing it meanwhile
        edit.write_text("notes\n- not committed yet\n")
        (self.tmp / "agent.py").write_text(
            "import json, pathlib, re, sys\nprompt = open(sys.argv[1]).read()\n"
            "mid = re.search(r'- id ([^\\s,]+),', prompt).group(1)\n"
            "day = re.search(r'memory/(\\d{4}-\\d{2}-\\d{2})\\.md', prompt).group(1)\n"
            "for d in ('.', '.agent-memory', '.agent-memory/memory'):\n"
            "    open(f'{d}/.gitattributes', 'w').write('*.md filter=lfs\\n')\n"
            "for f in ('README.md', '.agent-memory/STATE.md', '.agent-memory/memory/README.md'):\n"
            "    pathlib.Path(f).write_bytes(pathlib.Path(f).read_bytes())\n"
            "pathlib.Path(f'.agent-memory/memory/{day}.md').write_text('- a note\\n')\n"
            "print(json.dumps({'replies': [], 'done': [{'id': mid}], 'ask_owner': [], 'summary': mid}))\n")
        r = self.run_("hubbus", "send", "--from", "agent-hub", "--to", "demo", "--type", "question", "--text", "hm")
        mid = r.stdout.strip().split()[-1]
        r = self.run_("propose_apply.py", "mailbox", "demo", mid)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["applied"], [f".agent-memory/memory/{datetime.date.today().isoformat()}.md"], out)
        self.assertEqual(sorted(out["refused"]), [".agent-memory/.gitattributes", ".agent-memory/memory/.gitattributes",
                                                  ".gitattributes"], out)
        self.assertEqual(edit.read_text(), "notes\n- not committed yet\n", "a file the agent did not change is not "
                         "written over")

    def test_failed_agent_changes_nothing(self):
        (self.tmp / "agent.py").write_text("import sys\nopen('README.md','w').write('x')\n"
                                           "open('.agent-memory/memory/2000-01-01.md','w').write('- half done')\n"
                                           "sys.exit(2)\n")
        r = self.run_("hubbus", "send", "--from", "agent-hub", "--to", "demo", "--type", "request", "--text", "do something")
        mid = r.stdout.strip().split()[-1]
        r = self.run_("propose_apply.py", "mailbox", "demo", mid)
        self.assertEqual(r.returncode, 1)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual((out["done"], out["sent"]), ([], []))
        self.assertFalse((self.ws / ".agent-memory" / "memory" / "2000-01-01.md").exists(),
                         "even an allowed path is not applied from a failed run (the letter is held and the owner asked)")
        self.assertEqual((self.ws / "README.md").read_text(), "the original README\n")


class ChangeListing(unittest.TestCase):
    """project-git-job.py checkout-sigs / changed on their own: bytes decide, whatever git's attributes say"""

    def job(self, op, *args, stdin=None):
        r = subprocess.run([sys.executable, str(BIN / "project-git-job.py"), op, *map(str, args)], input=stdin,
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def setUp(self):
        t = self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, t, ignore_errors=True)
        self.env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t"}
        repo = self.repo = t / "repo"
        repo.mkdir()
        g = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=self.env)  # noqa: E731
        g("init", "-q", "-b", "main")
        # the project's own filter and line endings: its checkout is not the blob byte for byte
        g("config", "filter.mark.smudge", "sed 's/^/smudged /'")
        g("config", "filter.mark.clean", "sed 's/^smudged //'")
        (repo / ".gitattributes").write_text("*.mark filter=mark\n*.crlf text eol=crlf\n")
        (repo / "a.mark").write_text("one\n")
        (repo / "b.crlf").write_text("two\n")
        (repo / "plain.txt").write_text("three\n")
        (repo / "run.sh").write_text("echo\n")
        (repo / "run.sh").chmod(0o755)
        (repo / "link").symlink_to("plain.txt")
        g("add", "-A")
        g("commit", "-qm", "init")
        self.base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True,
                                   check=True).stdout.strip()
        self.wt = t / "wt"
        g("worktree", "add", "-q", "--detach", str(self.wt), "HEAD")

    def changed(self, sigs):
        return [p for p, _ in self.job("changed", self.wt, self.base, stdin=json.dumps(sigs))]

    def test_untouched_is_unchanged_and_every_kind_of_change_is_listed(self):
        sigs = self.job("checkout-sigs", self.wt, self.base)
        self.assertEqual(sorted(sigs), ["a.mark", "b.crlf"], "what the checkout converted is recorded")
        self.assertEqual(self.changed(sigs), [])
        for name in ("a.mark", "b.crlf", "plain.txt"):   # rewritten, same bytes
            (self.wt / name).write_bytes((self.wt / name).read_bytes())
        (self.wt / ".gitattributes").write_text("* -text -filter\n*.txt filter=mark\n")   # the agent's
        self.assertEqual(self.changed(sigs), [".gitattributes"])
        (self.wt / "a.mark").write_text("smudged one, edited\n")
        (self.wt / "run.sh").chmod(0o644)
        (self.wt / "link").unlink()
        (self.wt / "link").symlink_to("a.mark")
        (self.wt / "plain.txt").unlink()
        (self.wt / "new.md").write_text("x\n")
        self.assertEqual(self.changed(sigs), [".gitattributes", "a.mark", "link", "new.md", "plain.txt", "run.sh"])

    def test_stdin_that_is_not_the_snapshot_fails(self):
        r = subprocess.run([sys.executable, str(BIN / "project-git-job.py"), "changed", self.wt, self.base],
                           input="[]", capture_output=True, text=True, env=self.env)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
