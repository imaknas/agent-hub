"""agent 提議、平台套用(bin/propose_apply.py)跟指令樣板的 runtime(runtime_command):任何 CLI agent 都能處理信箱交辦。

隔離的 config / 匯流排 / 專案 repo(含 .agent-memory worktree);agent 是一支假的 CLI:它寫一筆 memory 筆記(准許)、
改 README(不准許),最後回 JSON。檢查:只有筆記被採用、README 原封不動、worktree 清乾淨、回信跟結案由平台代寄。

    bin/py -m unittest discover -s tests
"""
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

AGENT = r'''
import json, pathlib, re, sys
prompt = open(sys.argv[1]).read()
mid = re.search(r"- id (\S+),來自", prompt).group(1)
day = re.search(r"memory/(\d{4}-\d{2}-\d{2})\.md", prompt).group(1)
note = pathlib.Path(".agent-memory/memory") / f"{day}.md"
note.parent.mkdir(parents=True, exist_ok=True)
note.write_text("- 回答了 other 的問題:用 SQLite\n")
pathlib.Path("README.md").write_text("agent 不該改這個\n")
# 自己寄信、結案:一般寄、清掉環境變數再寄(兩種都要被 hubbus 擋;平台照輸出代寄)
import os, subprocess
tries = []
for env in (dict(os.environ), {k: v for k, v in os.environ.items() if k != "HUB_MAIL_BY_PLATFORM"}):
    r = subprocess.run([sys.executable, "@HUBBUS@", "send", "--from", "demo", "--to", "other", "--type", "info",
                        "--text", "agent 自己寄的"], env=env, capture_output=True, text=True)
    tries.append(r.returncode)
r = subprocess.run([sys.executable, "@HUBBUS@", "done", mid], capture_output=True, text=True)
tries.append(r.returncode)
pathlib.Path(os.environ["PROPOSE_TEST_TRIES"]).write_text(json.dumps(tries))
print(json.dumps({"replies": [{"reply_to": mid, "type": "done", "text": "用 SQLite"},
                              {"reply_to": "not-this-one", "type": "info", "text": "不該寄出"}],
                  "done": [{"id": mid, "note": "回答了"}, {"id": "not-this-one"}],
                  "ask_owner": [], "summary": f"{mid}:回答了"}, ensure_ascii=False))
'''


def sh(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class ProposeApply(unittest.TestCase):
    def setUp(self):
        t = self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        for d in ("home", "config/projects", "data", "secrets", "bus"):
            (t / d).mkdir(parents=True)
        # 專案 repo + memory bare repo + .agent-memory worktree
        ws = self.ws = t / "ws" / "demo"
        ws.mkdir(parents=True)
        sh("git", "init", "-q", "-b", "main", cwd=ws)
        (ws / "README.md").write_text("原本的 README\n")
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
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def run_(self, script, *args, stdin=""):
        return subprocess.run([sys.executable, str(BIN / script), *args], input=stdin, env=self.env, cwd=self.tmp,
                              capture_output=True, text=True, timeout=300)

    def test_any_cli_agent_handles_a_letter(self):
        r = self.run_("hubbus", "send", "--from", "agent-hub", "--to", "demo", "--type", "question", "--text", "快取用哪個?")
        self.assertEqual(r.returncode, 0, r.stderr)
        mid = r.stdout.strip().split()[-1]
        r = self.run_("propose_apply.py", "mailbox", "demo", mid)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        notes = [p for p in out["applied"] if p.startswith(".agent-memory/memory/")]
        self.assertEqual(len(notes), 1, out)
        self.assertIn("README.md", out["refused"])
        self.assertEqual((self.ws / "README.md").read_text(), "原本的 README\n", "不准的路徑不採用")
        self.assertIn("用 SQLite", (self.ws / notes[0]).read_text())
        pushed = sh("git", "--git-dir", str(self.tmp / "bare.git"), "show", f"agent-memory:{notes[0].removeprefix('.agent-memory/')}")
        self.assertIn("用 SQLite", pushed, "筆記 commit 並 push 到 memory repo")
        self.assertEqual(out["done"], [mid], "只結這次交辦的信")
        self.assertEqual(len(out["sent"]), 1, "只回這次交辦的信、回給原寄件人")
        tries = json.loads((self.tmp / "tries.json").read_text())
        self.assertTrue(all(code != 0 for code in tries), f"agent 自己寄信、結案都被擋(清掉環境變數也一樣):{tries}")
        self.assertNotIn("agent 自己寄的", self.run_("hubbus", "inbox", "--project", "other").stdout)
        self.assertEqual(sh("git", "worktree", "list", "--porcelain", cwd=self.ws).count("worktree "), 1, "丟棄式 worktree 清掉了")
        inbox = self.run_("hubbus", "inbox", "--project", "agent-hub").stdout
        self.assertIn("用 SQLite", inbox)
        status = json.loads((self.tmp / "state" / "agent-jobs" / "mailbox-demo" / "status.json").read_text())
        self.assertEqual(status["state"], "done", "dispatch 看 status 決定要不要重派")

    def test_failed_agent_changes_nothing(self):
        (self.tmp / "agent.py").write_text("import sys\nopen('README.md','w').write('x')\nsys.exit(2)\n")
        r = self.run_("hubbus", "send", "--from", "agent-hub", "--to", "demo", "--type", "request", "--text", "做件事")
        mid = r.stdout.strip().split()[-1]
        r = self.run_("propose_apply.py", "mailbox", "demo", mid)
        self.assertEqual(r.returncode, 1)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual((out["done"], out["sent"]), ([], []))
        self.assertEqual((self.ws / "README.md").read_text(), "原本的 README\n")


if __name__ == "__main__":
    unittest.main()
