"""Where the hub is: what the platform writes that calls back into the hub (the agent's hook commands, the user-level
worktree hooks, the lease resume line, the git hooks) names the hub's real location, taken from hubconf when it is
written; nothing it writes or ships assumes the hub sits at ~/agent-hub (AGENTS.md, rules for new code).

Characterized first: with the hub at ~/agent-hub, the text is exactly what is installed in projects today, character
for character, so a rewrite at the next session start changes nothing on such a machine.
Then: a hub elsewhere (a path with a space, outside home) gets hooks that name it and really reach it; the platform's
hooks written for an earlier location are replaced, not doubled; and no tracked file outside docs and tests names
~/agent-hub or $HOME/agent-hub (only the remote machine's migrate-to-mini.py, which knows that layout by design, and
sync-local-setup.sh's leak detector, which looks for exactly that text in projects' committed files).

    bin/py -m unittest tests.test_hub_location
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import runtime_claude  # noqa: E402


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def py(expr: str) -> str:
    """How a platform Python script is written in a hook on this system (hubconf.sh_py)."""
    return expr if sys.platform == "darwin" else f"{expr.rsplit('/', 1)[0]}/py {expr}"


class AtHomeToday(unittest.TestCase):
    """The hub at ~/agent-hub (the layout every installed hook was written for)."""

    def setUp(self):
        hub = pathlib.Path.home() / "agent-hub"
        for name in ("ROOT", "MAIN"):
            p = mock.patch.object(hubconf, name, hub)
            p.start()
            self.addCleanup(p.stop)

    def test_hook_commands(self):
        for _name, _matcher, ev in runtime_claude.HOOK_TABLE:
            self.assertEqual(runtime_claude.hook_command(ev),
                             f"[ -x ~/agent-hub/bin/hub-event ] && {py('~/agent-hub/bin/hub-event')} --runtime claude {ev}"
                             " || true")

    def test_installed_hooks(self):
        def entry(ev, matcher=None):
            e = {"hooks": [{"type": "command",
                            "command": f"[ -x ~/agent-hub/bin/hub-event ] && {py('~/agent-hub/bin/hub-event')} "
                                       f"--runtime claude {ev} || true"}]}
            return {"matcher": matcher, **e} if matcher else e
        self.assertEqual(runtime_claude.install_hooks({}), {"hooks": {
            "SessionStart": [entry("session_start")],
            "SessionEnd": [entry("session_end")],
            "PreCompact": [entry("pre_compact")],
            "UserPromptSubmit": [entry("prompt")],
            "PostToolUse": [entry("tool_used", "Bash")],
            "PreToolUse": [entry("open_session_attempt", "mcp__ccd_session__start_session")],
        }})

    def test_worktree_hooks(self):
        m = load("merge_hook_settings", BIN / "merge-hook-settings.py")
        wt = "[ -x ~/agent-hub/bin/worktree-memory.sh ] && ~/agent-hub/bin/worktree-memory.sh"
        self.assertEqual(m.WORKTREE_HOOKS, (
            ("SessionStart", None, f"{wt} start || true"),
            ("SessionEnd", None, f"{wt} end || true"),
            ("PreCompact", None, f"{wt} end || true"),
            ("PostToolUse", "Bash", f"{wt} nudge || true"),
            ("UserPromptSubmit", None, f"{wt} prompt || true"),
        ))

    def test_resume_command(self):
        self.assertEqual(runtime_claude.resume_command("s1")[0], "~/agent-hub/bin/lease-resume s1")


class Elsewhere(unittest.TestCase):
    """The hub cloned somewhere else: outside home, with a space in the path."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.hub = self.tmp / "my hubs" / "hub"
        (self.hub / "bin").mkdir(parents=True)
        for name in ("ROOT", "MAIN"):
            p = mock.patch.object(hubconf, name, self.hub)
            p.start()
            self.addCleanup(p.stop)

    def written(self) -> list[str]:
        rps = load("render_project_settings", BIN / "render-project-settings.py")
        mhs = load("merge_hook_settings", BIN / "merge-hook-settings.py")
        out = [runtime_claude.hook_command(ev) for _n, _m, ev in runtime_claude.HOOK_TABLE]
        out += [h["command"] for es in runtime_claude.install_hooks({})["hooks"].values() for e in es for h in e["hooks"]]
        out += [c for _e, _m, c in mhs.WORKTREE_HOOKS]
        out.append(runtime_claude.resume_command("s1")[0])
        out += [text for text, _why in rps.wanted_hooks({"commit_gate": True, "commits": "conventional"}).values()]
        return out

    def test_everything_written_names_this_hub(self):
        for text in self.written():
            with self.subTest(text=text):
                self.assertNotIn("agent-hub/", text)
                self.assertIn(str(self.hub / "bin"), text)

    def test_the_hook_reaches_the_hub(self):
        """Run as the agent runs it: through a shell, from a project folder."""
        (self.hub / "bin" / "hub-event").write_text('#!/bin/sh\necho "reached $*"\n')
        (self.hub / "bin" / "py").write_text('#!/bin/sh\nexec /bin/sh "$@"\n')   # Linux: py in front (hubconf.sh_py)
        for f in ("hub-event", "py"):
            (self.hub / "bin" / f).chmod(0o755)
        r = subprocess.run(["/bin/sh", "-c", runtime_claude.hook_command("session_start")], cwd=self.tmp,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.strip(), "reached --runtime claude session_start", r.stderr)

    def test_old_and_moved_hooks_are_replaced_not_doubled(self):
        settings = {"hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "[ -x ~/agent-hub/bin/hub-event ] && "
                                                                       "~/agent-hub/bin/hub-event session_start || true"}]},
                             {"hooks": [{"type": "command", "command": "echo mine"}]}],
            "SessionEnd": [{"hooks": [{"type": "command", "command": "[ -x /old/place/bin/hub-event ] && "
                                                                     "/old/place/bin/hub-event session_end || true"}]}]}}
        out = runtime_claude.install_hooks(settings)
        cmds = [h["command"] for es in out["hooks"].values() for e in es for h in e["hooks"]]
        self.assertEqual(sorted(cmds), sorted([runtime_claude.hook_command(ev) for _n, _m, ev in runtime_claude.HOOK_TABLE]
                                              + ["echo mine"]))
        self.assertEqual(runtime_claude.install_hooks(json.loads(json.dumps(out))), out, "a rerun changes nothing")

    def test_worktree_hooks_replace_an_earlier_location(self):
        mhs = BIN / "merge-hook-settings.py"
        f = self.tmp / "settings.json"
        old = "[ -x ~/agent-hub/bin/worktree-memory.sh ] && ~/agent-hub/bin/worktree-memory.sh"
        f.write_text(json.dumps({"model": "x", "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": f"{old} start || true"}]},
                             {"hooks": [{"type": "command", "command": "echo mine"}]}]}}))
        env = {**os.environ, "AGENT_HUB_ROOT": str(self.hub)}
        for _ in range(2):
            subprocess.run([sys.executable, str(mhs), "--worktree", str(f)], check=True, env=env, timeout=60,
                           capture_output=True)
        d = json.loads(f.read_text())
        cmds = [h["command"] for es in d["hooks"].values() for e in es for h in e["hooks"]]
        self.assertEqual(d["model"], "x")
        self.assertIn("echo mine", cmds)
        self.assertFalse([c for c in cmds if "agent-hub/" in c], cmds)
        self.assertEqual(len([c for c in cmds if "worktree-memory.sh" in c]), 5, cmds)

    def test_backup_placeholders(self):
        with mock.patch.object(hubconf, "STATE", self.hub / ".state"):
            self.assertEqual(hubconf.path(".state"), self.hub / ".state")
            self.assertEqual(hubconf.path(".state/x"), self.hub / ".state" / "x")
            self.assertEqual(hubconf.path(".secrets"), hubconf.secrets_dir())
            self.assertEqual(hubconf.path(".secrets/backup-password"), hubconf.secrets_dir() / "backup-password")
        example = json.loads((ROOT / "hub.example.json").read_text())
        self.assertEqual(example["backup"]["include"], [".state", ".secrets"])


class HubsOwnSessions(unittest.TestCase):
    """agent-hub's own sessions keep what its committed settings gave them (a pull of the main checkout at session
    start, the mailbox), now through hub-event like every project; the agent-settings applier installs those hooks in
    the hub and keeps every onboarded project's pointing at where the hub is."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.hub = self.tmp / "hub"
        self.hub.mkdir()
        for name in ("ROOT", "MAIN"):
            p = mock.patch.object(hubconf, name, self.hub)
            p.start()
            self.addCleanup(p.stop)

    def test_session_start_pulls_only_the_hubs_main_checkout(self):
        he = load("hub_event", BIN / "hub-event")
        self.assertEqual(he.HANDLERS["session_start"][0][0], "hub_pull")
        wt = self.tmp / "hub-worktree"
        wt.mkdir()
        for ws, pulled in ((self.hub, True), (wt, False), (self.tmp, False), (None, False)):
            with self.subTest(ws=ws), mock.patch.object(he.subprocess, "run") as run:
                he.hub_pull(None, ws)
                self.assertEqual(run.called, pulled)
                if pulled:
                    self.assertEqual(run.call_args.args[0][1:], ["git", "pull", "-q"])
                    self.assertEqual(run.call_args.kwargs["cwd"], self.hub)

    def test_applier_installs_the_hub_and_repoints_projects(self):
        import runtime_ports
        rps = load("render_project_settings", BIN / "render-project-settings.py")
        on, sandboxed, plain = (self.tmp / n for n in ("on", "sandboxed", "plain"))
        for d in (on, sandboxed):
            (d / ".agent-memory").mkdir(parents=True)
        plain.mkdir()
        old = {"hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": "[ -x /moved/away/bin/hub-event ] && /moved/away/bin/hub-event session_start || true"},
            {"type": "command", "command": "echo mine"}]}]}}
        (on / ".claude").mkdir()
        (on / ".claude" / "settings.local.json").write_text(json.dumps(old))
        index = {"v": 1, "errors": {}, "projects": {
            hubconf.hub_project(): {"name": hubconf.hub_project()},
            "on": {"name": "on", "paths": {"workspace": str(on)}},
            "sandboxed": {"name": "sandboxed", "paths": {"workspace": str(sandboxed)}, "enforce": {"sandbox": True}},
            "plain": {"name": "plain", "paths": {"workspace": str(plain)}}}}
        with mock.patch.object(runtime_ports, "default_name", lambda: "claude"):
            want = sorted([f"agent-settings:{hubconf.hub_project()}", "agent-settings:on"])
            self.assertEqual(sorted(c.target for c in rps.lifecycle(index, apply=False)), want)
            self.assertFalse((self.hub / ".claude").exists(), "a plan writes nothing")
            self.assertEqual(sorted(c.target for c in rps.lifecycle(index, apply=True)), want)
            self.assertEqual(rps.lifecycle(index, apply=False), [], "applied: nothing left")
        self.assertFalse((sandboxed / ".claude").exists(), "a sandboxed project's hooks are part of its settings")
        self.assertFalse((plain / ".claude").exists(), "not onboarded here: left alone")
        for ws in (self.hub, on):
            cmds = [h["command"] for es in json.loads((ws / ".claude" / "settings.local.json").read_text())["hooks"].values()
                    for e in es for h in e["hooks"]]
            self.assertEqual(sorted(c for c in cmds if c != "echo mine"),
                             sorted(runtime_claude.hook_command(ev) for _n, _m, ev in runtime_claude.HOOK_TABLE))
        self.assertIn("echo mine", (on / ".claude" / "settings.local.json").read_text())


# the hub's own location as text: ~/agent-hub, $HOME/agent-hub, ${HOME}/agent-hub
LITERAL = re.compile(r"~/agent-hub\b|\$\{?HOME\}?/agent-hub\b")
SKIP_DIRS = ("docs", "tests", "locales")
# file → why it may name it (a line matching the pattern given is allowed, nothing else in the file)
ALLOWED = {
    "bin/migrate-to-mini.py": (re.compile(r""), "runs on the remote machine, for the layout it moves from"),
    "bin/sync-local-setup.sh": (re.compile(r"^leak_re=|this machine's paths \("),
                                "the leak detector looks for exactly this text in projects' committed files"),
    "install.sh": (re.compile(r'^DIR="\$\{AGENT_HUB_DIR:-\$HOME/agent-hub\}"$'),
                   "the one-line installer's default clone folder (AGENT_HUB_DIR overrides it); nothing reads it later"),
}


def tracked() -> list[str]:
    """The repo's versioned files, relative (git ls-files; outside git, e.g. the test runner's exported copy, a walk)."""
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, text=True).stdout
    rels = [q for q in out.split("\0") if q]
    if not rels:
        rels = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
                if p.is_file() and not {".git", "__pycache__"} & set(p.relative_to(ROOT).parts)]
    return sorted(rels)


class NothingShipsTheOldLayout(unittest.TestCase):
    def test_no_tracked_file_names_the_hub_at_home(self):
        hits = []
        for rel in tracked():
            if rel.split("/", 1)[0] in SKIP_DIRS or rel.endswith(".md"):
                continue
            try:
                lines = (ROOT / rel).read_text().splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            allowed = ALLOWED.get(rel, (None, ""))[0]
            hits += [f"{rel}:{i}: {line.strip()}" for i, line in enumerate(lines, 1)
                     if LITERAL.search(line) and not (allowed and allowed.search(line))]
        self.assertFalse(hits, "the hub's location comes from hubconf (hub_bin, sh_py, MAIN) or hubpaths.sh "
                               "(HUB_MAIN, HUB_STATE), never ~/agent-hub:\n" + "\n".join(hits))

    def test_the_pattern_catches_what_it_is_for(self):
        for bad in ['"command": "~/agent-hub/bin/with-lock.sh git pull -q"', 'LOG_DIR="$HOME/agent-hub/.logs"',
                    '"~/agent-hub/.state",', 'mv ${HOME}/agent-hub/.state/a.sha']:
            self.assertTrue(LITERAL.search(bad), bad)
        for ok in ["agent-hub/claude-login", "~/.agent-hub/config", "refs/agent-hub/incoming", "~/agent-hubs"]:
            self.assertFalse(LITERAL.search(ok), ok)


if __name__ == "__main__":
    unittest.main()
