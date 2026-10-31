"""The instance directory layout (hubconf, docs/design/new-user.md 3.1): no .hub-home = the old layout (everything in
the hub root), which protects machines not yet moved; a pointer file (or AGENT_HUB_HOME) = <instance>/config, data,
secrets; the individual AGENT_HUB_CONFIG/DATA/SECRETS can override further.
.state and .logs are always in the hub root (one per machine). The shell's bin/hubpaths.sh must compute the same
locations.

    bin/py -m unittest discover -s tests
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROBE = ("import hubconf as h, json; print(json.dumps({k: str(v) for k, v in {'config': h.config_dir(), 'data': h.data_dir(), "
         "'secrets': h.secrets_dir(), 'state': h.STATE, 'logs': h.LOGS, 'hub_json': h.config_file('hub.json'), "
         "'registry': h.registry_dir(), 'alert': h.path('.secrets/discord-token')}.items()}))")


class Layout(unittest.TestCase):
    def setUp(self):
        self.hub = pathlib.Path(tempfile.mkdtemp())
        (self.hub / "bin").mkdir()
        for f in ("hubconf.py", "i18n.py", "hubpaths.sh", "scheduler-watchdog.py"):
            shutil.copy(ROOT / "bin" / f, self.hub / "bin")

    def tearDown(self):
        shutil.rmtree(self.hub, ignore_errors=True)

    def env(self, **extra):
        e = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_HUB")}
        return {**e, "AGENT_HUB_ROOT": str(self.hub), **extra}

    def py(self, **extra) -> dict:
        r = subprocess.run([sys.executable, "-c", PROBE], cwd=self.hub / "bin", env=self.env(**extra),
                           capture_output=True, text=True, check=True)
        return json.loads(r.stdout)

    def sh(self, **extra) -> dict:
        r = subprocess.run(["bash", "-c", 'source bin/hubpaths.sh; echo "$HUB_CONFIG|$HUB_DATA|$HUB_SECRETS|$HUB_JSON"'],
                           cwd=self.hub, env=self.env(**extra), capture_output=True, text=True, check=True)
        c, d, s, j = r.stdout.strip().split("|")
        return {"config": c, "data": d, "secrets": s, "hub_json": j}

    def test_no_pointer_is_todays_layout(self):
        h = str(self.hub)
        want = {"config": h, "data": h, "secrets": f"{h}/.secrets", "state": f"{h}/.state", "logs": f"{h}/.logs",
                "hub_json": f"{h}/hub.json", "registry": f"{h}/projects", "alert": f"{h}/.secrets/discord-token"}
        self.assertEqual(self.py(), want)
        self.assertEqual(self.sh(), {k: want[k] for k in ("config", "data", "secrets", "hub_json")})

    def test_pointer_moves_config_data_secrets_only(self):
        inst = self.hub / "inst"
        (self.hub / ".hub-home").write_text(f"{inst}\n")
        h = str(self.hub)
        want = {"config": f"{inst}/config", "data": f"{inst}/data", "secrets": f"{inst}/secrets", "state": f"{h}/.state",
                "logs": f"{h}/.logs", "hub_json": f"{inst}/config/hub.json", "registry": f"{inst}/config/projects",
                "alert": f"{inst}/secrets/discord-token"}
        self.assertEqual(self.py(), want)
        self.assertEqual(self.sh(), {k: want[k] for k in ("config", "data", "secrets", "hub_json")})

    def test_watchdog_agrees_with_hubconf(self):
        """The watchdog deliberately does not import hubconf and computes its own; both must agree (in the old layout
        and with a pointer file)."""
        probe = ("import importlib.util as u; s = u.spec_from_file_location('w', 'scheduler-watchdog.py'); "
                 "m = u.module_from_spec(s); s.loader.exec_module(m); print(m.CONFIG_DIR, m.SECRETS_DIR)")
        for pointer in (None, self.hub / "inst"):
            if pointer:
                (self.hub / ".hub-home").write_text(f"{pointer}\n")
            r = subprocess.run([sys.executable, "-c", probe], cwd=self.hub / "bin", env=self.env(),
                               capture_output=True, text=True, check=True)
            got = self.py()
            self.assertEqual(r.stdout.split(), [got["config"], got["secrets"]])

    def test_single_overrides(self):
        got = self.py(AGENT_HUB_CONFIG="/x/cfg")
        self.assertEqual((got["config"], got["data"]), ("/x/cfg", str(self.hub)))
        self.assertEqual(self.sh(AGENT_HUB_CONFIG="/x/cfg")["config"], "/x/cfg")


if __name__ == "__main__":
    unittest.main()
