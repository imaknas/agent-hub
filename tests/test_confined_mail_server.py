"""The platform's MCP server serving a confined run (docs/design/confined-mail-tools.md M3): `mcp_server.py --run <file>`.

Isolated config / bus / projects, as tests/test_mcp_server.py; the server is spoken to over a pipe.

    bin/py -m unittest tests.test_confined_mail_server
"""
import datetime
import importlib.util
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))


def sh(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Base(unittest.TestCase):
    def setUp(self):
        t = self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, t, True)
        for d in ("home", "config/projects", "data", "secrets", "bus", "state/agent-jobs/.settings"):
            (t / d).mkdir(parents=True)
        self.ws = {}
        for name in ("demo", "other"):
            ws = self.ws[name] = t / "ws" / name
            ws.mkdir(parents=True)
            sh("git", "init", "-q", "-b", "main", cwd=ws)
            (ws / ".agent-memory" / "memory").mkdir(parents=True)
            doc = {"v": 1, "name": name, "paths": {"workspace": str(ws)}, "memory": True}
            (t / "config" / "projects" / f"{name}.yaml").write_text(yaml.safe_dump(doc))
        cfg = {"owner": {"name": "alex", "tag": "Alex"}, "owner_side": "builtin", "bus_dir": str(t / "bus"),
               "registry_dir": "projects", "workspace_root": str(t / "ws")}
        (t / "config" / "hub.json").write_text(json.dumps(cfg))
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("BRUNNA_", "AGENT_HUB_", "AGENTHUB_", "HUB_"))
                       or k in ("AGENT_HUB_PYTHON", "BRUNNA_PYTHON")},
                    "HOME": str(t / "home"), "AGENT_HUB_CONFIG": str(t / "config"), "AGENT_HUB_DATA": str(t / "data"),
                    "AGENT_HUB_SECRETS": str(t / "secrets"), "AGENT_HUB_STATE": str(t / "state"),
                    "AGENT_HUB_HOME": str(t / "instance"), "AGENT_HUB_LANG_UI": "en",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        self.env.pop("CLAUDE_CODE_SESSION_ID", None)
        self.env.pop("MAILBOX_LETTERS", None)

    def hubbus(self, *args, env=None, cwd=None):
        r = subprocess.run([sys.executable, str(BIN / "hubbus"), *args], env={**self.env, **(env or {})},
                           cwd=cwd or self.tmp, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip().split()[-1] if r.stdout.strip() else ""

    def run_file(self, point="mailbox-escalation", project="demo", tools=None, letters=None, mode=0o600, **extra):
        d = self.tmp / "state" / "agent-jobs" / ".settings" / f"{project}.{point}.{os.urandom(4).hex()}"
        d.mkdir()
        f = d / "server.json"
        if tools is None:
            tools = (["send", "ask_owner", "note"] if point == "agent-job-confined"
                     else ["inbox", "show", "thread", "send", "done", "ask_owner", "note"])
        body = {"project": project, "point": point, "tools": tools, **({"letters": letters} if letters is not None else {}),
                **extra}
        f.write_text(json.dumps(body))
        f.chmod(mode)
        return f

    def serve(self, run_file, calls, env=None, cwd=None):
        """initialize + tools/list + calls → (initialize result, tool names, [(isError, text)])."""
        msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]
        msgs += [{"jsonrpc": "2.0", "id": 10 + i, "method": "tools/call", "params": {"name": n, "arguments": a}}
                 for i, (n, a) in enumerate(calls)]
        # the agent's own environment: its point and letters as llm.py set them, which the server must not trust
        agent = {"HUB_LLM_POINT": "mailbox-escalation", "AGENT_HUB_CONFINED": "1", **(env or {})}
        r = subprocess.run([sys.executable, str(BIN / "mcp_server.py"), "--run", str(run_file)],
                           input="".join(json.dumps(m) + "\n" for m in msgs), env={**self.env, **agent},
                           cwd=cwd or self.tmp, capture_output=True, text=True, timeout=120)
        out = {m["id"]: m for m in map(json.loads, r.stdout.splitlines())}
        self.assertEqual(len(out), 2 + len(calls), r.stdout + r.stderr)
        res = [(out[10 + i]["result"]["isError"], out[10 + i]["result"]["content"][0]["text"]) for i in range(len(calls))]
        return out[0]["result"], [t["name"] for t in out[1]["result"]["tools"]], res

    def rows(self, where="1", *args):
        c = sqlite3.connect(self.tmp / "bus" / "bus.db")
        c.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in c.execute(f"SELECT * FROM events WHERE {where} ORDER BY seq", args)]
        except sqlite3.OperationalError:   # nothing was ever written: no bus yet
            return []
        finally:
            c.close()

    def letters(self, source):
        return [(r["to_project"], json.loads(r["payload"])) for r in self.rows("type='message' AND source=?", source)]


class RunFile(Base):
    def test_a_bad_file_answers_every_call_with_the_reason(self):
        good = self.run_file(letters=["m1"])
        link = good.parent / "link.json"
        link.symlink_to(good)
        outside = self.tmp / "server.json"
        outside.write_text(good.read_text())
        outside.chmod(0o600)
        cases = {"a link": link, "outside the run folders": outside,
                 "writable by others": self.run_file(letters=["m1"], mode=0o666),
                 "a point without the mail tools": self.run_file(point="agent-job-event"),
                 "an unregistered project": self.run_file(project="nobody", letters=["m1"]),
                 "a letters point without letters": self.run_file(),
                 "a bad letter id": self.run_file(letters=["m1\n"]),
                 "tools the point doesn't give": self.run_file(point="agent-job-confined", tools=["send", "inbox"])}
        for why, f in cases.items():
            with self.subTest(why):
                init, tools, res = self.serve(f, [("send", {"to": "other", "type": "info", "text": "x"})])
                self.assertTrue(tools, "the server still answers")
                self.assertTrue(res[0][0])
                self.assertIn("this run's mail is refused", res[0][1])
        self.assertEqual(self.letters("demo"), [])

    def test_the_tools_are_the_points(self):
        _, tools, _ = self.serve(self.run_file(letters=["m1"]), [])
        self.assertEqual(set(tools), {"inbox", "show", "thread", "send", "done", "ask_owner", "note"})
        _, tools, res = self.serve(self.run_file(point="agent-job-confined", tools=["send", "ask_owner"]),
                                   [("inbox", {}), ("ack", {"ids": ["m1"]}), ("note", {"text": "x"})])
        self.assertEqual(set(tools), {"send", "ask_owner"})
        self.assertEqual([e for e, _ in res], [True, True, True])

    def test_without_run_a_platform_run_is_refused(self):
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "send", "arguments": {"to": "other", "type": "info", "text": "x"}}}]
        r = subprocess.run([sys.executable, str(BIN / "mcp_server.py")], input=json.dumps(msgs[0]) + "\n",
                           env={**self.env, "HUB_LLM_POINT": "agent-job"}, cwd=self.ws["demo"], capture_output=True,
                           text=True, timeout=60)
        self.assertTrue(json.loads(r.stdout)["result"]["isError"])
        self.assertEqual(self.letters("demo"), [])


class Served(Base):
    def test_its_project_is_the_files(self):
        """Whatever AGENTHUB_PROJECT and the folder say (a cwd: state job ran in the hub's folder and sent as the hub)."""
        f = self.run_file(point="agent-job-confined")
        _, _, res = self.serve(f, [("send", {"to": "other", "type": "info", "text": "hello"})],
                               env={"AGENTHUB_PROJECT": "other", "HUB_LLM_POINT": "agent-job-confined"}, cwd=self.ws["other"])
        self.assertFalse(res[0][0], res)
        self.assertEqual([(to, p["text"], p.get("point")) for to, p in self.letters("demo")],
                         [("other", "hello", "agent-job-confined")])
        self.assertEqual(self.letters("other"), [])

    def test_no_value_becomes_an_option(self):
        mid = self.hubbus("send", "--from", "other", "--to", "demo", "--type", "request", "--text", "please")
        f = self.run_file(letters=[mid])
        _, _, res = self.serve(f, [
            ("send", {"to": "other", "type": "done", "text": "--withdraw", "reply_to": mid}),
            ("done", {"ids": ["--withdraw"]}),
            ("done", {"ids": [mid + "\n"]}),
            ("done", {"ids": mid}),
            ("ask_owner", {"text": "which?", "options": [{"id": "--text", "label": "x"}]}),
            ("ask_owner", {"text": "which?", "options": [{"id": "a", "label": "--text"}]}),
            ("show", {"id": "-h"}),
        ])
        self.assertFalse(res[0][0], res)
        self.assertEqual([e for e, _ in res[1:]], [True] * 6, res)
        self.assertEqual([p["text"] for _, p in self.letters("demo")], ["--withdraw"])
        self.assertEqual(self.rows("type='message.done'"), [])

    def test_the_letters_limits_hold(self):
        m1 = self.hubbus("send", "--from", "other", "--to", "demo", "--type", "request", "--text", "one")
        m2 = self.hubbus("send", "--from", "other", "--to", "demo", "--type", "request", "--text", "two")
        f = self.run_file(letters=[m1])
        _, _, res = self.serve(f, [("done", {"ids": [m2]}), ("show", {"id": m2}),
                                   ("send", {"to": "other", "type": "info", "text": "x"}),
                                   ("send", {"to": "owner", "type": "request", "text": "a to-do"}),
                                   ("send", {"to": "other", "type": "done", "text": "handled", "reply_to": m1}),
                                   ("done", {"ids": [m1], "note": "handled"})])
        self.assertEqual([e for e, _ in res], [True, True, True, True, False, False], res)
        self.assertEqual({r["ref"] for r in self.rows("type='message.done'")}, {m1})

    def test_a_jobs_send_starts_new_letters_only(self):
        mid = self.hubbus("send", "--from", "other", "--to", "demo", "--type", "request", "--text", "x")
        f = self.run_file(point="agent-job-confined")
        _, _, res = self.serve(f, [("send", {"to": "other", "type": "info", "text": "x", "reply_to": mid}),
                                   ("send", {"to": "owner", "type": "done", "text": "x", "question": "q1"}),
                                   ("send", {"to": "other", "type": "done", "text": "x"}),
                                   ("send", {"to": "owner", "type": "request", "text": "x"}),
                                   ("ask_owner", {"text": "a question", "options": [{"id": "a", "label": "A"}]})],
                               env={"HUB_LLM_POINT": "agent-job-confined"})
        self.assertEqual([e for e, _ in res], [True, True, True, True, False], res)

    def test_a_record_is_no_letter_to_close(self):
        mid = self.hubbus("send", "--from", "other", "--to", "demo", "--type", "request", "--text", "x")
        self.hubbus("publish", "job.failed", "--source", "other", "--to", "demo", "--id", "rec-like", "--payload", "{}")
        _, _, res = self.serve(self.run_file(letters=[mid, "rec-like"]), [("done", {"ids": ["rec-like"]})])
        self.assertTrue(res[0][0], res)

    def test_a_sandboxed_projects_letter_goes_to_its_outbox(self):
        doc = yaml.safe_load((self.tmp / "config" / "projects" / "demo.yaml").read_text())
        doc["enforce"] = {"sandbox": True}
        (self.tmp / "config" / "projects" / "demo.yaml").write_text(yaml.safe_dump(doc))
        _, _, res = self.serve(self.run_file(point="agent-job-confined"),
                               [("send", {"to": "other", "type": "info", "text": "boxed"})],
                               env={"HUB_LLM_POINT": "agent-job-confined"})
        self.assertFalse(res[0][0], res)
        self.assertEqual(self.letters("demo"), [], "never on the bus directly")
        box = list((self.ws["demo"] / ".git" / "brunna" / "outbox").glob("*"))
        self.assertEqual(len(box), 1)


class Notes(Base):
    def day(self):
        return f"{datetime.date.today().isoformat()}.md"

    def note(self, project="demo"):
        f = self.run_file(point="agent-job-confined", project=project)
        return self.serve(f, [("note", {"text": "an entry"})], env={"HUB_LLM_POINT": "agent-job-confined"})[2][0]

    def test_it_writes_and_creates_the_day_file(self):
        self.assertFalse(self.note()[0])
        self.assertEqual((self.ws["demo"] / ".agent-memory" / "memory" / self.day()).read_text(), "- an entry\n")

    def test_it_never_writes_through_a_link(self):
        target = self.tmp / "home" / ".zshenv"
        target.write_text("")
        mem = self.ws["demo"] / ".agent-memory" / "memory"
        (mem / self.day()).symlink_to(target)
        self.assertTrue(self.note()[0])
        (mem / self.day()).unlink()
        os.link(target, mem / self.day())
        self.assertTrue(self.note()[0], "a hard link to a file elsewhere")
        (mem / self.day()).unlink()
        shutil.rmtree(mem)
        mem.symlink_to(self.tmp / "home")
        self.assertTrue(self.note()[0])
        self.assertEqual(target.read_text(), "")


class Unit(unittest.TestCase):
    """The server's own pieces, without a process."""

    def load(self):
        spec = importlib.util.spec_from_file_location("mcp_server_unit", BIN / "mcp_server.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_hubbus_gets_the_runs_values_and_no_login(self):
        m = self.load()
        run = m.Run.__new__(m.Run)
        run.project, run.point, run.letters, run.chain, run.depth = "demo", "mailbox-escalation", ["m1"], '["a/b"]', "1"
        agent = {"PATH": "/usr/bin", "HOME": "/h", "CLAUDE_CODE_SESSION_ID": "s-1", "CLAUDE_CODE_OAUTH_TOKEN": "secret",
                 "HUB_LLM_POINT": "x", "MAILBOX_LETTERS": '["other"]', "AGENT_HUB_JOB": "a/job", "BRUNNA_JOB": "a/job",
                 "AGENT_JOB_ALLOW": "[]", "HUBBUS_LEASE": "1", "LLM_AUTH": "subscription", "AGENT_HUB_STATE": "/s"}
        with mock.patch.dict(os.environ, agent, clear=True):
            env = m.run_env(run)
        self.assertEqual(env["HUB_LLM_POINT"], "mailbox-escalation")
        self.assertEqual(json.loads(env["MAILBOX_LETTERS"]), ["m1"])
        self.assertEqual(env["CLAUDE_CODE_SESSION_ID"], "s-1")
        for gone in ("CLAUDE_CODE_OAUTH_TOKEN", "AGENT_HUB_JOB", "BRUNNA_JOB", "AGENT_JOB_ALLOW", "HUBBUS_LEASE", "LLM_AUTH"):
            self.assertNotIn(gone, env)
        import names
        self.assertEqual(names.env("CONFINED", environ=env), "1")
        self.assertEqual(names.env("STATE", environ=env), "/s")
        self.assertEqual(names.env("JOB_CHAIN", environ=env), '["a/b"]')

    def test_a_folder_that_is_another_projects_is_refused(self):
        m = self.load()
        run = m.Run.__new__(m.Run)
        run.project, run.refusal, run.tools, run.workspace = "demo", None, m.HANDLER_TOOLS, pathlib.Path("/x")
        hb = mock.Mock(project_of=lambda p: "other")
        with mock.patch.object(m, "hb", lambda: hb):
            ok, why = m.run_call(run, "inbox", {})
        self.assertFalse(ok)
        self.assertIn("demo", why)


if __name__ == "__main__":
    unittest.main()
