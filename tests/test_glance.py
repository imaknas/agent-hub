"""brunna glance (bin/glance.py, docs/design/herdr.md): one look at what needs the owner and the platform's health, in
the glance.v1 contract, for status bars and other clients. It reads only: nothing is created on an instance that isn't
set up, it answers while another process holds the bus's write lock, and run from inside one project's folder it still
shows every project. What the status page's tick writes (write_glance) is the alert list it showed, unchanged.

    bin/py -m unittest tests.test_glance
"""
import importlib.util
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import contracts  # noqa: E402
import names  # noqa: E402

SCHEMA = ROOT / "contracts" / "glance.v1.schema.json"


class Glance(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        for d in ("home", "config/projects", "data", "secrets", "bus", "state", "ws/app", "ws/box"):
            (self.tmp / d).mkdir(parents=True)
        self.env = {**names.env_strip(os.environ), "HOME": str(self.tmp / "home"),
                    "AGENT_HUB_CONFIG": str(self.tmp / "config"), "AGENT_HUB_DATA": str(self.tmp / "data"),
                    "AGENT_HUB_SECRETS": str(self.tmp / "secrets"), "AGENT_HUB_STATE": str(self.tmp / "state"),
                    "AGENT_HUB_LANG_UI": "en"}
        for py in ("AGENT_HUB_PYTHON", "BRUNNA_PYTHON"):
            if os.environ.get(py):
                self.env[py] = os.environ[py]

    def set_up(self):
        (self.tmp / "config" / "hub.json").write_text(json.dumps({"v": 1, "owner": {"tag": "Alex"},
                                                                  "bus_dir": str(self.tmp / "bus"),
                                                                  "registry_dir": "projects"}))
        for name in ("app", "box"):
            (self.tmp / "config" / "projects" / f"{name}.yaml").write_text(
                f"v: 1\nname: {name}\npaths:\n  workspace: {self.tmp / 'ws' / name}\n")

    def run_(self, script, *args, cwd=None):
        return subprocess.run([sys.executable, str(ROOT / "bin" / script), *args], env=self.env, cwd=cwd or self.tmp,
                              capture_output=True, text=True, timeout=120)

    def glance(self, cwd=None):
        r = self.run_("glance.py", "--json", cwd=cwd)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(contracts.check_json(SCHEMA, out), [], out)
        return out

    def test_the_example_is_valid(self):
        example = json.loads((ROOT / "contracts" / "fixtures" / "glance.v1.example.json").read_text())
        self.assertEqual(contracts.check_json(SCHEMA, example), [])

    def test_not_set_up_touches_nothing(self):
        self.assertEqual(self.glance(), {"v": 1, "set_up": False})
        self.assertEqual(list((self.tmp / "bus").iterdir()), [], "no bus made")

    def test_mail_for_every_project_from_inside_one(self):
        self.set_up()
        for to in ("app", "app", "box"):
            r = self.run_("hubbus", "send", "--from", "box", "--to", to, "--type", "info", "--text", "hello")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        g = self.glance(cwd=self.tmp / "ws" / "app")
        self.assertEqual(g["mail"]["total"], 3)
        self.assertEqual({p["project"]: p["open"] for p in g["mail"]["projects"]}, {"app": 2, "box": 1})
        self.assertEqual(g["health"]["state"], "unknown", "no tick has written the status page's part yet")
        self.assertEqual(g["status_page"]["local_to"], "hub")

    def test_answers_while_the_bus_is_being_written(self):
        self.set_up()
        self.run_("hubbus", "send", "--from", "box", "--to", "app", "--type", "info", "--text", "hello")
        c = sqlite3.connect(self.tmp / "bus" / "bus.db", timeout=1)
        c.execute("BEGIN IMMEDIATE")   # a writer holds the lock
        try:
            self.assertEqual(self.glance()["mail"]["total"], 1)
        finally:
            c.rollback()
            c.close()

    def test_the_ticks_part(self):
        """write_glance stores the alert list the page showed as it is; glance reports it, with its time, and the
        owner's open items."""
        self.set_up()
        spec = importlib.util.spec_from_file_location("gsp_glance", ROOT / "bin" / "gen-status-page.py")
        gsp = importlib.util.module_from_spec(spec)
        old = dict(os.environ)
        os.environ.update(self.env)
        try:
            spec.loader.exec_module(gsp)
            import datetime
            from unittest import mock
            alerts = [("bad", "app/nightly failed"), ("notify", "no alerts set up")]
            items = [{"kind": "task", "project": "app", "key": "task-app-1", "text": "Renew it", "since": "2026-11-01"}]
            with mock.patch.dict(sys.modules, {"owner": mock.Mock(open_items=lambda: items)}), \
                    mock.patch.object(gsp, "GLANCE", self.tmp / "state" / "status" / "glance.json"):
                gsp.write_glance(datetime.datetime(2026, 11, 3, 15, 0), alerts)
        finally:
            os.environ.clear()
            os.environ.update(old)
        g = self.glance()
        self.assertEqual([(a["level"], a["text"]) for a in g["health"]["alerts"]], alerts)
        self.assertEqual(g["health"]["state"], "problem")
        self.assertTrue(g["health"]["as_of"].startswith("2026-11-03T15:00"))
        self.assertEqual(g["needs_you"], [{"kind": "todo", "project": "app", "id": "task-app-1", "text": "Renew it",
                                           "since": "2026-11-01"}])

    def test_a_set_status_page_url_is_reported(self):
        self.set_up()
        cfg = json.loads((self.tmp / "config" / "hub.json").read_text())
        cfg["status_page"] = {"url": "https://hub.example.ts.net/"}
        (self.tmp / "config" / "hub.json").write_text(json.dumps(cfg))
        self.assertEqual(self.glance()["status_page"], {"url": "https://hub.example.ts.net/", "local_to": None})


if __name__ == "__main__":
    unittest.main()
