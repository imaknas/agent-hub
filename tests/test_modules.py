"""Modules (docs/design/new-user.md 3.2): before: becomes the other side's after:, jobs of a module that is off are
treated as nonexistent, cycles are blocked, a module whose requires_config is not set is not adopted; the platform's
own modules/*.yaml must all be valid.

    bin/py -m unittest discover -s tests
"""
import datetime
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import i18n  # noqa: E402
import registry  # noqa: E402
import scheduler  # noqa: E402


def index(modules: dict | None = None, core_jobs: list | None = None) -> dict:
    core = {"name": "core", "paths": {"workspace": "/tmp"},
            "jobs": core_jobs if core_jobs is not None else [
                {"name": "librarian", "at": "03:00", "run": "/bin/echo"},
                {"name": "todo", "at": "03:00", "after": ["librarian"], "run": "/bin/echo"}]}
    return {"projects": {"core": core}, "modules": modules or {}, "errors": {}}


def module(name: str, jobs: list) -> dict:
    return {"v": 1, "name": name, "type": "module", "description": "x", "jobs": jobs}


ASSISTANT = module("assistant", [{"name": "tasks", "at": "03:00", "after": ["core/librarian"],
                                  "before": ["core/todo"], "run": "/bin/echo"}])


class Before(unittest.TestCase):
    def jobs(self, idx):
        return {j["name"]: j for j in registry.scheduler_jobs(idx, set())[0]}

    def test_before_becomes_the_targets_after(self):
        js = self.jobs(index({"assistant": ASSISTANT}))
        self.assertEqual(js["core/todo"]["after"], ["core/librarian", "assistant/tasks"])
        self.assertEqual(js["assistant/tasks"]["after"], ["core/librarian"])

    def test_module_off_means_its_jobs_do_not_exist(self):
        js = self.jobs(index())
        self.assertNotIn("assistant/tasks", js)
        self.assertEqual(js["core/todo"]["after"], ["core/librarian"],
                         "the core does not know about modules: one that is off is as if it did not exist")

    def test_before_to_a_missing_job_is_ignored(self):
        m = module("x", [{"name": "a", "at": "03:00", "before": ["nope/job"], "run": "/bin/echo"}])
        self.assertIn("x/a", self.jobs(index({"x": m})))

    def test_scheduler_does_not_wait_for_a_job_that_is_not_there(self):
        """after: pointing at a job that does not exist (its module is off): the scheduler does not wait."""
        now = datetime.datetime(2026, 10, 27, 3, 5)
        todo = {"name": "core/todo", "time": "03:00", "after": ["assistant/tasks"]}
        self.assertIsNone(scheduler.waiting_on(todo, {"core/todo": todo}, {}, set(), now, "2026-10-27"))


class Cycles(unittest.TestCase):
    def test_cycle_is_reported(self):
        m = module("loop", [{"name": "a", "at": "03:00", "after": ["core/todo"], "before": ["core/librarian"],
                             "run": "/bin/echo"}])
        cyc = registry.job_cycles(index({"loop": m}))
        self.assertTrue(cyc)
        self.assertIn("loop/a", cyc[0])

    def test_no_cycle(self):
        self.assertEqual(registry.job_cycles(index({"assistant": ASSISTANT})), [])


class Validate(unittest.TestCase):
    def test_requires_config(self):
        m = module("needs", [])
        m["requires_config"] = ["no_such_key.at_all"]
        self.assertTrue(any("no_such_key.at_all" in e for e in registry.validate_module(m, "needs")))

    def test_module_jobs_run_in_the_hub(self):
        m = module("w", [{"name": "a", "at": "03:00", "cwd": "workspace", "run": "/bin/echo"}])
        self.assertTrue(any("cwd" in e for e in registry.validate_module(m, "w")))

    @mock.patch.dict("os.environ", {"AGENT_HUB_LANG_UI": "zh-Hant"})
    def test_shipped_modules_are_valid(self):
        import yaml
        # a requires_config key this test's settings do not have is fine: the fixed part before the key
        unset = i18n.translate("registry", "{key} must be set in hub.json first", {"key": "{key}"},
                               "zh-Hant").split("{key}")[0]
        self.assertTrue(unset)
        for f in sorted((ROOT / "modules").glob("*.yaml")):
            doc = yaml.safe_load(f.read_text())
            errs = [e for e in registry.validate_module(doc, f.stem) if not e.startswith(unset)]
            with self.subTest(module=f.stem):
                self.assertEqual(errs, [])


if __name__ == "__main__":
    unittest.main()
