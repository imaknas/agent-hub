"""Event-triggered jobs through the scheduler's tick (docs/design/event-jobs.md): first sight, the batch a run gets,
the cursor on success and failure, debounce, the loop chain, background runs, a new bus file, renames, the date
filters, after:, the lock; and the registry's trigger: validation and conversion.

The tick runs in-process (tests/test_scheduler_tick.py's harness) against a real SQLite bus in a temp directory.

    bin/py -m unittest tests.test_scheduler_events
"""
import datetime
import io
import json
import types
import os
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))
import hubconf  # noqa: E402
from test_scheduler_tick import DAY, Tick  # noqa: E402

LOCAL = datetime.datetime.now().astimezone().tzinfo

# the assertions read the English texts: pin the UI language (an instance's hub.json may set another)
_LANG = mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "en"})


def setUpModule():
    _LANG.start()


def tearDownModule():
    _LANG.stop()


class Bus:
    def __init__(self, d: pathlib.Path, generation="g1"):
        self.db = d / "bus.db"
        c = self.c = sqlite3.connect(self.db, isolation_level=None)
        c.execute("CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, ts TEXT NOT NULL,"
                  " type TEXT NOT NULL, source TEXT NOT NULL, to_project TEXT, ref TEXT, payload TEXT NOT NULL DEFAULT '{}')")
        c.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        c.execute("INSERT INTO meta VALUES('generation', ?)", (generation,))
        self.n = 0

    def publish(self, type="merge.done", project="p", at=DAY, **payload):
        self.n += 1
        self.c.execute("INSERT INTO events(id, ts, type, source, to_project, payload) VALUES(?,?,?,?,?,?)",
                       (f"e{self.n}", at.replace(tzinfo=LOCAL).isoformat(), type, project, project,
                        json.dumps({"v": 1, **payload})))
        return self.n


def ejob(name="p/react", **kw):
    trigger = {"events": ["merge.done"], "projects": ["p"], "sources": [], "where": {}, "debounce_minutes": 0}
    trigger.update(kw.pop("trigger", {}))
    return {"name": name, "project": name.split("/")[0], "command": "echo react", "trigger": trigger,
            "max_attempts_per_day": 12, **kw}


class EventTick(unittest.TestCase):
    def setUp(self):
        self.t = Tick()
        self.bus = Bus(self.t.tmp)
        self.addCleanup(lambda: self.bus.c.close())
        p = mock.patch.object(hubconf, "BUS_DIR", self.t.tmp)
        p.start()
        self.addCleanup(p.stop)

    def minutes(self, n):
        return DAY + datetime.timedelta(minutes=n)

    def events_of(self, i=-1):
        return json.loads(self.t.ran[i]["env"]["AGENT_HUB_EVENTS"])["events"]

    def failed(self):
        return [b for b in self.t.bus if b["argv"][:2] == ["publish", "job.failed"]]

    def test_first_sight_then_a_batch_then_the_cursor(self):
        self.bus.publish()                                   # history: never run
        self.t.tick([ejob()], DAY)
        self.assertEqual(self.t.ran, [])
        self.assertEqual(self.t.state()["p/react.event_seq"], 1)
        self.bus.publish(branch="b1")
        self.bus.publish(project="q")                        # another project's: not seen
        self.bus.publish(branch="b2")
        self.t.tick([ejob()], self.minutes(15))
        self.assertEqual([e["payload"]["branch"] for e in self.events_of()], ["b1", "b2"])
        env = self.t.ran[0]["env"]
        self.assertEqual((env["AGENT_HUB_JOB"], json.loads(env["AGENT_HUB_JOB_CHAIN"])), ("p/react", ["p/react"]))
        st = self.t.state()
        self.assertEqual(st["p/react.event_seq"], 4)
        self.assertEqual(st["p/react.event_last"]["count"], 2)
        self.t.tick([ejob()], self.minutes(30))             # consumed: nothing more
        self.assertEqual(len(self.t.ran), 1)

    def test_failure_keeps_the_batch_and_publishes_with_project_and_chain(self):
        self.t.tick([ejob()], DAY)
        self.bus.publish(chain=["p/root"])
        job = ejob(command="exit 4")
        self.t.tick([job], self.minutes(15))
        self.bus.publish(branch="later")
        self.t.tick([job], self.minutes(30))
        self.assertEqual([len(json.loads(r["env"]["AGENT_HUB_EVENTS"])["events"]) for r in self.t.ran], [1, 2])
        self.assertEqual(self.t.state()["p/react.event_seq"], 0)
        argv, env = self.failed()[0]["argv"], self.failed()[0]["env"]
        self.assertEqual(argv[argv.index("--to") + 1], "p")
        self.assertEqual(json.loads(env["AGENT_HUB_JOB_CHAIN"]), ["p/root", "p/react"])
        self.t.tick([ejob()], self.minutes(45))
        self.assertEqual(self.t.state()["p/react.event_seq"], 2)

    def test_daily_cap(self):
        self.t.tick([ejob()], DAY)
        job = ejob(command="exit 1", max_attempts_per_day=2)
        self.bus.publish()
        for i in range(1, 5):
            self.t.tick([job], self.minutes(15 * i))
        self.assertEqual(len(self.t.ran), 2)

    def test_debounce_waits_for_quiet_with_a_ceiling(self):
        job = ejob(trigger={"debounce_minutes": 10})
        self.t.tick([job], DAY)
        self.bus.publish(at=self.minutes(10))
        self.t.tick([job], self.minutes(15))                 # 5 minutes quiet: wait
        self.assertEqual(self.t.ran, [])
        self.bus.publish(at=self.minutes(18))
        self.t.tick([job], self.minutes(25))                 # newest only 7 minutes old
        self.assertEqual(self.t.ran, [])
        self.bus.publish(at=self.minutes(48))
        self.t.tick([job], self.minutes(50))                 # oldest 40 minutes old: the ceiling
        self.assertEqual(len(self.events_of()), 3)

    def test_the_chain_holds_back_a_loop_and_counts_it(self):
        self.t.tick([ejob()], DAY)
        self.bus.publish(chain=["p/react"])
        self.bus.publish(chain=["a", "b", "c", "d"])
        self.t.tick([ejob()], self.minutes(15))
        self.assertEqual(self.t.ran, [])
        st = self.t.state()
        self.assertEqual(st["p/react.event_seq"], 2)
        self.assertEqual(st["p/react.event_held"], {"date": "2026-10-30", "count": 2, "last": "e2"})

    def test_background_run_takes_its_batch_on_reconcile(self):
        job = ejob(background=True)
        self.t.tick([job], DAY)
        self.bus.publish()
        self.t.tick([job], self.minutes(15))
        name, _cmd, _log, _now, _today, env, extra, _minutes = self.t.spawned[0]
        self.assertEqual(name, "p/react")
        self.assertEqual(len(json.loads(env["AGENT_HUB_EVENTS"])["events"]), 1)
        self.assertEqual((extra["events_upto"], extra["project"], extra["chain"]), (1, "p", ["p/react"]))
        self.assertEqual(self.t.state()["p/react.event_seq"], 0)
        (self.t.m.RUNS_DIR / "p__react.json").write_text(json.dumps(
            {"date": "2026-10-30", "started": "2026-10-30T12:15:00", "pid": 1, "exit": 0, **extra}))
        self.t.tick([job], self.minutes(30))
        self.assertEqual(self.t.state()["p/react.event_seq"], 1)
        self.assertEqual(len(self.t.spawned), 1)
        self.assertFalse((self.t.m.RUNS_DIR / "p__react.json").exists())

    def test_a_new_bus_file_resets_the_cursor(self):
        self.t.tick([ejob()], DAY)
        for _ in range(3):
            self.bus.publish()
        self.t.tick([ejob()], self.minutes(15))
        self.assertEqual(self.t.state()["p/react.event_seq"], 3)
        self.bus.c.close()
        self.bus.db.unlink()
        self.bus = Bus(self.t.tmp, generation="g2")
        for _ in range(5):
            self.bus.publish()
        self.t.tick([ejob()], self.minutes(30))
        self.assertEqual(len(self.t.ran), 1)                 # the new file's events are not run
        self.assertEqual((self.t.state()["p/react.event_seq"], self.t.state()["p/react.event_gen"]), (5, "g2"))

    def test_first_sight_happens_on_a_day_the_job_does_not_run(self):
        job = ejob(days_of_week=["mon"])
        self.t.tick([job], DAY)                              # a Friday
        self.assertIn("p/react.event_seq", self.t.state())
        self.bus.publish()
        self.t.tick([job], self.minutes(15))
        self.assertEqual(self.t.ran, [])
        self.t.tick([job], DAY + datetime.timedelta(days=3))
        self.assertEqual(len(self.events_of()), 1)

    def test_a_renamed_job_does_not_start_while_its_old_name_runs(self):
        st = {"old/react.event_seq": 0, "old/react.event_gen": "g1"}
        self.t.m.STATE_FILE.write_text(json.dumps(st))
        (self.t.m.RUNS_DIR / "old__react.json").write_text(json.dumps(
            {"date": "2026-10-30", "started": "2026-10-30T11:50:00", "pid": os.getpid()}))
        self.bus.publish(project="old")
        with mock.patch.object(hubconf, "former_names", lambda p: ["old"] if p == "new" else []):
            self.t.tick([ejob("new/react", background=True, trigger={"projects": ["new", "old"]})], DAY)
        self.assertEqual(self.t.spawned, [])

    def test_an_old_names_late_advance_still_counts(self):
        for _ in range(3):
            self.bus.publish(project="other")
        st = {"old/react.event_seq": 3, "old/react.event_gen": "g1", "new/react.event_seq": 1, "new/react.event_gen": "g1"}
        self.t.m.STATE_FILE.write_text(json.dumps(st))
        with mock.patch.object(hubconf, "former_names", lambda p: ["old"] if p == "new" else []):
            self.t.tick([ejob("new/react", trigger={"projects": ["new", "old"]})], DAY)
        self.assertEqual(self.t.state()["new/react.event_seq"], 3)

    def test_a_renamed_project_keeps_its_cursor_and_old_events(self):
        st = {"old/react.event_seq": 0, "old/react.event_gen": "g1"}
        self.t.m.STATE_FILE.write_text(json.dumps(st))
        self.bus.publish(project="old")
        job = ejob("new/react", trigger={"projects": ["new", "old"]})
        with mock.patch.object(hubconf, "former_names", lambda p: ["old"] if p == "new" else []):
            self.t.tick([job], DAY)
        self.assertEqual(len(self.events_of()), 1)
        self.assertEqual(self.t.state()["new/react.event_seq"], 1)

    def test_after_an_event_job_waits_only_while_it_runs(self):
        jobs = [ejob(), {"name": "w", "time": "06:00", "after": ["p/react"], "command": "echo w"}]
        self.t.tick(jobs, DAY)
        self.assertEqual(self.t.commands(), ["echo w"])

    def test_a_held_lock_skips_the_tick(self):
        self.t.m.TICK_LOCK_WAIT = 0.3
        with self.t.m.state_lock() as mine:
            self.assertTrue(mine)
            self.t.tick([{"name": "a", "time": "06:00", "command": "echo a"}], DAY)
            with self.t.m.state_lock() as again:          # a second writer waits, then gives up: never hangs
                self.assertFalse(again)
        self.assertEqual(self.t.ran, [])
        self.assertFalse(self.t.m.HEARTBEAT.exists())       # a tick that couldn't run doesn't look alive
        self.t.tick([{"name": "a", "time": "06:00", "command": "echo a"}], DAY)
        self.assertEqual(len(self.t.ran), 1)

    def test_every_run_gets_its_name_and_chain(self):
        self.t.tick([{"name": "a", "time": "06:00", "command": "echo a"}], DAY)
        env = self.t.ran[0]["env"]
        self.assertEqual((env["AGENT_HUB_JOB"], env["AGENT_HUB_JOB_CHAIN"], env["AGENT_HUB_JOB_DEPTH"]), ("a", '["a"]', "1"))
        self.assertNotIn("AGENT_HUB_EVENTS", env)

    def test_no_bus_file_no_cursor(self):
        self.bus.c.close()
        self.bus.db.unlink()
        self.t.tick([ejob()], DAY)
        self.assertNotIn("p/react.event_seq", self.t.state())

    def test_depth_runs_on_from_the_batch(self):
        self.t.tick([ejob()], DAY)
        self.bus.publish(chain=["a", "b"], depth=2)
        self.t.tick([ejob()], self.minutes(15))
        env = self.t.ran[0]["env"]
        self.assertEqual((json.loads(env["AGENT_HUB_JOB_CHAIN"]), env["AGENT_HUB_JOB_DEPTH"]), (["a", "b", "p/react"], "3"))


class RegistryTrigger(unittest.TestCase):
    def setUp(self):
        import registry
        self.r = registry

    def errs(self, trigger, stem="p", doc=None, **job):
        return self.r.validate_trigger({"name": "j", "trigger": trigger, **job}, doc or {"name": stem}, stem)

    def test_valid(self):
        self.assertEqual(self.errs({"events": ["merge.done", "job.failed"], "where": {"branch": "c/*"},
                                    "debounce": "10m", "sources": ["p"]}), [])
        self.assertEqual(self.errs({"events": ["message"], "where": {"type": ["info", "done"]}}), [])
        self.assertEqual(self.errs({"events": ["merge.done"], "projects": ["*"]}, stem=hubconf.hub_project()), [])
        self.assertEqual(self.errs({"events": ["merge.done"]}, kind="agent", allow=["Read"]), [])
        rt = {"name": "p", "runtime": {}}
        self.assertEqual(self.errs({"events": ["merge.done"]}, doc=rt, kind="agent", allow=["Read"], cwd="workspace"), [])

    def test_invalid(self):
        for trigger, extra, what in (
                ({"events": []}, {}, "non-empty"),
                ({"events": ["nope"]}, {}, "not a bus event type"),
                ({"events": ["record"]}, {}, "mailbox work"),
                ({"events": ["message.delivered"]}, {}, "mailbox work"),
                ({"events": ["message.routed"]}, {}, "mailbox work"),
                ({"events": ["message.passed"]}, {}, "mailbox work"),
                ({"events": ["message.woken"]}, {}, "mailbox work"),
                ({"events": ["message.done"]}, {}, "mailbox work"),
                ({"events": ["merge.done"]}, {"kind": "agent"}, "must declare allow"),
                ({"events": ["merge.done"]}, {"kind": "agent", "allow": ["Read"], "cwd": "state"}, "in the workspace"),
                ({"events": ["merge.done"]}, {"kind": "agent", "allow": ["Read"], "connectors": True}, "connectors"),
                ({"events": ["message"]}, {}, "notices"),
                ({"events": ["message"], "where": {"type": "request"}}, {}, "notices"),
                ({"events": ["merge.done"], "projects": ["q"]}, {}, "own project"),
                ({"events": ["merge.done"], "debounce": "soon"}, {}, "debounce"),
                ({"events": ["merge.done"], "where": {"x": {"a": 1}}}, {}, "where.x"),
                ({"events": ["merge.done"], "when": 1}, {}, "unknown field"),
                ({"events": ["merge.done"]}, {"legacy_name": "x"}, "legacy_name")):
            errs = self.errs(trigger, **extra)
            self.assertTrue(any(what in e for e in errs), (trigger, errs))
        hub = hubconf.hub_project()
        for doc, stem in (({"name": hub}, hub), ({"name": "m", "type": "module"}, "m")):
            errs = self.errs({"events": ["merge.done"]}, stem=stem, doc=doc, kind="agent", allow=["Read"])
            self.assertTrue(any("can't be agent jobs" in e for e in errs), errs)

    def test_exactly_one_trigger_and_yaml_key(self):
        import yaml
        doc = yaml.safe_load("jobs:\n  - name: j\n    trigger: {events: [merge.done]}\n    run: 'true'\n")
        self.assertIn("trigger", doc["jobs"][0])                       # (a bare `on:` would load as True)
        errs = self.r.validate_runs({"name": "p", "jobs": [{**doc["jobs"][0], "at": "06:00"}]}, "p",
                                    {"runtime": None, "workspace": pathlib.Path("/"), "hub": ROOT})
        self.assertTrue(any("exactly one of at, every and trigger" in e for e in errs), errs)

    def test_conversion(self):
        index = {"projects": {"p": {"name": "p", "paths": {"workspace": "/tmp/p-ws"}, "jobs": [
            {"name": "r", "trigger": {"events": ["merge.done"], "debounce": "1h"}, "run": "true"},
            {"name": "a", "trigger": {"events": ["merge.done"], "projects": ["self"]}, "kind": "agent"},
        ]}, "taken": {"name": "taken", "paths": {"workspace": "/tmp/t-ws"}}}}
        with mock.patch.object(self.r.isolation, "sandboxed", lambda doc: False), \
                mock.patch.object(hubconf, "former_names", lambda p: ["p-old", "taken"]):
            jobs, _s, _r = self.r.scheduler_jobs(index, set())
        by = {j["name"]: j for j in jobs}
        self.assertEqual(by["p/r"]["trigger"], {"events": ["merge.done"], "sources": [], "projects": ["p", "p-old"],
                                                "where": {}, "debounce_minutes": 60})
        self.assertEqual(by["p/r"]["max_attempts_per_day"], 12)
        self.assertFalse(by["p/r"]["platform"])
        self.assertNotIn("time", by["p/r"])
        self.assertTrue(by["p/a"]["background"])
        self.assertTrue(by["p/a"]["command"].startswith("RUN_AGENT_JOB_FOREGROUND=1 "))


class Publish(unittest.TestCase):
    def test_publish_stamps_the_chain_and_relays_drop_it(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("hubbus_ev", str(ROOT / "bin" / "hubbus"))
        spec = importlib.util.spec_from_loader("hubbus_ev", loader)
        hb = importlib.util.module_from_spec(spec)
        loader.exec_module(hb)
        hb.DB = pathlib.Path(tempfile.mkdtemp()) / "bus.db"
        c = hb.db()
        self.addCleanup(c.close)
        gen = c.execute("SELECT value FROM meta WHERE key='generation'").fetchone()[0]
        c2 = hb.db()
        self.assertEqual(c2.execute("SELECT value FROM meta WHERE key='generation'").fetchone()[0], gen)
        c2.close()
        with mock.patch.dict(os.environ, {"AGENT_HUB_JOB_CHAIN": '["p/a"]', "AGENT_HUB_JOB_DEPTH": "2"}):
            hb.publish(c, "x1", "merge.done", "p", "p", payload={"chain": ["root"], "depth": 3})
            hb.publish(c, "x3", "merge.done", "p", "p")
            env = hb.chain_env([{"payload": json.dumps({"chain": ["q/b", "q/c"]})},
                                {"payload": json.dumps({"chain": ["q/c", "q/d"]})}])
            self.assertEqual(json.loads(env["AGENT_HUB_JOB_CHAIN"]), ["q/b", "q/c", "q/d"])
            self.assertEqual(env["AGENT_HUB_JOB_DEPTH"], "2")
            self.assertNotIn("AGENT_HUB_JOB_CHAIN", hb.chain_env([{"payload": "{}"}]))
        hb.publish(c, "x2", "merge.done", "p", "p")
        rows = {r["id"]: json.loads(r["payload"]) for r in c.execute("SELECT id, payload FROM events")}
        self.assertEqual((rows["x1"]["chain"], rows["x1"]["depth"]), (["root", "p/a"], 3))
        self.assertEqual((rows["x3"]["chain"], rows["x3"]["depth"]), (["p/a"], 2))
        self.assertNotIn("chain", rows["x2"])
        self.assertIn("bridge", hb.RELAYS)

    def test_publish_choices_are_the_contract_types(self):
        import re
        src = (ROOT / "bin" / "hubbus").read_text()
        choices = re.search(r'pb\.add_argument\("type", choices=\(([^)]*)\)', src).group(1)
        enum = json.loads((ROOT / "contracts" / "hubbus.v1.schema.json").read_text())["properties"]["type"]["enum"]
        self.assertEqual(set(re.findall(r'"([^"]+)"', choices)),
                         {t for t in enum if t != "record" and not t.startswith("message")})


if __name__ == "__main__":
    unittest.main()


class PlatformSettings(unittest.TestCase):
    """judgment point agent-job-event (contracts/llm-points.yaml): only allow: and the platform's settings apply."""

    def spec(self):
        import yaml
        return yaml.safe_load((ROOT / "contracts" / "llm-points.yaml").read_text())["points"]["agent-job-event"]

    def test_flags(self):
        import runtime_claude
        import runtime_ports
        env = {"AGENT_JOB_ALLOW": json.dumps(["Bash(git log:*)", "WebFetch(domain:example.org)"]),
               "AGENT_JOB_PROJECT": "p"}
        task = runtime_ports.Task("agent-job-event", "x", self.spec(), env=env)
        # the project's folders (runtime_claude.project_roots): here, where the run starts (cwd None = this process's)
        roots = mock.patch.object(runtime_claude, "project_roots", lambda p: [pathlib.Path.cwd().resolve()])
        roots.start()
        self.addCleanup(roots.stop)
        with mock.patch.object(runtime_claude, "platform_settings", lambda p, *a, **k: pathlib.Path(f"/s/{p}.json")):
            cmd = runtime_claude._flags(task)
        self.assertIn("--restricted", cmd)
        self.assertEqual(cmd[cmd.index("--settings") + 1], "/s/p.json")
        self.assertEqual(cmd[cmd.index("--tools") + 1], "Bash,Glob,Grep,Read,WebFetch")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")
        self.assertIn("Bash(git log:*)", cmd)
        env2 = {**env, "AGENT_JOB_ALLOW": json.dumps(["mcp__claude_ai_x__y", "Edit(./a/**)"])}
        with mock.patch.object(runtime_claude, "platform_settings", lambda p, *a, **k: pathlib.Path(f"/s/{p}.json")):
            cmd = runtime_claude._flags(task._replace(env=env2))
        self.assertEqual(cmd[cmd.index("--tools") + 1], "Bash,Edit,Glob,Grep,Read,Write")   # no MCP names in --tools
        with self.assertRaises(ValueError):   # no project: never a run with whatever settings happen to load
            runtime_claude._flags(task._replace(env={"AGENT_JOB_ALLOW": "[]"}))

    def test_settings_file(self):
        import runtime_claude
        pol = {"other_projects": [pathlib.Path("/w/q")], "deny_write": [], "deny_read": [], "platform_commands": [],
               "platform_cli": ["agenthub send"], "write": ["/w/p"], "network": [], "credential_files": [],
               "credentials": []}
        tmp = pathlib.Path(tempfile.mkdtemp())
        for enforced in (False, True):
            doc = {"name": "p", "enforce": {"sandbox": enforced}}
            # the services-only exemption must not take the sandbox away from an agent anyone can start
            with mock.patch("policy.policy", lambda name, d, i: pol), mock.patch.object(hubconf, "STATE", tmp), \
                    mock.patch("registry.refresh", lambda: {"projects": {"p": doc}}), \
                    mock.patch.object(hubconf, "get", lambda k, d=None: ["p"] if k == "service_sandbox_exempt" else d):
                d = json.loads(runtime_claude.platform_settings("p", {}, tempfile.mkdtemp()).read_text())
            self.assertIn("Read(//w/q/**)", d["permissions"]["deny"])
            self.assertIn("Bash(agenthub send *)", d["permissions"]["allow"])
            self.assertFalse(any("merge-branch" in r or "hubbus" in r or "cap-run" in r for r in d["permissions"]["allow"]))
            self.assertEqual("sandbox" in d, enforced)
            if enforced:   # the CLI's default (true) would approve sandboxed Bash before allow: is consulted
                self.assertIs(d["sandbox"]["autoAllowBashIfSandboxed"], False)

    def test_a_runtime_that_cannot_confine_refuses(self):
        import importlib.util
        import runtime_ports
        spec = importlib.util.spec_from_file_location("llm_conf", ROOT / "bin" / "llm.py")
        llm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(llm)
        self.assertIn("confine", runtime_ports.capabilities("claude"))
        self.assertNotIn("confine", runtime_ports.capabilities("command"))
        failed = []
        a = types.SimpleNamespace(cmd="run", point="agent-job-event", cwd=None, model=None, prompt_file=None,
                                  dry_run=True, extra=[])
        with mock.patch.object(llm, "fail", lambda point, spec, why: failed.append(why) or 1), \
                mock.patch.object(runtime_ports, "default_name", lambda: "command"), \
                mock.patch("sys.stdin", io.StringIO("x")):
            self.assertEqual(llm.run(a), 1)
        self.assertIn("confine", failed[0])

    def test_registry_points_agent_event_jobs_at_it(self):
        import registry
        index = {"projects": {"p": {"name": "p", "paths": {"workspace": "/tmp/p-ws"}, "jobs": [
            {"name": "a", "trigger": {"events": ["merge.done"]}, "kind": "agent", "allow": ["Read"]}]}}}
        with mock.patch.object(registry.isolation, "sandboxed", lambda doc: False):
            cmd = registry.scheduler_jobs(index, set())[0][0]["command"]
        self.assertTrue(cmd.startswith("RUN_AGENT_JOB_FOREGROUND=1 AGENT_JOB_POINT=agent-job-event AGENT_JOB_ALLOW="))
        self.assertIn(" AGENT_JOB_PROJECT=p ", cmd)


class ConfinedMail(unittest.TestCase):
    """A confined run (event agent job) uses the mail commands only as and for its own project."""

    def setUp(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("hubbus_conf", str(ROOT / "bin" / "hubbus"))
        spec = importlib.util.spec_from_loader("hubbus_conf", loader)
        self.hb = importlib.util.module_from_spec(spec)
        loader.exec_module(self.hb)
        p = mock.patch.dict(os.environ, {"AGENT_HUB_CONFINED": "1"})
        p.start()
        self.addCleanup(p.stop)

    def refused(self, **a):
        return self.hb.confined_refusal(types.SimpleNamespace(**{"sender": None, "task": None, "project": None,
                                                                 "question": None, **a}), "p")

    def test_refusals(self):
        self.assertIsNone(self.refused(cmd="send", type="info"))
        self.assertIsNone(self.refused(cmd="send", type="question"))
        self.assertIsNone(self.refused(cmd="inbox"))
        self.assertIsNone(self.refused(cmd="send", type="info", sender="p"))
        # withdrawing its own question (send checks the question is the project's): the mailbox handler's answer to
        # reconcile-questions
        self.assertIsNone(self.refused(cmd="send", type="drop", question="m1"))
        self.assertIsNone(self.refused(cmd="send", type="done", question="m1"))
        for a in ({"cmd": "send", "type": "drop"}, {"cmd": "send", "type": "drop", "task": "k1"},
                  {"cmd": "send", "type": "drop", "task": "k1", "question": "m1"},
                  {"cmd": "send", "type": "info", "sender": "q"},
                  {"cmd": "send", "type": "manifest.update"},
                  {"cmd": "send", "type": "task"},
                  {"cmd": "send", "type": "done", "task": "k1"},
                  {"cmd": "inbox", "project": "q"},
                  {"cmd": "open-questions", "project": "q"},
                  {"cmd": "send", "type": "info", "id": "x:done"},
                  {"cmd": "publish"}, {"cmd": "record-publish"}, {"cmd": "dispatch"}):
            self.assertTrue(self.refused(**a), a)
        with self.assertRaises(self.hb.Refused):   # the CLI exits with it
            self.hb.confined_letter({"to_project": "q", "source": "r"}, "p")
        self.hb.confined_letter({"to_project": "p", "source": "r"}, "p")
        with mock.patch.dict(os.environ, {"AGENT_HUB_CONFINED": ""}):
            self.assertIsNone(self.refused(cmd="send", type="manifest.update", sender="q"))

    def test_reply_and_open_questions_stay_in_the_project(self):
        src = (ROOT / "bin" / "hubbus").read_text()
        self.assertIn("orig = must_message(c, a.reply_to)\n        confined_letter(orig, here, boxed)", src)
        self.assertIn('if names.env("CONFINED"):   # only its own project\'s questions', src)

    def test_llm_marks_confined_runs(self):
        src = (ROOT / "bin" / "llm.py").read_text()
        self.assertIn('if spec.get("settings") == "platform":\n        # a confined run', src)
        self.assertIn('names.env_set(env, "CONFINED", "1")', src)
