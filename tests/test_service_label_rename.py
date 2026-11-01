"""Renaming a platform service's label (projects/agent-hub.yaml): render-services must bring up the new label and take
down the old one it generated, leaving nothing behind. Plans only: nothing is loaded, launchd is never asked.

    bin/py -m unittest tests.test_service_label_rename
"""
import importlib.util
import json
import pathlib
import plistlib
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
OLD = "local.sync-all-memory-mini"


def load():
    spec = importlib.util.spec_from_file_location("render_services_t", ROOT / "bin" / "render-services.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class LabelRename(unittest.TestCase):
    def test_the_platforms_memory_sync_label(self):
        m = load()
        doc = yaml.safe_load((ROOT / "projects" / "agent-hub.yaml").read_text())
        svc = next(s for s in doc["services"] if s["name"] == "sync-all-memory")
        self.assertEqual(m.label_of("agent-hub", svc), "local.agent-hub.sync-all-memory",
                         "the default label: no machine name in it")

    def test_a_renamed_label_replaces_the_old_one(self):
        m = load()
        tmp = pathlib.Path(tempfile.mkdtemp())
        new = "local.agent-hub.sync-all-memory"
        plist = {"Label": new, "ProgramArguments": ["/bin/true"], "StartInterval": 3600}
        (tmp / f"{OLD}.plist").write_bytes(plistlib.dumps({**plist, "Label": OLD}))
        (tmp / "rendered.json").write_text(json.dumps([OLD]))
        with mock.patch.object(m, "AGENTS", tmp), mock.patch.object(m, "RENDERED", tmp / "rendered.json"), \
                mock.patch.object(m.hubconf, "service_manager", lambda: "launchd"):
            changes = [(c.op, c.target) for c, _cur, _plist in m._changes({new: ("agent-hub", plist)}, set())]
        self.assertEqual(changes, [("add", f"service:{new}"), ("remove", f"service:{OLD}")])


if __name__ == "__main__":
    unittest.main()
