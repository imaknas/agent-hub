"""hubconf.bus_db() moves an old-layout bus into bus_dir and points <root>/.logs/hubbus.log at the bus's log. That is a
change to the root's live layout, so it may happen only for the root's own instance (the one <root>/.hub-home names,
nothing overridden): a throwaway world, an eval run or a test that points AGENT_HUB_HOME / AGENT_HUB_CONFIG /
AGENT_HUB_STATE at a temp instance while running the live checkout's code must leave the live link, the live log and
the live bus alone.

Everything here is a temp copy: the "live" root is a temp hub with its own .hub-home, .state and .logs.

    bin/py -m unittest tests.test_bus_log_link
"""
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import names  # noqa: E402

PROBE = "import hubconf; print(hubconf.bus_db())"


def instance(at: pathlib.Path, bus: pathlib.Path) -> pathlib.Path:
    (at / "config").mkdir(parents=True)
    (at / "config" / names.current("config_file")).write_text(json.dumps({"bus_dir": str(bus)}))
    return at


def old_bus(state: pathlib.Path) -> pathlib.Path:
    """A bus at the old place, <state>/bus/bus.db, with one row to recognise it by."""
    (state / "bus").mkdir(parents=True, exist_ok=True)
    db = state / "bus" / "bus.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE t (x)")
    c.execute("INSERT INTO t VALUES ('old')")
    c.commit()
    c.close()
    return db


class BusLogLink(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.hub = self.tmp / "hub"   # stands in for the live checkout
        (self.hub / "bin").mkdir(parents=True)
        for f in ("hubconf.py", "i18n.py", "names.py", "gitdirs.py"):
            shutil.copy(ROOT / "bin" / f, self.hub / "bin")
        self.live_bus = self.tmp / "live-bus"
        live = instance(self.tmp / "live", self.live_bus)
        (self.hub / ".hub-home").write_text(f"{live}\n")
        self.live_bus.mkdir()
        (self.live_bus / "hubbus.log").write_text("live\n")
        (self.hub / ".logs").mkdir()
        self.link = self.hub / ".logs" / "hubbus.log"
        self.link.symlink_to(self.live_bus / "hubbus.log")
        self.temp_bus = self.tmp / "temp-bus"
        self.temp = instance(self.tmp / "temp", self.temp_bus)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def bus_db(self, **extra) -> pathlib.Path:
        e = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_HUB", "BRUNNA_"))}
        r = subprocess.run([sys.executable, "-c", PROBE], cwd=self.hub / "bin",
                           env={**e, "AGENT_HUB_ROOT": str(self.hub), **extra}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return pathlib.Path(r.stdout.strip())

    def assert_live_link(self):
        self.assertEqual(os.readlink(self.link), str(self.live_bus / "hubbus.log"))

    def test_temp_instance_leaves_live_link_and_bus(self):
        """The 11-09 hazard: the live root, a temp instance (its bus_dir elsewhere), the live .state still holding an
        old-layout bus. Nothing of the live root may move."""
        live_old = old_bus(self.hub / ".state")
        self.assertEqual(self.bus_db(AGENT_HUB_HOME=str(self.temp)), self.temp_bus / "bus.db")
        self.assert_live_link()
        self.assertTrue(live_old.exists(), "the live old bus was moved into the temp bus_dir")
        self.assertFalse((self.temp_bus / "bus.db").exists())

    def test_temp_config_and_state_leave_live_link(self):
        """A temp state with an old-layout bus and a temp config (an eval run's own copy) under the live root."""
        state = self.tmp / "temp-state"
        old_bus(state)
        self.bus_db(AGENT_HUB_CONFIG=str(self.temp / "config"), AGENT_HUB_STATE=str(state))
        self.assert_live_link()

    def test_temp_state_leaves_live_log_file(self):
        """The oldest layout kept the log as a plain file in .logs: a temp state's bus.db must not carry it off."""
        self.link.unlink()
        self.link.write_text("live log\n")
        state = self.tmp / "temp-state"
        state.mkdir()
        c = sqlite3.connect(state / "bus.db")
        c.execute("CREATE TABLE t (x)")
        c.close()
        self.bus_db(AGENT_HUB_HOME=str(self.temp), AGENT_HUB_STATE=str(state))
        self.assertFalse(self.link.is_symlink())
        self.assertEqual(self.link.read_text(), "live log\n")

    def test_own_instance_still_migrates_and_links(self):
        """The root's own instance (no overrides, or AGENT_HUB_HOME naming the same instance): the old bus moves into
        bus_dir and the link points at its log."""
        for extra in ({}, {"AGENT_HUB_HOME": (self.hub / ".hub-home").read_text().strip()}):
            with self.subTest(extra=extra):
                self.link.unlink(missing_ok=True)
                shutil.rmtree(self.hub / ".state", ignore_errors=True)
                shutil.rmtree(self.live_bus, ignore_errors=True)
                old_bus(self.hub / ".state")
                new = self.bus_db(**extra)
                self.assertEqual(new, self.live_bus / "bus.db")
                self.assertEqual(sqlite3.connect(new).execute("SELECT x FROM t").fetchone(), ("old",))
                self.assertTrue((self.hub / ".state" / "bus" / "bus.db.moved").exists())
                self.assert_live_link()


if __name__ == "__main__":
    unittest.main()
