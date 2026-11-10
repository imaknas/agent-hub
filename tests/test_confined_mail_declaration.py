"""The mail keys of the judgment points and the registry's mail: (docs/design/confined-mail-tools.md M1).

- llm-points: mail (tools | none) on every settings: platform point and no other; mail_from_env and letters_env only with
  mail: tools; every mail: tools point declares tool_servers: allowed_only.
- A mail handler's run doesn't start without letters, or with an id that isn't one.
- The registry: mail: true only on a scheduled agent job with allow:; it puts AGENT_JOB_MAIL=1 on the job's command.

    bin/py -m unittest tests.test_confined_mail_declaration
"""
import importlib.machinery
import importlib.util
import pathlib
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

try:
    import yaml
except ImportError:
    yaml = None


def load_llm():
    spec = importlib.util.spec_from_file_location("llm_mail_decl", ROOT / "bin" / "llm.py")
    llm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(llm)
    return llm


def points() -> dict:
    return yaml.safe_load((ROOT / "contracts" / "llm-points.yaml").read_text())["points"]


@unittest.skipIf(yaml is None, "needs PyYAML")
class Declarations(unittest.TestCase):
    def test_the_points(self):
        p = points()
        want = {"mailbox-escalation": ("tools", "MAILBOX_LETTERS", None, "untrusted"),
                "mailbox-conversation": ("tools", "MAILBOX_LETTERS", None, "untrusted"),
                "agent-job-event": ("none", None, None, "untrusted"),
                "agent-job-confined": ("tools", None, "AGENT_JOB_MAIL", None)}
        for name, (mail, letters, opt_in, source) in want.items():
            with self.subTest(name):
                spec = p[name]
                self.assertEqual((spec.get("mail"), spec.get("letters_env"), spec.get("mail_from_env"),
                                  spec.get("prompt_source")), (mail, letters, opt_in, source))
        confined = {n for n, s in p.items() if s.get("settings") == "platform"}
        self.assertEqual(confined, set(want), "every settings: platform point declares its mail")

    def test_the_lint(self):
        llm = load_llm()
        self.assertEqual([b for n, s in points().items() for b in llm.mail_problems(n, s)], [])
        base = {"settings": "platform", "tool_servers": "allowed_only"}
        for spec in ({**base}, {**base, "mail": "maybe"}, {"mail": "none"},
                     {**base, "mail": "none", "letters_env": "X"}, {**base, "mail": "none", "mail_from_env": "X"},
                     {"settings": "platform", "mail": "tools"}):
            with self.subTest(spec):
                self.assertEqual(len(llm.mail_problems("x", spec)), 1)
        self.assertEqual(llm.mail_problems("x", {**base, "mail": "tools", "letters_env": "L", "mail_from_env": "M"}), [])

    def test_the_letter_id_shape_is_hubbus_own(self):
        loader = importlib.machinery.SourceFileLoader("hubbus_mail_decl", str(ROOT / "bin" / "hubbus"))
        spec = importlib.util.spec_from_loader("hubbus_mail_decl", loader)
        hubbus = importlib.util.module_from_spec(spec)
        loader.exec_module(hubbus)
        llm = load_llm()
        self.assertEqual("^" + llm.EVENT_ID.pattern + "$", hubbus.EVENT_ID.pattern)


@unittest.skipIf(yaml is None, "needs PyYAML")
class Letters(unittest.TestCase):
    def refusal(self, letters):
        import registry
        import runtime_fake
        llm = load_llm()
        spec = points()["mailbox-escalation"]
        env = {"MAILBOX_PROJECT": "p"} | ({} if letters is None else {"MAILBOX_LETTERS": letters})
        with mock.patch.object(registry, "refresh", lambda: {"projects": {"p": {"name": "p"}}}):
            return llm.rule_refusal("mailbox-escalation", spec, runtime_fake, types.SimpleNamespace(env=env))

    def test_a_handler_without_letters_doesnt_start(self):
        for letters in (None, "", "[]", "not json", '"m1"', '["m1", 2]', '["m1\\n"]', '["--withdraw"]', '["a b"]'):
            with self.subTest(letters=letters):
                self.assertIsNotNone(self.refusal(letters))
        self.assertIsNone(self.refusal('["m11100227-08bb", "qcheck-x:1"]'))


@unittest.skipIf(yaml is None, "needs PyYAML")
class RegistryMail(unittest.TestCase):
    def errors(self, job: dict) -> list:
        import registry
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        (tmp / "p" / "jobs").mkdir(parents=True)
        (tmp / "p" / "jobs" / "j.md").write_text("x\n")
        doc = {"v": 1, "name": "p", "paths": {"workspace": str(tmp)}, "jobs": [{"name": "j", **job}]}
        with mock.patch.object(registry.hubconf, "registry_dir", lambda: tmp):
            return [e for e in registry.validate_runs(doc, "p", {"workspace": tmp}) if "mail" in e]

    def test_only_a_scheduled_agent_job_with_allow(self):
        agent = {"at": "08:00", "kind": "agent", "prompt": "jobs/j.md"}
        self.assertEqual(self.errors({**agent, "allow": ["Read"], "mail": True}), [])
        self.assertEqual(self.errors({**agent, "allow": ["Read"], "mail": False}), [])
        for job in ({**agent, "mail": True},
                    {"at": "08:00", "run": "true", "mail": True},
                    {"trigger": {"events": ["merge.done"]}, "kind": "agent", "prompt": "jobs/j.md", "allow": ["Read"],
                     "mail": True},
                    {**agent, "allow": ["Read"], "mail": "yes"}):
            with self.subTest(job):
                self.assertEqual(len(self.errors(job)), 1)

    def test_the_command_says_so(self):
        import registry
        index = {"projects": {"p": {"name": "p", "paths": {"workspace": "/tmp/p-ws"}, "jobs": [
            {"name": "a", "at": "08:00", "kind": "agent", "allow": ["Read"], "mail": True},
            {"name": "b", "at": "09:00", "kind": "agent", "allow": ["Read"]}]}}}
        with mock.patch.object(registry.isolation, "sandboxed", lambda doc: False):
            jobs = {j["name"]: j["command"] for j in registry.scheduler_jobs(index, set())[0]}
        self.assertTrue(jobs["p/a"].startswith("AGENT_JOB_MAIL=1 "))
        self.assertNotIn("AGENT_JOB_MAIL", jobs["p/b"])


if __name__ == "__main__":
    unittest.main()
