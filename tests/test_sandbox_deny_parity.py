"""A sandboxed project's sessions are denied what its services are (docs/design/capabilities.md, "11-08: a sandboxed
project's sessions are denied what its services are", A1): one policy (policy.py), four renderers that must agree on
what can't be read -- Seatbelt (macOS services, jobs, cap-runner), Landlock (the same on Linux), and a Claude Code
session's Bash sandbox and file tools (runtime_claude.settings). Each renderer's output is matched here the way that
renderer matches a path, on a tree whose instance dir is reached through a link (as <hub>/.hub-home's usually is).

    bin/py -m unittest tests.test_sandbox_deny_parity
"""
import os
import pathlib
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import landlock  # noqa: E402
import names  # noqa: E402
import policy  # noqa: E402
import runtime_claude  # noqa: E402
import seatbelt  # noqa: E402

REAL = {"state": hubconf.STATE, "shared": hubconf.SHARED, "index": hubconf.REGISTRY_INDEX,
        "slots": hubconf.shared() / hubconf.TEST_SLOTS_DIR, "renames": hubconf.shared() / hubconf.RENAMES_FILE}

try:
    import yaml  # noqa: F401  (registry.dirs, which policy.policy uses)
except ImportError:
    yaml = None


class Tree(unittest.TestCase):
    """A home, a hub with its .state and .logs, an instance dir behind a link, credentials, two projects."""

    def setUp(self):
        t = self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, t, ignore_errors=True)
        home, hub = self.home, self.hub = t / "home", t / "hub"
        self.state, self.logs, self.shared = hub / ".state", hub / ".logs", hub / ".shared"
        real = self.inst_real = t / "instance-real"
        link = self.inst = home / ".instance"
        for d in (self.shared / "registry", self.shared / "test-slots", self.state / "agent-jobs" / "job-a",
                  self.state / "cap-runner", self.logs, real / "config", real / "data", real / "secrets",
                  home / ".ssh", home / ".aws", home / ".claude" / "projects" / "x" / "memory", home / ".claude" / "plans",
                  t / "ws" / "proj", t / "ws" / "other", t / "pathbin", t / "copies", t / "origin", t / "bus"):
            d.mkdir(parents=True)
        link.symlink_to(real)
        for f, text in ((self.shared / "registry" / "index.json", "{}"), (self.shared / "test-slots" / "1", ""),
                        (self.shared / "project-renames.json", "{}"), (self.state / "scheduler-state.json", "{}"),
                        (self.state / "agent-jobs" / "job-a" / "report.md", "x"), (self.logs / "a.log", "x"),
                        (real / "config" / "brunna.json", "{}"), (real / "data" / "todo.md", "x"),
                        (real / "secrets" / "key", "x"), (home / ".ssh" / "id_x", "x"), (home / ".aws" / "credentials", "x"),
                        (home / ".netrc", "x"), (home / ".claude" / ".credentials.json", "x"),
                        (t / "ws" / "proj" / "f", "x"), (t / "ws" / "other" / "f", "x")):
            f.write_text(text)
        # install-cli's wrapper and uv on PATH, outside the default folders
        (t / "pathbin" / "brunna").write_text("#!/bin/sh\n# written by brunna install-cli\n")
        (t / "pathbin" / "uv").write_text("#!/bin/sh\n")
        for f in ("brunna", "uv"):
            (t / "pathbin" / f).chmod(0o755)
        # the platform's venv, found the way bin/py finds it, and the interpreter its pyvenv.cfg names
        venv = self.venv = home / ".local" / "share" / names.all("share_dir")[0] / "python"
        (venv / "bin").mkdir(parents=True)
        (venv / "bin" / "python3").write_text("")
        self.interp = t / "uv-python" / "cpython-3.13"
        (venv / "pyvenv.cfg").write_text(f"home = {self.interp / 'bin'}\nversion = 3.13.0\n")
        self.evals = t / "evals-not-made"
        cfg = {"credentials": {"dirs": ["~/.claude", "~/.ssh"], "files": ["~/.claude.json"]},
               "evals_dir": str(self.evals), "workspace_root": str(t / "ws"), "git_origin": str(t / "origin"),
               "memory_copies": str(t / "copies")}
        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDE_CONFIG_DIR", "NVM_DIR", *names.env_names("PYTHON"), *names.env_names("EVALS"))}
        env.update(HOME=str(home), PATH=f"{t / 'pathbin'}:/usr/bin:/bin")
        for p in (mock.patch.dict(os.environ, env, clear=True),
                  mock.patch.object(policy, "HOME", home),
                  mock.patch.object(hubconf, "_cfg", cfg),
                  mock.patch.object(hubconf, "ROOT", hub), mock.patch.object(hubconf, "MAIN", hub),
                  mock.patch.object(hubconf, "STATE", self.state), mock.patch.object(hubconf, "LOGS", self.logs),
                  mock.patch.object(hubconf, "BUS_DIR", t / "bus"),
                  mock.patch.object(hubconf, "instance_home", lambda: link),
                  mock.patch.object(hubconf, "config_dir", lambda: link / "config"),
                  mock.patch.object(hubconf, "data_dir", lambda: link / "data"),
                  mock.patch.object(hubconf, "secrets_dir", lambda: link / "secrets"),
                  mock.patch.object(hubconf, "evals_dir", mock.Mock(side_effect=AssertionError("evals_dir() makes it"))),
                  mock.patch.object(runtime_claude, "CREDENTIALS",
                                    {"dirs": [], "files": [str(home / ".claude" / ".credentials.json")]}, create=True),
                  mock.patch.object(policy, "protect", lambda: [])):
            p.start()
            self.addCleanup(p.stop)
        self.docs = {n: {"name": n, "paths": {"workspace": str(t / "ws" / n)}, "enforce": {"sandbox": True}}
                     for n in ("proj", "other")}

    def pol(self, name="proj"):
        return policy.policy(name, self.docs.get(name) or {"name": name, "paths": {"workspace": str(self.hub)}},
                             {"projects": self.docs})


# ---------- each renderer's matcher ----------

def _sb_items(line: str):
    return re.findall(r'\((subpath|literal|regex) (#?"(?:[^"\\]|\\.)*")\)', line)


def _sb_match(items, path: str) -> bool:
    for kind, lit in items:
        if kind == "regex":   # seatbelt.glob_regex: only " is escaped
            if re.search(lit[2:-1].replace('\\"', '"'), path):
                return True
            continue
        s = lit[1:-1].replace('\\"', '"').replace("\\\\", "\\")   # seatbelt.q
        if path == s or (kind == "subpath" and path.startswith(s.rstrip("/") + "/")):
            return True
    return False


def seatbelt_reads(pol, own, path) -> bool:
    """Seatbelt matches the real path of what is opened; the allow after the deny wins."""
    text = seatbelt.service_profile(pol, own)
    deny = next(x for x in text.splitlines() if x.startswith("(deny file-read*"))
    allow = next(x for x in text.splitlines() if x.startswith("(allow file-read*"))
    real = os.path.realpath(path)
    return not _sb_match(_sb_items(deny), real) or _sb_match(_sb_items(allow), real)


def landlock_reads(pol, own, path, root) -> bool:
    """What landlock.plan allows (read_roots of denied(), plus policy.read), walked from the test's own folder; the
    kernel follows links, so the real path counts."""
    orig = landlock.read_roots
    # the fixture is in the temp area, which plan lets everything write (and so read): not counted here
    with mock.patch.object(landlock, "read_roots", lambda deny, r=None: orig(deny, root)), \
            mock.patch.object(landlock, "TMP", []):
        pl = landlock.plan(pol, own)
    roots = [*pl["read"], *pl["write"]]   # a write rule carries every right, reading included
    real = pathlib.Path(os.path.realpath(path))
    return any(landlock._under(real, r) for r in roots)


def _expand(entry: str, home: pathlib.Path) -> str:
    return str(home) + entry[1:] if entry.startswith("~") else entry


def bash_reads(sandbox, path, home) -> bool:
    """The session's Bash sandbox, taken at its word: an entry denies the path named under it (no link resolved)."""
    return not any(path == e or path.startswith(e.rstrip("/") + "/")
                   for e in (_expand(x, home) for x in sandbox["filesystem"]["denyRead"]))


def _rule_regex(spec: str, home: pathlib.Path) -> re.Pattern:
    """A file rule's path (~/…, //…) → a regex: ** any depth, * one name."""
    p = str(home) + spec[1:] if spec.startswith("~") else spec[1:] if spec.startswith("//") else None
    assert p is not None, f"not an absolute rule: {spec}"
    out = re.escape(p).replace(r"/\*\*", "(/.*)?").replace(r"\*", "[^/]+")
    return re.compile("^" + out + "$")


def rule_hits(rules, tool: str, path: str, home) -> list[str]:
    hits = []
    for r in rules:
        m = re.fullmatch(rf"{tool}\((.*)\)", r)
        if m and m[1].startswith(("~", "//")) and _rule_regex(m[1], home).match(path):
            hits.append(r)
    return hits


@unittest.skipIf(yaml is None, "needs PyYAML")
class Parity(Tree):
    def denied_everywhere(self):
        t, real, link = self.tmp, self.inst_real, self.inst
        return [self.home / ".ssh" / "id_x", self.home / ".aws" / "credentials", self.home / ".netrc",
                self.home / ".claude" / ".credentials.json",
                link / "data" / "todo.md", real / "data" / "todo.md", link / "secrets" / "key", real / "secrets" / "key",
                self.logs / "a.log", self.state / "agent-jobs" / "job-a" / "report.md", self.state / "scheduler-state.json",
                self.state / "cap-runner", self.evals / "case.json", t / "ws" / "other" / "f"]

    def readable_everywhere(self):
        return [self.shared / "registry" / "index.json", self.shared / "test-slots" / "1", self.shared / "project-renames.json",
                self.tmp / "ws" / "proj" / "f", self.inst / "config" / "brunna.json",
                self.inst_real / "config" / "brunna.json"]

    def test_the_four_renderers_deny_the_same(self):
        pol = self.pol()
        own = [self.tmp / "ws" / "proj"]
        sandbox, deny, _allow = runtime_claude.settings(pol)
        for p in self.denied_everywhere():
            s = str(p)
            with self.subTest(denied=s):
                self.assertFalse(seatbelt_reads(pol, own, s), "Seatbelt")
                self.assertFalse(landlock_reads(pol, own, s, self.tmp), "Landlock")
                self.assertFalse(bash_reads(sandbox, s, self.home), "the session's Bash")
                self.assertTrue(rule_hits(deny, "Read", s, self.home), "the session's Read tool")
        for p in self.readable_everywhere():
            s = str(p)
            with self.subTest(readable=s):
                self.assertTrue(seatbelt_reads(pol, own, s), "Seatbelt")
                self.assertTrue(landlock_reads(pol, own, s, self.tmp), "Landlock")
                self.assertTrue(bash_reads(sandbox, s, self.home), "the session's Bash")
                self.assertFalse(rule_hits(deny, "Read", s, self.home), "the session's Read tool")

    def test_the_hub_root_is_never_denied_to_the_sessions_bash(self):
        """git dies when a folder above its -C can't be stat'ed: not the workspace root, not the hub's own folder."""
        sandbox = runtime_claude.settings(self.pol())[0]
        for p in (self.tmp / "ws", self.hub, self.shared):
            self.assertTrue(bash_reads(sandbox, str(p), self.home), p)


@unittest.skipIf(yaml is None, "needs PyYAML")
class PlatformState(Tree):
    def test_the_hub_has_none(self):
        self.assertEqual(self.pol(hubconf.hub_project())["platform_state"], [])

    def test_state_is_denied_whole_and_what_sandboxes_read_is_beside_it(self):
        got = self.pol()["platform_state"]
        self.assertIn(self.state, got)
        self.assertFalse([p for p in got if self.state in p.parents], "nothing listed inside it")
        self.assertEqual(self.pol()["read"], [self.shared])
        for p in (REAL["index"], REAL["slots"], REAL["renames"]):   # the module's own, not this test's tree
            self.assertIn(REAL["shared"], p.parents, p)
        self.assertNotIn(REAL["state"], [REAL["shared"], *REAL["shared"].parents])
        sandbox, deny, _allow = runtime_claude.settings(self.pol())
        self.assertEqual([r for r in deny if ".state" in r],
                         [f"{t}(/{self.state}{s})" for t in ("Read", "Edit") for s in ("", "/**")])
        self.assertEqual([e for e in sandbox["filesystem"]["denyRead"] if ".state" in e], [str(self.state)])

    def test_a_confined_jobs_own_folder_is_left_out(self):
        (self.state / "agent-jobs" / "job-b").mkdir()
        keep = self.state / "agent-jobs" / "job-a"
        got = policy.platform_state("proj", [keep])
        self.assertNotIn(keep, got)
        self.assertNotIn(self.state / "agent-jobs", got)
        self.assertIn(self.state / "agent-jobs" / "job-b", got)
        self.assertIn(self.state / "scheduler-state.json", got)

    def test_logs_data_and_evals_without_making_evals(self):
        got = self.pol()["platform_state"]
        for p in (self.logs, self.inst / "data", self.evals):
            self.assertIn(p, got)
        self.assertFalse(self.evals.exists())

    def test_the_old_layouts_data_dir_is_left_out(self):
        with mock.patch.object(hubconf, "data_dir", lambda: self.hub):
            got = self.pol()["platform_state"]
        self.assertNotIn(self.hub, got)
        self.assertIn(self.logs, got)


@unittest.skipIf(yaml is None, "needs PyYAML")
class DenyWrite(Tree):
    def test_what_runs_as_the_user_is_never_written(self):
        pol = self.pol()
        dirs, files = pol["deny_write"], pol["deny_write_files"]
        for d in (self.inst, self.venv, self.interp, self.home / ".local" / "bin", self.tmp / "pathbin",
                  self.home / ".nvm", self.home / ".config" / "git", self.home / "Library" / "LaunchAgents",
                  self.home / ".config" / "systemd" / "user", self.hub):
            self.assertIn(d, dirs)
        for f in (self.tmp / "pathbin" / "uv", self.home / ".gitconfig", self.home / ".zshrc", self.home / ".zprofile",
                  self.home / ".bashrc", self.home / ".bash_profile", self.home / ".profile"):
            self.assertIn(f, files)

    def test_the_bus_only_for_a_project_whose_mail_goes_through_its_outbox(self):
        self.assertIn(self.tmp / "bus", self.pol()["deny_write"])
        self.docs["other"]["enforce"] = {}
        self.assertNotIn(self.tmp / "bus", self.pol("other")["deny_write"])

    def test_the_file_tools_get_both_forms(self):
        deny = runtime_claude.settings(self.pol())[1]
        self.assertTrue(rule_hits(deny, "Edit", str(self.home / ".zshrc"), self.home))
        self.assertTrue(rule_hits(deny, "Edit", str(self.inst_real / "config" / "brunna.json"), self.home))
        self.assertTrue(rule_hits(deny, "Edit", str(self.interp / "lib" / "site.py"), self.home))
        self.assertTrue(rule_hits(deny, "Edit", str(self.tmp / "pathbin" / "git"), self.home))
        self.assertFalse(rule_hits(deny, "Edit", str(self.tmp / "ws" / "proj" / "f"), self.home))


@unittest.skipIf(yaml is None, "needs PyYAML")
class UserControl(Tree):
    def test_the_runtimes_user_files_are_never_edited_its_sessions_files_are(self):
        deny = runtime_claude.settings(self.pol())[1]
        c = self.home / ".claude"
        for p in (c / "settings.json", c / "settings.local.json", c / "CLAUDE.md", c / "hooks" / "x", c / "plugins" / "x",
                  c / "skills" / "s" / "SKILL.md", c / "commands" / "x.md", self.home / ".claude.json"):
            self.assertTrue(rule_hits(deny, "Edit", str(p), self.home), p)
        # the home is in the settings' credentials (as in hub.example.json): its sessions still use it
        for p in (c / "projects" / "x" / "memory" / "a.md", c / "plans" / "p.md"):
            self.assertFalse(rule_hits(deny, "Edit", str(p), self.home), p)
            self.assertFalse(rule_hits(deny, "Read", str(p), self.home), p)
        self.assertTrue(rule_hits(deny, "Read", str(c / ".credentials.json"), self.home))
        self.assertFalse(rule_hits(deny, "Read", str(c / "skills" / "s" / "SKILL.md"), self.home))
        sandbox = runtime_claude.settings(self.pol())[0]
        self.assertTrue(bash_reads(sandbox, str(c / "plans" / "p.md"), self.home))
        self.assertFalse(bash_reads(sandbox, str(c / ".credentials.json"), self.home))
        for r in runtime_claude.SECURITY_DENY:
            self.assertIn(r, deny)

    def test_a_configured_home_is_honoured(self):
        alt = self.tmp / "claude-config"
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(alt)}):
            deny = runtime_claude.settings(self.pol())[1]
        self.assertTrue(rule_hits(deny, "Edit", str(alt / "settings.json"), self.home))
        self.assertTrue(rule_hits(deny, "Edit", str(alt / ".claude.json"), self.home))

    def test_the_platforms_own_sandboxes_still_deny_the_home_whole(self):
        pol = self.pol()
        self.assertFalse(seatbelt_reads(pol, [self.tmp / "ws" / "proj"], str(self.home / ".claude" / "plans" / "p.md")))

    def test_a_lease_still_denies_the_credential_folders_whole(self):
        deny = runtime_claude.lease_settings(self.pol())["permissions"]["deny"]
        self.assertTrue(rule_hits(deny, "Read", str(self.home / ".claude" / "plans" / "p.md"), self.home))
        self.assertEqual(len(deny), len(set(deny)))


class MovedOnce(unittest.TestCase):
    """What sandboxed code reads left .state for hubconf.SHARED (11-08): moved once, by the live layout's first look, never
    read in both places."""

    def setUp(self):
        t = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, t, ignore_errors=True)
        self.state, self.shared, self.t = t / ".state", t / ".shared", t
        (self.state / "test-slots").mkdir(parents=True)
        (self.state / "test-slots" / "slot-00").write_text("")
        (self.state / "project-renames.json").write_text('{"a": {"new": "b", "stage": "done"}}')
        for p in (mock.patch.object(hubconf, "MAIN", t), mock.patch.object(hubconf, "STATE", self.state),
                  mock.patch.object(hubconf, "worktree_lookup", lambda: False)):
            p.start()
            self.addCleanup(p.stop)

    def test_a_file_and_a_folder_move_once(self):
        self.assertEqual(hubconf.renames(), self.shared / "project-renames.json")
        self.assertFalse((self.state / "project-renames.json").exists())
        self.assertEqual(hubconf.former_names("b"), ["a"])
        self.assertEqual(hubconf.test_slots(), self.shared / "test-slots")
        self.assertTrue((self.shared / "test-slots" / "slot-00").exists())
        self.assertFalse((self.state / "test-slots").exists())
        (self.state / "project-renames.json").write_text("{}")   # a stale copy is never taken over the moved one
        hubconf.renames()
        self.assertIn("stage", (self.shared / "project-renames.json").read_text())

    def test_not_from_a_worktree_or_a_throwaway_state(self):
        with mock.patch.object(hubconf, "worktree_lookup", lambda: True):
            hubconf.renames()
        with mock.patch.object(hubconf, "MAIN", self.t / "elsewhere"):   # a state other than the hub's own
            self.assertEqual(hubconf.renames(), self.state / "shared" / "project-renames.json")
        self.assertTrue((self.state / "project-renames.json").exists())
        self.assertFalse(self.shared.exists())


if __name__ == "__main__":
    unittest.main()
