"""A new user sees only brunna (docs/design/rename-brunna.md, "Proving it"): a fresh install of this code into an empty
HOME (init, the CLI wrapper, the config plan, a letter on the bus, doctor), and then everything it created under HOME
(paths and file contents) and everything it printed is searched for the old names. The repo's own source is not: it
has to know the old spellings to read an older install.

Allowed: the per-repo lock agent-hub-op.lock (fixed for good), and the project-facing job variables written in both
spellings (names.DOCUMENTED); neither appears in a fresh install anyway.
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import names  # noqa: E402

OLD = re.compile(r"agent-hub|agenthub|AGENT_HUB|AGENTHUB", re.I)
ALLOWED = re.compile(r"agent-hub-op\.lock|\b(?:AGENT_HUB|AGENTHUB)_(?:" + "|".join(sorted(names.DOCUMENTED)) + r")\b"
                     # the Python this test borrows (a new install builds its own under ~/.local/share/brunna)
                     + "|" + re.escape(str(pathlib.Path(sys.executable).parents[1])))


class FreshInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        (self.home / ".local" / "bin").mkdir(parents=True)
        # the code, as a new user's checkout: a plain copy at ~/brunna
        self.hub = self.home / "brunna"
        listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, timeout=60)
        rels = [r for r in listed.stdout.decode().split("\0") if r] if listed.returncode == 0 else \
            [str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts
             and "__pycache__" not in p.parts]
        for rel in rels:
            src, dst = ROOT / rel, self.hub / rel
            if os.path.lexists(src) and not src.is_dir():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst, follow_symlinks=False)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("AGENT_HUB", "AGENTHUB", "BRUNNA", "GIT_", "NVM_"))}
        self.env.update(HOME=str(self.home), PATH=f"{self.home / '.local' / 'bin'}:/usr/bin:/bin",
                        BRUNNA_PYTHON=sys.executable, BRUNNA_LANG_UI="en", GIT_AUTHOR_NAME="t",
                        GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        self.said = []

    def run_(self, script, *args):
        r = subprocess.run([sys.executable, str(self.hub / "bin" / script), *map(str, args)], env=self.env,
                           cwd=self.home, capture_output=True, text=True, timeout=300)
        self.said.append((f"{script} {' '.join(map(str, args))}", r.stdout + r.stderr))
        return r

    def test_nothing_a_new_user_meets_names_the_old_name(self):
        r = self.run_("agenthub", "init", "--yes", "--no-launchd", "--owner", "Alex", "--llm", "none",
                      "--workspace", self.home / "workspace")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.run_("agenthub", "install-cli")
        self.run_("agenthub", "config", "plan")
        self.run_("hubbus", "send", "--to", names.current("project"), "--from", names.current("project"),
                  "--type", "info", "--text", "hello")
        self.run_("agenthub", "doctor")
        self.assertTrue((self.home / ".brunna" / "config" / "brunna.json").is_file())
        self.assertEqual(sorted(p.name for p in (self.home / ".local" / "bin").iterdir()), ["brunna"])
        r = subprocess.run([str(self.home / ".local" / "bin" / "brunna"), "--version"], env=self.env, capture_output=True,
                           text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)   # the wrapper runs the CLI

        found = []
        tmp = str(self.tmp)   # where the test itself runs (a runner's folder may be named anything)
        for what, text in [(w, s.replace(tmp, "<tmp>")) for w, s in self.said]:
            found += [f"printed by `{what}`: {line.strip()[:160]}" for line in text.splitlines()
                      if OLD.search(ALLOWED.sub("", line))]
        for p in sorted(self.home.rglob("*")):
            if self.hub in (p, *p.parents):
                continue   # the code itself
            rel = p.relative_to(self.home)
            if OLD.search(str(rel)):
                found.append(f"path: ~/{rel}")
            if p.is_file() and not p.is_symlink() and p.stat().st_size < 2_000_000:
                try:
                    text = p.read_text()
                except (UnicodeDecodeError, OSError):
                    continue   # git objects and the bus database
                found += [f"~/{rel}: {line.strip()[:160]}" for line in text.replace(tmp, "<tmp>").splitlines()
                          if OLD.search(ALLOWED.sub("", line))]
        self.assertEqual(found, [], "a new install shows the old name")


if __name__ == "__main__":
    unittest.main()
