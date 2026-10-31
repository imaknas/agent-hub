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
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith("AGENT_HUB_") or k == "AGENT_HUB_PYTHON"},
                    "HOME": str(t / "home"), "AGENT_HUB_CONFIG": str(t / "config"),
                    "AGENT_HUB_DATA": str(t / "data"), "AGENT_HUB_SECRETS": str(t / "secrets"),
                    "AGENT_HUB_STATE": str(t / "state"), "PROPOSE_TEST_TRIES": str(t / "tries.json"),
                    # bin/py under the fake HOME (git-sync-memory.sh asks project_git.py whose memory this is)
                    "AGENT_HUB_PYTHON": os.environ.get("AGENT_HUB_PYTHON", sys.executable),
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

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

    def test_a_failed_run_holds_the_letter_and_asks_the_owner(self):
        """claimed by dispatch, failed: never left claimed without a word, never dispatched again (nothing loops)"""
        (self.tmp / "agent.py").write_text("import sys\nsys.exit(2)\n")
        r = self.run_("hubbus", "send", "--from", "agent-hub", "--to", "demo", "--type", "request", "--text", "do it")
        mid = r.stdout.strip().split()[-1]
        self.run_("propose_apply.py", "mailbox", "demo", mid)
        check = ("import importlib.machinery, importlib.util, json, sys\n"
                 f"l = importlib.machinery.SourceFileLoader('hb', '{BIN / 'hubbus'}')\n"
                 "s = importlib.util.spec_from_loader('hb', l); m = importlib.util.module_from_spec(s); l.exec_module(m)\n"
                 "c = m.db(); mid = sys.argv[1]\n"
                 "q = lambda sql: c.execute(sql, (mid,)).fetchone()[0]\n"
                 "print(json.dumps([sorted(m.claimed(c, [mid])),"
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
                 "print(json.dumps([sorted(m.claimed(c, [mid])), c.execute(\"SELECT COUNT(*) FROM events WHERE "
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


if __name__ == "__main__":
    unittest.main()
