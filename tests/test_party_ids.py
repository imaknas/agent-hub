"""hubconf.party:Principal Handoff 的參與者識別字從 hub.json 來(principal、principal side),其他照格式產生。
規範 core R6:principal 識別字是設定的、不是宣稱的,而且跟任何參與者都不一樣。

    bin/py -m unittest discover -s tests
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"
PROBE = ("import json, hubconf; print(json.dumps([hubconf.party('principal'), hubconf.party('principal_side'), "
         "hubconf.party('requester', 'demo'), hubconf.party('agent', 'demo'), hubconf.party('registrar'), "
         "hubconf.principal_is_legal_person()]))")


def probe(hub_json: dict) -> list:
    with tempfile.TemporaryDirectory() as d:
        (pathlib.Path(d) / "hub.json").write_text(json.dumps(hub_json))
        env = {**os.environ, "AGENT_HUB_CONFIG": d}
        r = subprocess.run([sys.executable, "-c", PROBE], cwd=BIN, env=env, capture_output=True, text=True, check=True)
        return json.loads(r.stdout)


class PartyIds(unittest.TestCase):
    def test_configured(self):
        got = probe({"owner": {"address": "owner", "tag": "Me", "principal_id": "person:me", "legal_person": True},
                     "assistant": {"project": "helper", "api": "http://x", "party_id": "helper-side"}})
        self.assertEqual(got[:4], ["person:me", "helper-side", "project:demo", "project:demo"])
        self.assertTrue(got[4].endswith(":registrar"))
        self.assertTrue(got[5])

    def test_defaults(self):
        got = probe({"owner": {"address": "owner", "tag": "Me"}, "assistant": {"project": "helper", "api": "http://x"}})
        self.assertEqual(got[:2], ["person:owner", "helper"])
        self.assertFalse(got[5])

    def test_principal_is_never_a_participant(self):
        got = probe({"owner": {"address": "owner", "tag": "Me", "principal_id": "person:me"},
                     "assistant": {"project": "helper", "api": "http://x"}})
        self.assertNotIn(got[0], got[1:5])

    def test_requester_needs_a_project(self):
        r = subprocess.run([sys.executable, "-c", "import hubconf; hubconf.party('requester')"], cwd=BIN,
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
