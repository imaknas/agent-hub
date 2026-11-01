"""Project v2 (contracts/project.v2.schema.json, bin/registry.py validate_v2; docs/design/test-waiting.md B1, step
2.1): tests leave runtime for a top-level tests list with code and inputs. Only the schema and its validation exist
yet: the live validators keep refusing v2 until the runner and the readers switch over (step 2.3).

    bin/py -m unittest discover -s tests
"""
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hubconf  # noqa: E402
import minischema  # noqa: E402
import registry  # noqa: E402

SCHEMA = json.loads((ROOT / "contracts" / "project.v2.schema.json").read_text())


class V2(unittest.TestCase):
    def setUp(self):
        self.ws = pathlib.Path(tempfile.mkdtemp()).resolve()
        # a config of its own (registry.dirs needs services_root for a project with a runtime), for this test only
        (self.ws / "cfg" / "services" / "demo").mkdir(parents=True)
        (self.ws / "cfg" / "hub.json").write_text(json.dumps({"services_root": str(self.ws / "cfg" / "services")}))
        self.saved = os.environ.get("AGENT_HUB_CONFIG"), hubconf._cfg
        os.environ["AGENT_HUB_CONFIG"] = str(self.ws / "cfg")
        hubconf._cfg = None

    def tearDown(self):
        env, hubconf._cfg = self.saved
        if env is None:
            os.environ.pop("AGENT_HUB_CONFIG", None)
        else:
            os.environ["AGENT_HUB_CONFIG"] = env
        shutil.rmtree(self.ws, ignore_errors=True)

    def doc(self, **extra) -> dict:
        d = {"v": 2, "name": "demo", "paths": {"workspace": str(self.ws)},
             "code": ["src/**", "tests/**", "uv.lock"], "inputs": ["cmd:sh -c true", "env:TZ", "config:private-names.txt"],
             "tests": [{"tier": "full", "run": "sh -c 'exit 0' -n {jobs}", "when": ["merge", "nightly", "deploy"]},
                       {"tier": "e2e", "run": "sh -c 'exit 0'", "paths": ["src/web/**"], "when": ["merge"],
                        "jobs": {"max": 8, "env": "WORKERS"}}]}
        d.update(extra)
        return d

    def errs(self, **extra) -> list[str]:
        return registry.validate_v2(self.doc(**extra), "demo")

    def test_valid_document(self):
        self.assertEqual(self.errs(), [])
        self.assertEqual(minischema.errors(SCHEMA, self.doc()), [])

    def test_commit_tier_needs_the_commit_gate(self):
        lint = {"tier": "lint", "run": "sh -c true", "when": ["commit", "merge"]}
        self.assertTrue(any("commit_gate" in e for e in self.errs(tests=[lint])))
        self.assertEqual(self.errs(tests=[lint], commit_gate=True), [])

    def test_runtime_keeps_only_deploy(self):
        errs = self.errs(runtime={"test": "sh -c true"})
        self.assertTrue(any("deploy" in e and "test" in e for e in errs), errs)
        self.assertNotEqual(minischema.errors(SCHEMA, self.doc(runtime={"test": "x"})), [])

    def test_the_live_validator_still_refuses_v2(self):
        self.assertTrue(any("v" in e and "1" in e for e in registry.validate(self.doc(), "demo")))
        self.assertTrue(any("2" in e for e in registry.validate_v2({**self.doc(), "v": 1}, "demo")))

    def test_inputs(self):
        for bad in ("file:/etc/passwd", "file:../other/x", "config:sub/x.txt", "env:lower", "url:x", "cmd:"):
            with self.subTest(bad=bad):
                self.assertNotEqual(self.errs(inputs=[bad]), [])
        self.assertEqual(self.errs(inputs=["file:tools/version.txt", "cmd:sh -c true"]), [])

    def test_tiers(self):
        ok = {"tier": "full", "run": "sh -c true", "when": ["merge"]}
        cases = {
            "duplicate name": [ok, ok],
            "no when": [{"tier": "a", "run": "sh -c true"}],
            "unknown moment": [{**ok, "when": ["push"]}],
            "unknown field": [{**ok, "repo": "x"}],
            "bad name": [{**ok, "tier": "Full"}],
            "run not found": [{**ok, "run": "no-such-command-anywhere"}],
            "max without a way to pass it": [{**ok, "jobs": {"max": 2}}],
            "max not a number": [{**ok, "run": "sh -c true {jobs}", "jobs": {"max": True}}],
            "paths not patterns": [{**ok, "paths": "src/**"}],
        }
        for what, tiers in cases.items():
            with self.subTest(what=what):
                self.assertNotEqual(self.errs(tests=tiers), [])

    def test_unknown_top_level_field(self):
        self.assertTrue(any("testz" in e for e in self.errs(testz=[])))


if __name__ == "__main__":
    unittest.main()
