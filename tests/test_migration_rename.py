"""The rename to brunna across releases (docs/design/rename-brunna.md, "Proving it"): an instance made by v0.5.0 is
taken through v0.6.0 to this code one release at a time, in one jump, and back from this code to v0.6.0. Each release
is its own tree (v0.5.0 and v0.6.0 from the repo's tags, this one a plain copy) over one instance and one checkout
state (.state is shared, as it is when one checkout moves between releases).

After each step: every open letter is in the platform's mailbox, the owner's task is still filed and found, the daily
job that ran today is not due again (scheduler state adopted across the platform's two names), and doctor runs.
Units are out of scope here: a test's HOME never reaches the account's service manager (hubconf.home_is_the_accounts);
the pairwise unit installer has its own tests. Skipped where the tags are not there.
"""
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TAGS = ("v0.5.0", "v0.6.0")
# where a rollback lands: the oldest release upgrade goes back to (bin/upgrade.py OLDEST_BACK). Before it is tagged, a
# ref can stand in for it: MIGRATION_BACK_REF=<branch>
BACK = os.environ.get("MIGRATION_BACK_REF") or "v0.6.1"
TODAY = datetime.date.today().isoformat()
TASK = "task-agent-hub-0001"

# a tree's view, run with that tree's own code: its platform's name, its mailbox, its records, the daily jobs due late
# today after its scheduler adopted the state (and saved it, as a tick does)
PROBE = r"""
import datetime, io, json, subprocess, sys, contextlib
root = sys.argv[1]
sys.path.insert(0, root + "/bin")
import hubconf, scheduler
hub = hubconf.hub_project()
def run(*a):
    return subprocess.run([sys.executable, root + "/bin/hubbus", *a], capture_output=True, text=True)
jobs, _info = scheduler.all_jobs()
state = json.loads(scheduler.STATE_FILE.read_text()) if scheduler.STATE_FILE.exists() else {}
for j in jobs:
    getattr(scheduler, "adopt_state", lambda *a: None)(j, state)
scheduler.save_state(state)
now = datetime.datetime.combine(datetime.date.today(), datetime.time(23, 58))
due = [j["name"] for j in jobs if j.get("enabled", True) and "time" in j
       and scheduler.is_due(j, state, now, now.date().isoformat())]
print(json.dumps({"hub": hub, "inbox": run("inbox", "--project", hub).stdout,
                  "records": run("records", "--project", hub).stdout, "due": due}))
"""


def _has(ref):
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "-q", "--verify", f"{ref}^{{commit}}"],
                              capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _has_tags():
    return all(_has(f"refs/tags/{t}") for t in TAGS)


@unittest.skipUnless(_has_tags(), f"tags {', '.join(TAGS)} not in this checkout")
class Rename(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = self.tmp = pathlib.Path(self._tmp.name).resolve()
        (tmp / "home").mkdir()
        self.state = tmp / "state"
        self.state.mkdir()
        self.trees = {t: self.archive(t) for t in TAGS}
        if _has(BACK):
            self.trees["back"] = self.trees.get(BACK) or self.archive(BACK)
        self.trees["now"] = self.copy_now()
        for tree in set(self.trees.values()):   # one checkout's state, whichever release is checked out
            (tree / ".state").symlink_to(self.state)
        self.inst = tmp / "inst"
        self.base = {k: v for k, v in os.environ.items()
                     if not k.startswith(("AGENT_HUB", "AGENTHUB", "BRUNNA", "GIT_", "NVM_"))}
        self.base.update(HOME=str(tmp / "home"), PATH="/usr/bin:/bin", AGENT_HUB_PYTHON=sys.executable,
                         AGENT_HUB_LANG_UI="en", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                         GIT_COMMITTER_EMAIL="t@t")
        self.letters = []
        old = self.trees["v0.5.0"]
        r = self.run_as(old, "agenthub", "init", "--yes", "--no-launchd", "--owner", "Alex", "--workspace",
                        tmp / "ws", "--home", self.inst)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.send(old, "agent-hub", "written by v0.5")
        rec = {"ph": "0.4", "ext": "acts/0.4", "id": f"r:{TASK}", "type": "raised", "origin": "requester",
               "by": "agent-hub:registrar", "key": TASK, "project": "agent-hub", "ts": "2026-11-01T01:00:00Z",
               "terms": {"text": "Renew the certificate"}, "terms_rev": 1}
        r = self.run_as(old, "hubbus", "record-publish", "--json", "-", stdin=json.dumps(rec))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # the daily to-do job ran today under v0.5's name for it
        (self.state / "scheduler-last-run.json").write_text(json.dumps({"agent-hub/todo": TODAY}))

    def archive(self, tag):
        d = self.tmp / tag.replace("/", "-")
        d.mkdir()
        out = subprocess.run(["git", "-C", str(ROOT), "archive", tag], capture_output=True, check=True, timeout=120)
        subprocess.run(["tar", "-x", "-C", str(d)], input=out.stdout, check=True, timeout=120)
        return d

    def copy_now(self):
        d = self.tmp / "now"
        listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True, timeout=60)
        for rel in filter(None, listed.stdout.decode().split("\0")):
            src, dst = ROOT / rel, d / rel
            if os.path.lexists(src):
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst, follow_symlinks=False)
        return d

    def run_as(self, root, script, *args, stdin=None):
        env = {**self.base, "AGENT_HUB_ROOT": str(root), "AGENT_HUB_HOME": str(self.inst),
               "AGENT_HUB_STATE": str(self.state)}
        return subprocess.run([sys.executable, str(root / "bin" / script), *map(str, args)], env=env, cwd=self.tmp,
                              input=stdin, capture_output=True, text=True, timeout=300)

    def send(self, root, to, text):
        r = self.run_as(root, "hubbus", "send", "--to", to, "--from", to, "--type", "request", "--text", text)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.letters.append(r.stdout.strip().split()[-1])

    def view(self, which):
        root = self.trees[which]
        env = {**self.base, "AGENT_HUB_ROOT": str(root), "AGENT_HUB_HOME": str(self.inst),
               "AGENT_HUB_STATE": str(self.state)}
        r = subprocess.run([sys.executable, "-c", PROBE, str(root)], env=env, cwd=self.tmp, capture_output=True,
                           text=True, timeout=300)
        self.assertEqual(r.returncode, 0, f"{which}: {r.stdout}{r.stderr}")
        return json.loads(r.stdout)

    def check(self, which):
        v = self.view(which)
        hub = v["hub"]
        for mid in self.letters:
            self.assertIn(mid, v["inbox"], f"{which}: letter {mid} is in {hub}'s mailbox")
        self.assertIn(TASK, v["records"], f"{which}: the owner's task is still filed and found")
        self.assertNotIn(f"{hub}/todo", v["due"], f"{which}: the daily job that ran today does not run again")
        self.assertTrue(v["due"], f"{which}: other daily jobs are still due (the probe works)")
        r = self.run_as(self.trees[which], "agenthub", "doctor")
        self.assertNotIn("Traceback", r.stdout + r.stderr, which)
        return hub

    def test_one_release_at_a_time(self):
        self.assertEqual(self.check("v0.6.0"), "agent-hub")
        self.send(self.trees["v0.6.0"], "agent-hub", "written by v0.6")
        self.assertEqual(self.check("now"), "brunna")
        self.send(self.trees["now"], "brunna", "written by v0.7")
        self.check("now")

    def test_in_one_jump(self):
        self.assertEqual(self.check("now"), "brunna")
        self.send(self.trees["now"], "agent-hub", "to the old name, after the switch")
        self.check("now")

    @unittest.skipUnless(_has(BACK), f"{BACK} is not in this checkout (MIGRATION_BACK_REF=<ref> stands in for it)")
    def test_back(self):
        self.check("now")
        self.send(self.trees["now"], "brunna", "written by v0.7")
        self.assertEqual(self.check("back"), "agent-hub")


if __name__ == "__main__":
    unittest.main()
