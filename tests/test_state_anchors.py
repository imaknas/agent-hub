"""bin/state_anchors.py (docs/design/state-anchors.md): the anchor syntax, that anchors never change how either to-do
token set reads a line, and every outcome of resolving an anchor against a real temp repository.

    bin/py -m unittest tests.test_state_anchors
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import state_anchors as sa  # noqa: E402
import todo_tokens  # noqa: E402

OWNER = "Alex"
ZH = todo_tokens.tokens("zh-Hant")   # the zh-Hant set, built from its tokens (no Chinese in test code)
ZH_ITEM = {r: todo_tokens.tag("zh-Hant", r, OWNER) for r in todo_tokens.ROLES}
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
       "GIT_AUTHOR_DATE": "2026-11-01T00:00:00+00:00", "GIT_COMMITTER_DATE": "2026-11-01T00:00:00+00:00"}


class Syntax(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(sa.parse("src/a.py"), ("src/a.py", None, None))
        self.assertEqual(sa.parse("src/"), ("src/", None, None))
        self.assertEqual(sa.parse("src/a.py#run_job"), ("src/a.py", "run_job", None))
        self.assertEqual(sa.parse("app/models.py#Order.total"), ("app/models.py", "Order.total", None))
        for raw, code in [("", "empty"), ("#x", "empty"), ("https://x.org/a", "url"), ("/etc/hosts", "absolute"),
                          ("~/x.py", "absolute"), ("a/../b.py", "parent"), ("a.py:42", "line-number"),
                          ("a.py:3-9", "line-number"), ("a.py#42", "symbol-name"), ("a.py#do it", "symbol-name"),
                          ("src/#main", "directory-symbol"), ("memory/2026-11-01.md", "memory"),
                          (".agent-memory/x", "memory"), ("STATE.md", "memory")]:
            self.assertEqual(sa.parse(raw)[2], code, raw)
            self.assertIn(code, sa.PROBLEMS)

    def test_find_skips_todo_sections_and_fences(self):
        text = ("# STATE\n\n## Now\n\n- The job is in ⚓`src/job.py#run` and ⚓️ `src/lib/`.\n"
                "1. Not an anchor: `src/x.py`, ⚓ plain.\n\n```\n⚓`in/fence.py`\n```\n\n"
                f"## Tasks\n\n- [ ] [agent] Write ⚓`src/new.py`\n\n{ZH['todo_heading']}\n\n"
                f"- [ ] {ZH_ITEM['agent']}write ⚓`src/zh.py`\n\n"
                "## After\n\n### ⚓`docs/a.md` heading\n")
        found = sa.find(text, OWNER)
        self.assertEqual([a.raw for a in found], ["src/job.py#run", "src/lib/", "docs/a.md"])
        self.assertEqual(found[0].line_no, 5)
        self.assertEqual(found[0].statement, "The job is in ⚓`src/job.py#run` and ⚓️ `src/lib/`.")
        self.assertEqual(found[2].statement, "⚓`docs/a.md` heading")
        self.assertEqual(sa.in_todo(text, OWNER), 2)
        self.assertEqual(sa.count(text, OWNER), 3)


class TokenSets(unittest.TestCase):
    """An anchor anywhere in a to-do line changes nothing either set reads: role, text start, key."""

    LINES = ["- [ ] [owner] Renew the domain{a} (waiting on owner: task-p-0123456789)",
             "- [ ] [agent] Move{a} to the new layout",
             "- [ ] [undecided]{a} pick a queue",
             f"- [ ] {ZH_ITEM['owner']}renew{{a}}" + todo_tokens.stamp("zh-Hant", OWNER, "task-p-0123456789"),
             f"- [ ] {ZH_ITEM['agent']}move{{a}}",
             f"- [ ] {ZH_ITEM['tbd']}{{a}}pick",
             "- [ ] bare item{a}",
             "### [owner]",
             f"### {ZH_ITEM['agent']}"]

    def test_patterns_read_the_same(self):
        P = todo_tokens.patterns(OWNER)
        for tmpl in self.LINES:
            plain, anchored = tmpl.format(a=""), tmpl.format(a=" ⚓`src/a.py#f`")
            for name in ("item", "labelled", "bare", "tag_start", "no_box", "group", "key"):
                pat = getattr(P, name)
                m1, m2 = (pat.match(plain) if name != "key" else pat.search(plain),
                          pat.match(anchored) if name != "key" else pat.search(anchored))
                self.assertEqual(m1 is None, m2 is None, f"{name}: {tmpl}")
                if m1 is not None and name in ("item", "group"):
                    self.assertEqual(P.role(m1), P.role(m2), tmpl)
                if m1 is not None and name == "key":
                    self.assertEqual(m1.group("key"), m2.group("key"), tmpl)
            self.assertEqual([m.group("key") for m in P.key_mark.finditer(plain)],
                             [m.group("key") for m in P.key_mark.finditer(anchored)])

    def test_anchor_alone_is_not_a_label_or_key(self):
        P = todo_tokens.patterns(OWNER)
        for line in ("- ⚓`src/a.py` is the entry point", "⚓`a.py#f`", "- [ ] ⚓`a.py` first"):
            self.assertIsNone(P.no_box.match(line), line)
            self.assertIsNone(P.key_mark.search(line), line)
            self.assertIsNone(P.labelled.match(line), line)


class RepoCase(unittest.TestCase):
    """a repository with every case: a file renamed and one deleted on main, an unmerged branch, a merged one, the
    memory branch, untracked and ignored files"""

    def setUp(self):
        self.repo = pathlib.Path(tempfile.mkdtemp())
        self.git("init", "-q", "-b", "main")
        self.write("src/job.py", "def run_job():\n    return 1\n\nclass Order:\n    def total(self): ...\n")
        self.write("src/old.py", "x = 1\n")
        self.write("src/gone.py", "y = 2\n")
        self.write("src/lib/util.py", "def helper(): ...\n")
        self.write("other/moved_here.py", "def run_job(): ...\n")
        self.commit("init")
        self.git("mv", "src/old.py", "src/new.py")
        self.git("rm", "-q", "src/gone.py")
        self.commit("rename and delete")
        self.git("branch", "merged")                    # an old, merged branch: never a reason for pending
        self.git("checkout", "-q", "--orphan", "agent-memory")
        self.git("rm", "-rq", "--cached", ".")
        self.write("src/memonly.py", "m\n")
        self.git("add", "src/memonly.py")
        self.git("commit", "-q", "-m", "memory branch")
        (self.repo / "src/memonly.py").unlink()
        self.git("checkout", "-q", "-f", "main")
        self.git("checkout", "-q", "-b", "feature")
        self.write("src/feature.py", "z = 3\n")
        self.commit("feature")
        self.git("checkout", "-q", "main")
        self.write("src/dirty.py", "uncommitted\n")   # the working tree is not a fact
        self.write("data/cache.bin", "x")
        self.write(".gitignore", "data/\n")

    def git(self, *a, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or self.repo), *a], check=True, capture_output=True, text=True,
                              env=ENV).stdout

    def write(self, rel, text):
        f = self.repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)


class Resolve(RepoCase):
    def outcomes(self, raws, ws=None):
        repo, why = sa.Repo.open(ws or self.repo)
        self.assertEqual(why, "")
        return [(r.outcome, r.hint) for r in (repo.resolve(a) for a in
                                               sa.find("\n".join(f"- ⚓`{x}`" for x in raws), OWNER))]

    def test_outcomes(self):
        sha = self.git("log", "-1", "--format=%h", "--abbrev=8", "main").strip()
        got = self.outcomes(["src/job.py", "src/lib/", "src/lib", "src/job.py#run_job", "src/job.py#Order.total",
                             "src/job.py#Order.missing", "src/lib/util.py#run_job", "src/old.py", "src/gone.py",
                             "src/feature.py", "src/dirty.py", "nowhere.py", "src/job.py/", "a.py:3", "data/",
                             "src/memonly.py"])
        self.assertEqual(got, [
            ("ok", ""), ("ok", ""), ("ok", ""), ("ok", ""), ("ok", ""),
            ("symbol", ""), ("symbol", "other/moved_here.py, src/job.py"),
            ("renamed", "src/new.py"), ("deleted", sha),
            ("pending", "feature"), ("untracked", ""), ("missing", ""), ("ok", ""), ("malformed", "line-number"),
            ("untracked", ""), ("missing", "")])

    def test_only_unmerged_branches_count(self):
        repo, _ = sa.Repo.open(self.repo)
        self.assertEqual(repo.others(), ["feature"])

    def test_history_wins_over_an_old_branch(self):
        self.git("checkout", "-q", "-b", "stale", "HEAD~1")   # still has src/gone.py, and is not merged
        self.write("src/extra.py", "e\n")
        self.commit("extra")
        self.git("checkout", "-q", "main")
        self.assertEqual(self.outcomes(["src/gone.py"])[0][0], "deleted")

    def test_symbol_hint_excludes_its_own_file(self):
        got = self.outcomes(["src/lib/util.py#run_job"])
        self.assertEqual(got[0][0], "symbol")
        self.assertNotIn("src/lib/util.py", got[0][1])

    def test_workspace_in_a_subfolder(self):
        got = self.outcomes(["job.py", "lib/util.py#helper", "old.py", "../src/job.py"], ws=self.repo / "src")
        sha = self.git("log", "-1", "--format=%h", "--abbrev=8", "main").strip()
        self.assertEqual(got, [("ok", ""), ("ok", ""), ("renamed", "new.py"), ("malformed", "parent")])
        repo, _ = sa.Repo.open(self.repo / "src")
        self.assertEqual(repo.resolve(sa.find("- ⚓`old.py`", OWNER)[0]).commit, sha)

    def test_master_is_a_base_branch(self):
        self.git("branch", "-m", "main", "master")
        self.assertEqual(self.outcomes(["src/job.py"]), [("ok", "")])

    def test_not_checked(self):
        self.assertEqual(sa.Repo.open(None), (None, "no-workspace"))
        self.assertEqual(sa.Repo.open(self.repo / "nope"), (None, "no-directory"))
        plain = pathlib.Path(tempfile.mkdtemp())
        self.assertEqual(sa.Repo.open(plain), (None, "not-git"))
        self.git("branch", "-m", "main", "trunk")
        self.assertEqual(sa.Repo.open(self.repo), (None, "no-base-branch"))
        self.assertEqual(sa.check("- ⚓`src/job.py`\n", OWNER, self.repo), ([], "no-base-branch"))
        self.assertEqual(sa.check("- no anchors\n", OWNER, None), ([], ""))

    def test_stale_excludes_ok_and_pending(self):
        results, why = sa.check("- ⚓`src/job.py`\n- ⚓`src/feature.py`\n- ⚓`src/gone.py`\n", OWNER, self.repo)
        self.assertEqual([r.outcome for r in sa.stale(results)], ["deleted"])


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TodoBlock(unittest.TestCase):
    """check-state-anchors.py's block in todo.md: added at the end, replaced, removed, and the memory-gaps block left
    alone"""

    def test_block(self):
        m = load("check_state_anchors_t", "check-state-anchors.py")
        todo = pathlib.Path(tempfile.mkdtemp()) / "todo.md"
        gaps = "<!-- memory-gaps:begin -->\n## gaps\n\n- [ ] **g**\n<!-- memory-gaps:end -->\n"
        todo.write_text("# todo\n\n- [ ] x\n\n" + gaps)
        m.write_todo_block(todo, ["- [ ] **a**: one"])
        m.write_todo_block(todo, ["- [ ] **b**: two"])
        s = todo.read_text()
        self.assertEqual(s.count("<!-- state-anchors:begin -->"), 1)
        self.assertIn("**b**", s)
        self.assertNotIn("**a**", s)
        self.assertIn(gaps, s)
        m.write_todo_block(todo, [])
        self.assertNotIn("state-anchors", todo.read_text())
        self.assertIn(gaps, todo.read_text())


class Librarian(RepoCase):
    """the librarian resolves a project's anchors before its call and tells the model about the stale ones"""

    def test_stale_note(self):
        lib = load("librarian_anchors_t", "librarian.py")
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "STATE.md").write_text("- Runs ⚓`src/job.py#run_job`.\n- Exports ⚓`src/gone.py`.\n"
                                    "- Parses ⚓`src/feature.py`.\n- Settings ⚓`src/old.py`.\n")
        with mock.patch.object(lib.hubconf, "roster", return_value={"demo": {"workspace": str(self.repo)}}):
            stale = lib.stale_anchors("demo", d)
        self.assertEqual([r["outcome"] for r in stale], ["deleted", "renamed"])
        note = lib.anchor_note(stale)
        self.assertIn("- line 2, ⚓`src/gone.py`: the file was deleted on main (commit ", note)
        self.assertIn("- line 4, ⚓`src/old.py`: the file was renamed to `src/new.py` (commit ", note)
        self.assertNotIn("feature", note)
        self.assertEqual(lib.anchor_note([]), "")
        with mock.patch.object(lib.hubconf, "roster", return_value={}):
            self.assertEqual(lib.stale_anchors("demo", d), [])


class EvalScores(unittest.TestCase):
    def test_anchor_scores(self):
        sys.path.insert(0, str(ROOT / "bin" / "evals"))
        import e2
        case = pathlib.Path(tempfile.mkdtemp())
        (case / "args.json").write_text(json.dumps({
            "owner": OWNER, "project": "nowhere", "diff": "+ moved the job to src/run.py",
            "anchors": [{"raw": "src/gone.py"}]}))
        before = "- Job ⚓`src/job.py`.\n- Export ⚓`src/gone.py`.\n\n## Tasks\n\n- (none)\n"
        after = ("- Job ⚓`src/run.py`, helper ⚓`src/guess.py`.\n\n## Tasks\n\n"
                 "- [ ] [agent] Write ⚓`src/later.py`\n")
        r = e2.anchor_scores(case, before, after)
        self.assertEqual((r["anchors"], r["added"], r["grounded"], r["in_todo"], r["retention"], r["stale_fixed"]),
                         (2, 2, 0.5, 1, 0.0, 1.0))
        self.assertIsNone(r["resolve"])   # the project is not in the registry


if __name__ == "__main__":
    unittest.main()
