"""Contract tests for appliers (bin/appliers.py, docs/design/new-user.md 3.7): every one registered in APPLIERS runs
the same checks: ① plan has no side effects (the files it manages are the same before and after) ② what apply returns
== what plan just returned ③ plan after apply is empty (idempotent).
An applier in the registry without a matching scenario setup fails the test outright: a new applier cannot skip the
contract tests.

Each scenario is a fake registry + a temp directory; launchd is replaced with a fake launchctl that only records, and
real services are never touched.

    bin/py -m unittest discover -s tests
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import appliers  # noqa: E402


def snapshot(d: pathlib.Path) -> dict:
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


class SchedulerFixture:
    def setup(self, m, tmp: pathlib.Path):
        self._snap = m.SNAPSHOT
        m.SNAPSHOT = tmp / "state" / "jobs.json"
        return {"v": 1, "errors": {}, "projects": {"demo": {
            "name": "demo", "paths": {"workspace": str(tmp / "ws")},
            "jobs": [{"name": "nightly", "at": "03:00", "run": "/bin/echo hi"},
                     {"name": "often", "every": "15m", "run": "/bin/echo hi", "after": ["nightly"]}]}}}

    def teardown(self, m):
        m.SNAPSHOT = self._snap


class ServicesFixture:
    def setup(self, m, tmp: pathlib.Path):
        self._saved = (m.AGENTS, m.RENDERED, m.launchctl, m.hubconf.service_manager, m.hubconf.cli_folder)
        # tests the launchd path (launchctl is fake), whatever system it runs on
        m.hubconf.service_manager = lambda: "launchd"
        m.hubconf.cli_folder = lambda: tmp / "shared" / "cli"   # the wrapper apply writes: never the live hub's
        m.AGENTS, m.RENDERED = tmp / "LaunchAgents", tmp / "state" / "rendered-services.json"
        m.AGENTS.mkdir(parents=True)
        self.calls, loaded = [], set()

        def fake(*args):
            # a launchd that remembers what is loaded: bootstrap loads the plist's label, bootout unloads it, print
            # answers whether it is loaded (render-services reloads a unit that isn't)
            self.calls.append(args)
            label = pathlib.Path(args[-1]).stem if args[0] == "bootstrap" else args[-1].rsplit("/", 1)[-1]
            if args[0] == "bootstrap":
                loaded.add(label)
            elif args[0] == "bootout":
                loaded.discard(label)
            return subprocess.CompletedProcess(args, 0 if args[0] != "print" or label in loaded else 1, "", "")
        m.launchctl = fake
        (tmp / "ws").mkdir()
        return {"v": 1, "errors": {}, "projects": {"demo": {
            "name": "demo", "paths": {"workspace": str(tmp / "ws")},
            "services": [{"name": "web", "run": "/bin/echo hi", "keepalive": True, "log": "web.log"}]}}}

    def teardown(self, m):
        m.AGENTS, m.RENDERED, m.launchctl, m.hubconf.service_manager, m.hubconf.cli_folder = self._saved


class AgentSettingsFixture:
    def setup(self, m, tmp: pathlib.Path):
        self._rendered = m.RENDERED
        m.RENDERED = tmp / "state" / "rendered-settings"
        (tmp / "ws").mkdir()
        return {"v": 1, "errors": {}, "projects": {"demo": {
            "name": "demo", "paths": {"workspace": str(tmp / "ws")}, "enforce": {"sandbox": True}}}}

    def teardown(self, m):
        m.RENDERED = self._rendered


FIXTURES = {"scheduler": SchedulerFixture, "services": ServicesFixture, "agent-settings": AgentSettingsFixture}


class Registry(unittest.TestCase):
    def test_every_applier_has_a_fixture(self):
        self.assertEqual(set(appliers.APPLIERS), set(FIXTURES),
                         "a new applier needs its scenario setup here to count as having run the contract tests")

    def test_load_rejects_incomplete_module(self):
        with tempfile.TemporaryDirectory() as d:
            bad = pathlib.Path(d) / "bad.py"
            bad.write_text("def plan(index=None):\n    return []\n")
            appliers.APPLIERS["broken"] = str(bad)
            try:
                with self.assertRaises(appliers.MissingFunctions):
                    appliers.load("broken")
            finally:
                del appliers.APPLIERS["broken"]
                appliers._loaded.pop("broken", None)


class Contract(unittest.TestCase):
    def each(self, check):
        for name in appliers.APPLIERS:
            with self.subTest(applier=name), tempfile.TemporaryDirectory() as d:
                tmp = pathlib.Path(d)
                m = appliers.load(name)
                fx = FIXTURES[name]()
                index = fx.setup(m, tmp)
                try:
                    check(m, index, tmp, fx)
                finally:
                    fx.teardown(m)

    def test_plan_has_no_side_effects(self):
        def check(m, index, tmp, fx):
            before = snapshot(tmp)
            first = m.plan(index)
            self.assertEqual(snapshot(tmp), before, "plan changed files")
            self.assertTrue(first, "the scenario should have something to apply (or the contract is not tested)")
            self.assertEqual(m.plan(index), first, "two plans gave different results")
            if hasattr(fx, "calls"):
                self.assertFalse([c for c in fx.calls if c[0] in ("bootstrap", "bootout")], "plan touched launchd")
        self.each(check)

    def test_apply_matches_plan_and_is_idempotent(self):
        def check(m, index, tmp, fx):
            planned = m.plan(index)
            self.assertEqual(m.apply(index), planned, "apply did something other than what plan said")
            self.assertEqual(m.plan(index), [], "plan after apply should be empty")
            self.assertEqual(m.apply(index), [], "a second apply must change nothing more")
        self.each(check)

    def test_changes_are_neutral(self):
        def check(m, index, tmp, fx):
            for c in m.plan(index):
                self.assertIsInstance(c, appliers.Change)
                self.assertIn(c.op, appliers.OPS)
                self.assertRegex(c.target, r"^(service|agent-settings|hook|job):")
                self.assertNotIn(".claude", c.target + c.detail,
                                 "a Change does not name the runtime's settings location")
            self.assertIsInstance(m.EXTERNAL, bool)
        self.each(check)


class ServiceNotLoaded(unittest.TestCase):
    """A generated unit whose file is right but which the service manager isn't running (11-02: unloaded behind its
    back for 14 hours while every check said "matches") is a change: planned, and loaded again by apply."""

    def test_planned_and_reloaded(self):
        m, fx = appliers.load("services"), ServicesFixture()
        index = fx.setup(m, pathlib.Path(tempfile.mkdtemp()))
        self.addCleanup(fx.teardown, m)
        m.apply(index)
        self.assertEqual(m.plan(index), [])
        m.launchctl("bootout", "gui/0/local.demo.web")   # something else unloads it
        self.assertEqual([(c.op, c.target) for c in m.plan(index)], [("update", "service:local.demo.web")])
        m.apply(index)
        self.assertEqual(m.plan(index), [])
        self.assertIn(("bootstrap", f"gui/{__import__('os').getuid()}", str(m.AGENTS / "local.demo.web.plist")),
                      fx.calls[-3:])


class ServiceManagerHome(unittest.TestCase):
    """With a HOME that isn't the account's (a test's, a sandbox's), the platform never touches the account's units."""

    def test_another_home_has_no_service_manager_and_says_why(self):
        import hubconf
        from unittest import mock
        for home, want in ((pathlib.Path(tempfile.mkdtemp()), None), (None, "launchd")):
            with self.subTest(home=home), mock.patch.object(hubconf, "_SERVICE_MANAGER", []), \
                    mock.patch.object(hubconf.sys, "platform", "darwin"):
                real = pathlib.Path(__import__("pwd").getpwuid(__import__("os").getuid()).pw_dir)
                with mock.patch.object(pathlib.Path, "home", lambda: home or real):
                    self.assertEqual(hubconf.service_manager(), want)
                    if want is None:
                        self.assertIn(str(home), hubconf.no_service_manager())


if __name__ == "__main__":
    unittest.main()
