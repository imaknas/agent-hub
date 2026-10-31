"""E1 golden (docs/design/i18n.md) for bin/worktrees.py and bin/prune-worktrees, on zh-Hant: what the manual prune
prints (with and without --yes), the result lines of the nightly auto prune (deps deleted, worktree removed, skipped
for unknown ignored files or a failed `git worktree remove`) and the rebuild marker it leaves, and how
prune-worktrees --auto prints a result (dry run or not).

In-process; overview() and the git/du calls are replaced, the deps folder is a real temp dir.

    bin/py -m unittest tests.test_i18n_golden_worktrees
    I18N_GOLDEN_UPDATE=1 bin/py -m unittest tests.test_i18n_golden_worktrees    # regenerate (only before a conversion)
"""
import contextlib
import io
import os
import pathlib
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hubconf  # noqa: E402
import worktrees  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_lang  # noqa: E402

GOLDEN = golden_lang.GOLDEN
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
ZH = {"AGENT_HUB_LANG_UI": golden_lang.LANG}


def wt(path, branch, merged=True, clean=True, age=3.0):
    return {"path": str(path), "branch": branch, "merged": merged, "clean": clean, "exists": True, "age_days": age}


class Golden(unittest.TestCase):
    maxDiff = None

    def scenario(self, tmp: pathlib.Path) -> str:
        res = []
        repo = tmp / "repo"
        repo.mkdir()
        manual = {"proj": {"worktrees": [wt(tmp / "a", "feat-a", age=1.5), wt(tmp / "b", "feat-b", merged=False),
                                         wt(tmp / "c", "feat-c", clean=False)]}}
        ok = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.dict(os.environ, ZH), mock.patch.object(worktrees, "overview", lambda: manual), \
                mock.patch.object(hubconf, "workspace", lambda name: repo), \
                mock.patch.object(worktrees.subprocess, "run", lambda *a, **k: ok):
            for yes in (False, True):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    worktrees.prune(None, yes)
                res.append((f"prune yes={yes}", buf.getvalue()))

        # auto prune: tier 2 removed / skipped (unknown ignored) / skipped (remove failed); tier 1 deps
        old, deps = tmp / "old", tmp / "deps"
        (deps / ".venv").mkdir(parents=True)
        (deps / "uv.lock").write_text("lock\n")
        gitdir = tmp / "gitdir"
        gitdir.mkdir()
        auto = {"proj": {"worktrees": [wt(old, "old-ok", age=20.0), wt(tmp / "secret", "old-env", age=20.0),
                                       wt(tmp / "broken", "old-fail", age=20.0), wt(deps, "idle", age=3.0)]}}
        unknown = {str(tmp / "secret"): [".env", "build", "dist", "more"]}

        def run(argv, **kw):
            if argv[-1] == str(tmp / "broken"):
                return subprocess.CompletedProcess(argv, 1, "", "fatal: cannot remove\n")
            if "remove-dep" in argv:   # the project's job writes the marker into its git dir
                with open(gitdir / argv[-2], "a") as fh:
                    fh.write(argv[-1] + "\n")
            return ok

        with mock.patch.dict(os.environ, ZH), mock.patch.object(worktrees, "overview", lambda: auto), \
                mock.patch.object(hubconf, "workspace", lambda name: repo), \
                mock.patch.object(hubconf, "STATE", tmp / "state"), \
                mock.patch.object(worktrees, "_cwds_in_use", lambda: []), \
                mock.patch.object(worktrees, "_transcript_age_days", lambda path: None), \
                mock.patch.object(worktrees, "_unknown_ignored", lambda p, project: unknown.get(str(p), [])), \
                mock.patch.object(worktrees, "_size_mb", lambda p: 12.5), \
                mock.patch.object(worktrees.subprocess, "run", run):
            for dry in (True, False):
                r = worktrees.auto_prune(dry_run=dry)
                for k in ("deps", "removed", "skipped"):
                    res.append((f"auto dry_run={dry}: {k}", "".join(line + "\n" for line in r[k])))
            marker = (gitdir / worktrees.MARKER).read_text()
            res.append(("marker", marker[11:]))   # without the date

        script = str(ROOT / "bin" / "prune-worktrees")
        for dry in (True, False):
            fixed = {"dry_run": dry, "deps": ["d1 .venv"], "removed": ["r1"], "skipped": ["s1"], "freed_mb": 7.5}
            buf = io.StringIO()
            with mock.patch.dict(os.environ, ZH), mock.patch.object(worktrees, "auto_prune", lambda dry_run: fixed), \
                    mock.patch.object(sys, "argv", ["prune-worktrees", "--auto"]), contextlib.redirect_stdout(buf):
                try:
                    runpy.run_path(script, run_name="__main__")
                except SystemExit:
                    pass
            res.append((f"prune-worktrees --auto (dry_run={dry})", buf.getvalue()))
        text = "".join(f"=== {k}\n{v}" for k, v in res)
        return text.replace(str(tmp), "<TMP>")

    def test_texts(self):
        with tempfile.TemporaryDirectory() as d:
            text = self.scenario(pathlib.Path(d).resolve())
        f = GOLDEN / "worktrees.txt"
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())


if __name__ == "__main__":
    sys.exit(unittest.main())
