"""A project's declared credentials are kept from every confined run of it (docs/design/project-agent-calls.md C).

- policy.secret_paths resolves each registry secrets: path: a relative one under both the workspace and the runtime
  folder, ~/ and absolute ones as given, folders and files apart.
- A confined run's settings deny reading and writing them (files by name, folders whole), and a sandboxed project's
  sandbox denies reading them too.
- registry.py check --secrets-report lists what that changes.

Scratch folders only; no agent is started.

    bin/py -m unittest tests.test_confined_secrets
"""
import contextlib
import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hubconf  # noqa: E402
import policy  # noqa: E402
import registry  # noqa: E402
import runtime_claude  # noqa: E402


class Scratch(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws, self.rt = self.tmp / "ws" / "p", self.tmp / "services" / "p"
        for d in (self.ws / "secrets", self.rt / "secrets"):
            d.mkdir(parents=True)
        self.doc = {"name": "p", "paths": {"workspace": str(self.ws)}, "runtime": {},
                    "secrets": [{"name": "a", "path": "secrets/token"}, {"name": "b", "path": ".env", "env_key": "K"},
                                {"name": "c", "path": "secrets"}, {"name": "d", "path": str(self.tmp / "abs-key")}]}
        p = mock.patch.object(registry, "dirs", lambda doc: {"workspace": self.ws, "runtime": self.rt, "hub": ROOT})
        p.start()
        self.addCleanup(p.stop)


class Paths(Scratch):
    def test_relative_under_both_absolute_as_given(self):
        folders, files = policy.secret_paths("p", self.doc)
        self.assertEqual(folders, [self.ws / "secrets", self.rt / "secrets"])
        self.assertEqual(files, [self.ws / "secrets" / "token", self.rt / "secrets" / "token", self.ws / ".env",
                                 self.rt / ".env", self.tmp / "abs-key"])

    def test_none_declared(self):
        self.assertEqual(policy.secret_paths("p", {"name": "p"}), ([], []))
        self.assertEqual(policy.secret_paths("p", {"name": "p", "secrets": [{"name": "x"}, "junk"]}), ([], []))

    def test_the_platforms_own_through_hubconf(self):
        with mock.patch.object(hubconf, "hub_project", lambda: "p"):
            _folders, files = policy.secret_paths("p", {"name": "p", "secrets": [{"name": "k", "path": ".secrets/key"}]})
        self.assertEqual(files, [hubconf.secrets_dir() / "key"])


class Settings(Scratch):
    def settings(self, doc) -> dict:
        pol = {"other_projects": [], "deny_write": [], "deny_read": [], "platform_commands": [], "platform_cli": [],
               "write": [str(self.ws)], "network": [], "credential_files": [], "credentials": [], "workspace": str(self.ws)}
        run_dir = self.tmp / "run"
        run_dir.mkdir(exist_ok=True)
        (run_dir / "settings.json").unlink(missing_ok=True)
        with mock.patch("policy.policy", lambda name, d, i: pol), \
                mock.patch("registry.refresh", lambda: {"projects": {"p": doc}}), \
                mock.patch.object(runtime_claude, "project_roots", lambda p: [self.ws]):
            f = runtime_claude.platform_settings("p", {}, run_dir, None, self.ws)
        return json.loads(f.read_text())

    def test_read_and_written_by_no_confined_run(self):
        deny = self.settings(self.doc)["permissions"]["deny"]
        rule = runtime_claude._rule_path
        for f in (self.ws / "secrets" / "token", self.rt / ".env", self.tmp / "abs-key"):
            for want in (f"Read({rule(f)})", f"Edit({rule(f)})"):
                self.assertIn(want, deny)
        for d in (self.ws / "secrets", self.rt / "secrets"):
            for want in (f"Read({rule(d)}/**)", f"Edit({rule(d)}/**)", f"Edit({rule(d)})"):
                self.assertIn(want, deny)

    def test_a_copy_anywhere_below_the_projects_folders(self):
        deny = self.settings(self.doc)["permissions"]["deny"]
        rule = runtime_claude._rule_path
        for base in (self.ws, self.rt):
            for rel in ("secrets/token", ".env", "secrets"):
                for tail in ("", "/**"):
                    for tool in ("Read", "Edit"):
                        self.assertIn(f"{tool}({rule(base)}/**/{rel}{tail})", deny)
        self.assertFalse([r for r in deny if "/**/" + str(self.tmp / "abs-key") in r])

    def test_a_path_that_isnt_plain_text_is_never_a_rule(self):
        doc = {**self.doc, "secrets": [{"name": "a", "path": "x)"}, {"name": "b", "path": "data/*"}]}
        self.assertEqual(policy.secret_paths("p", doc), ([], []))
        self.assertEqual(policy.secret_names("p", doc), [])

    def test_a_sandboxed_projects_sandbox_denies_reading_them(self):
        d = self.settings({**self.doc, "enforce": {"sandbox": True}})
        deny_read = d["sandbox"]["filesystem"]["denyRead"]
        for p in (self.ws / "secrets", self.rt / ".env", self.tmp / "abs-key"):
            self.assertIn(runtime_claude._tilde(p), deny_read)

    def test_none_declared_adds_nothing(self):
        bare = {k: v for k, v in self.doc.items() if k != "secrets"}
        mine = (str(self.ws / "secrets"), str(self.rt / "secrets"), str(self.ws / ".env"), str(self.rt / ".env"),
                str(self.tmp / "abs-key"))
        self.assertFalse([r for r in self.settings(bare)["permissions"]["deny"] if any(m in r for m in mine)])


class Report(Scratch):
    def test_it_lists_the_jobs_whose_commands_still_read_them(self):
        jobs = [{"name": "j", "kind": "agent", "allow": ["Read", "Bash(.venv/bin/python tools/x.py)"]},
                {"name": "k", "kind": "agent", "allow": ["Read"]}]
        index = {"projects": {"p": {**self.doc, "jobs": jobs}, "q": {"name": "q"}}}
        out = io.StringIO()
        with mock.patch.object(registry, "sandbox_enforced", lambda doc: False), \
                mock.patch("runtime_ports.load", lambda *a, **k: runtime_claude), contextlib.redirect_stdout(out):
            self.assertEqual(registry.secrets_report(index), 0)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn(str(self.ws / "secrets" / "token"), lines[0])
        self.assertTrue(lines[1].startswith("    j") and "Bash(.venv/bin/python tools/x.py)" in lines[1])


if __name__ == "__main__":
    unittest.main()
