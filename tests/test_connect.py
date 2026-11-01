"""agenthub connect (docs/design/newcomer-path.md B6): what a folder holds, what is refused, and what would be run.
Nothing is onboarded: --dry-run, or onboarding replaced.

    bin/py -m unittest tests.test_connect
"""
import importlib.machinery
import importlib.util
import io
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("connect_t", str(ROOT / "bin" / "connect.py"))
spec = importlib.util.spec_from_loader("connect_t", loader)
connect = importlib.util.module_from_spec(spec)
loader.exec_module(connect)


def repo(p: pathlib.Path, files=None) -> pathlib.Path:
    p.mkdir(parents=True, exist_ok=True)
    for name, text in (files or {"README": "x\n"}).items():
        (p / name).parent.mkdir(parents=True, exist_ok=True)
        (p / name).write_text(text)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(p), *args], check=True, capture_output=True, env=env)
    return p


class Connect(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.code = self.home / "code"
        self.code.mkdir(parents=True)
        self.projects = {}
        patches = [mock.patch.object(connect.hubconf, "projects", lambda: self.projects),
                   mock.patch.object(connect.hubconf, "MAIN", self.tmp / "hub"),
                   mock.patch.object(connect.hubconf, "instance_home", lambda: self.tmp / "instance"),
                   mock.patch.object(connect.pathlib.Path, "home", classmethod(lambda cls: self.home))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_connect(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = connect.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_layouts(self):
        plain = self.code / "notes"
        plain.mkdir()
        (plain / "a.md").write_text("x")
        self.assertEqual(connect.layout(plain), ("plain", [plain]))
        one = repo(self.code / "app")
        self.assertEqual(connect.layout(one), ("repo", [one]))
        mono = repo(self.code / "mono", {"pnpm-workspace.yaml": "packages: []\n"})
        self.assertEqual(connect.layout(mono)[0], "monorepo")
        parts = repo(self.code / "parts", {"packages/a/package.json": "{}", "packages/b/package.json": "{}"})
        self.assertEqual(connect.layout(parts)[0], "monorepo")
        group = self.code / "group"
        a, b = repo(group / "a"), repo(group / "b")
        self.assertEqual(connect.layout(group), ("repos", [a, b]))

    def test_refusals(self):
        (self.code / ".hidden").mkdir()
        repo(self.code / "inside" / "sub")
        self.projects = {"inside": {"paths": {"workspace": str(self.code / "inside")}}}
        for folder, why in ((self.home, "home"), (self.home.parent, "home"), (self.code / ".hidden", "hidden"),
                            (self.code / "inside" / "sub", "inside")):
            with self.subTest(folder=folder):
                self.assertIsNotNone(connect.refusal(folder), why)
        self.assertIsNone(connect.refusal(self.code))

    def test_a_symlink_is_followed_to_its_real_place(self):
        (self.home / ".dotfiles").mkdir()
        link = self.code / "x"
        link.symlink_to(self.home / ".dotfiles")
        rc, _out, err = self.run_connect(str(link), "--yes", "--dry-run")
        self.assertEqual(rc, 2)
        self.assertIn(".dotfiles", err, "resolved, then refused as hidden")

    def test_a_folder_of_repos_dry_run(self):
        group = self.code / "group"
        repo(group / "a"), repo(group / "b")
        rc, out, _err = self.run_connect(str(group), "--yes", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("onboard-project.sh --no-remote"), 2)
        self.assertNotIn("layout_intent", out, "--yes doesn't say they belong together")

    def test_monorepo_split_intent_is_recorded(self):
        mono = repo(self.code / "mono", {"go.work": "go 1.22\n"})
        with mock.patch.object(connect, "ask", lambda q, yes, default: True):
            rc, out, _err = self.run_connect(str(mono), "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn('layout_intent={"split": true}', out)

    def test_unsaved_changes_are_skipped(self):
        app = repo(self.code / "app")
        (app / "README").write_text("changed\n")
        with mock.patch.object(connect.subprocess, "run", wraps=connect.subprocess.run) as run:
            rc, out, _err = self.run_connect(str(app), "--yes")
        self.assertEqual(rc, 1)
        self.assertIn(str(app), out)
        self.assertFalse(any("onboard-project.sh" in " ".join(map(str, c.args[0])) for c in run.call_args_list))

    def test_untrusted_git_runs_without_hooks(self):
        app = repo(self.code / "app")
        hook = app / ".git" / "hooks" / "fsmonitor"
        hook.write_text("#!/bin/sh\ntouch " + str(self.tmp / "ran") + "\n")
        hook.chmod(0o755)
        subprocess.run(["git", "-C", str(app), "config", "core.fsmonitor", str(hook)], check=True)
        connect.sgit(app, "status", "--porcelain")
        self.assertFalse((self.tmp / "ran").exists(), "the folder's fsmonitor did not run")


if __name__ == "__main__":
    unittest.main()
