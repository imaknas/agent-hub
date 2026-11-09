"""brunna state history / restore (bin/state.py, docs/design/state-restore.md): a project's STATE.md as it was on an
earlier day, brought back as a new commit on the memory bare repo. The to-do list stays as it is today and everything
else goes back to that day; an item the owner was waiting on that was in the list that day and was deleted since,
still open, comes back; anything else restore leaves out, it lists.

Two layers: the rule (state.rebuild, a pure function of the two versions and the registrar's ledger) and the run (the
script against a temporary hub: memory bare repo, the platform's copy, a bus, the real registrar).

    bin/py -m unittest tests.test_state_restore
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))
import acts_md  # noqa: E402
import memlock  # noqa: E402
import names  # noqa: E402
import state  # noqa: E402
import todo_tokens  # noqa: E402
from test_registrar_flow import Env, TAG  # noqa: E402

READ = acts_md.profiles(TAG)
EN, ZH = acts_md.profile("en", TAG), acts_md.profile("zh-Hant", TAG)


def owner(text, k=None, lang="en"):
    return todo_tokens.item(lang, "owner", TAG, text) + (todo_tokens.stamp(lang, TAG, k) if k else "")


def agent(text, lang="en"):
    return todo_tokens.item(lang, "agent", TAG, text)


def heading(lang="en"):
    return todo_tokens.tokens(lang)["todo_heading"]


def doc(status, *todo, lang="en", after=""):
    return f"# STATE\n\n## Now\n\n{status}\n\n{heading(lang)}\n\n" + "".join(t + "\n" for t in todo) + after


def book(open_bound=(), closed=(), raised_here=(), terms=None):
    return state.Book(set(open_bound), set(closed), set(raised_here), dict(terms or {}))


class Rule(unittest.TestCase):
    """state.rebuild(T, C, book, read, write): R, or a refusal."""

    def rebuild(self, t, c, b, write=EN):
        r = state.rebuild(t, c, b, READ, write)
        if r.text is not None:
            self.invariants(t, c, b, r)
        return r

    def invariants(self, t, c, b, r):
        """the design's invariants: R's to-do sections are C's plus the re-added lines; R's other sections are T's;
        every open bound key C mentions is in R; no key on two lines that C didn't have twice; no unclosed block"""
        R, C = acts_md.Doc(r.text, READ), acts_md.Doc(c, READ)
        self.assertIsNone(R.unclosed)
        for k in b.open_bound & set(C.mentioned()):
            self.assertIn(k, R.mentioned(), k)
        for k in R.mentioned():
            on = [l for l in R.lines if k in l]
            self.assertLessEqual(len(on), max(1, sum(k in l for l in C.lines)), k)

    def test_a_deleted_open_item_comes_back(self):
        t = doc("The demo runs on the shared box.", owner("Renew the domain", "task-a"), agent("Write the notes"))
        c = doc("Everything moved to a new box (wrong).", agent("Rewrite the notes"))
        r = self.rebuild(t, c, book(open_bound=["task-a"], terms={"task-a": "Renew the domain"}))
        self.assertIsNone(r.refused)
        self.assertIn("The demo runs on the shared box.", r.text)
        self.assertNotIn("(wrong)", r.text)
        self.assertIn(agent("Rewrite the notes"), r.text, "the to-do list is today's")
        self.assertNotIn("Write the notes", r.text)
        self.assertIn(EN.principal_line("Renew the domain", "task-a"), r.text)
        self.assertEqual(r.readded, ["task-a"])
        R = acts_md.Doc(r.text, READ)
        self.assertEqual([e.key for e in R.tasks() if e.owner == "principal"], ["task-a"])

    def test_re_added_from_the_latest_terms(self):
        t = doc("then", owner("Renew the domain", "task-a"))
        c = doc("now", agent("Something"))
        r = self.rebuild(t, c, book(open_bound=["task-a"], terms={"task-a": "Renew the domain for two years"}))
        self.assertIn(EN.principal_line("Renew the domain for two years", "task-a"), r.text)

    def test_todays_list_whole_and_verbatim(self):
        """ticks, continuation lines, groups, agent and undecided items, closed items: C's, as C has them"""
        t = doc("then", owner("Pay rent", "task-p"), agent("Old agent item"))
        todo = [owner("Pick a host", "task-b"), "  continued on the next line", "- [x] [owner] Done one",
                agent("New agent item"), todo_tokens.item("en", "tbd", TAG, "Decide later"), "", "### [agent]", "",
                "- [ ] grouped item"]
        c = doc("now", *todo)
        r = self.rebuild(t, c, book(open_bound=["task-b"], closed=["task-p"], terms={"task-b": "Pick a host"}))
        self.assertIn("\n".join(todo), r.text)
        self.assertNotIn("Pay rent", r.text, "closed since: not brought back")
        self.assertNotIn("Old agent item", r.text)
        self.assertEqual(r.readded, [])
        self.assertIn(("closed", "task-p", "Pay rent"), r.left)

    def test_two_to_do_sections_move_whole_to_the_first_place(self):
        h = heading()
        t = f"# STATE\n\n{h}\n\n{owner('Old ask')}\n\n## Notes\n\nnotes then\n\n{h}\n\n- (none)\n"
        c = f"# STATE\n\n{h}\n\n{agent('A')}\n\n## Elsewhere\n\nnow\n\n{h}\n\n{owner('Archived', 'task-z')}\n"
        r = self.rebuild(t, c, book(raised_here=[]))
        self.assertEqual(r.text, f"# STATE\n\n{h}\n\n{agent('A')}\n\n{h}\n\n{owner('Archived', 'task-z')}\n\n"
                                 f"## Notes\n\nnotes then\n\n")
        self.assertIn(("nokey", "", "Old ask"), r.left)

    def test_an_empty_to_do_section(self):
        h = heading()
        t = f"# STATE\n\n{h}\n## Next\n\nthen\n"
        c = f"# STATE\n\n{h}\n\n{agent('A')}\n\n## Next\n\nnow\n"
        r = self.rebuild(t, c, book())
        self.assertEqual(r.text, f"# STATE\n\n{h}\n\n{agent('A')}\n\n## Next\n\nthen\n")

    def test_the_version_had_no_to_do_section(self):
        t = "# STATE\n\n## Now\n\nthen\n"
        c = doc("now", agent("A"))
        r = self.rebuild(t, c, book())
        self.assertEqual(r.text, f"# STATE\n\n## Now\n\nthen\n\n{heading()}\n\n{agent('A')}\n")

    def test_today_has_no_to_do_section(self):
        """the bad night dropped it: that day's, minus the owner items closed since"""
        t = doc("then", owner("Pay rent", "task-p"), owner("Pick a host", "task-b"), agent("A"))
        c = "# STATE\n\n## Now\n\nnow\n"
        r = self.rebuild(t, c, book(open_bound=["task-b"], closed=["task-p"], terms={"task-b": "Pick a host"}))
        self.assertIn(owner("Pick a host", "task-b"), r.text)
        self.assertIn(agent("A"), r.text)
        self.assertNotIn("Pay rent", r.text)
        self.assertEqual(r.readded, [])
        self.assertIn(("closed", "task-p", "Pay rent"), r.left)

    def test_today_has_no_to_do_section_and_every_owner_item_closed(self):
        t = doc("then", owner("Pay rent", "task-p"))
        r = self.rebuild(t, "# STATE\n", book(closed=["task-p"]))
        self.assertNotIn("Pay rent", r.text)
        self.assertIn("- (none)", r.text, "an emptied section gets the empty marker, as the registrar writes it")

    def test_a_key_only_dropped_from_its_line_is_not_added_twice(self):
        """C still asks it, without the key: the registrar relinks it, restore adds nothing"""
        t = doc("then", owner("Renew the domain", "task-a"))
        c = doc("now", owner("Renew  the domain"))
        r = self.rebuild(t, c, book(open_bound=["task-a"], terms={"task-a": "Renew the domain"}))
        self.assertEqual(r.readded, [])
        self.assertEqual(r.text.count("Renew"), 1)

    def test_a_displaced_marker_is_refused(self):
        c = doc("now", agent("A"), after=f"\n## Notes\n\n- moved here{todo_tokens.stamp('en', TAG, 'task-a')}\n")
        r = self.rebuild(doc("then", owner("Renew", "task-a")), c, book(open_bound=["task-a"], terms={"task-a": "Renew"}))
        self.assertIsNone(r.text)
        self.assertIn("task-a", r.refused)
        r = self.rebuild(doc("then", agent("x"), after=f"\n## Old\n\nwas{todo_tokens.stamp('en', TAG, 'task-a')}\n"),
                         doc("now", owner("Renew", "task-a")), book(open_bound=["task-a"], terms={"task-a": "Renew"}))
        self.assertIsNone(r.text, "in the restored version too")

    def test_a_closed_key_in_prose_is_fine(self):
        c = doc("now", agent("A"), after=f"\n## Archive\n\n- done{todo_tokens.stamp('en', TAG, 'task-old')}\n")
        r = self.rebuild(doc("then"), c, book(closed=["task-old"]))
        self.assertIsNone(r.refused)

    def test_an_unclosed_block_is_refused(self):
        for t, c in ((doc("then", "```"), doc("now")), (doc("then"), doc("now", "<!-- open"))):
            r = self.rebuild(t, c, book())
            self.assertIsNone(r.text)
            self.assertTrue(r.refused)

    def test_what_is_listed_and_not_restored(self):
        t = doc("then", owner("Closed one", "task-c"), owner("Own ask", "lease-1-task-0"), owner("Never keyed"),
                owner("Unknown key", "task-u"))
        c = doc("now", agent("A"))
        r = self.rebuild(t, c, book(closed=["task-c"], raised_here=["lease-1-task-0"]))
        self.assertEqual(r.readded, [])
        self.assertEqual(sorted(r.left), sorted([("closed", "task-c", "Closed one"),
                                                 ("project", "lease-1-task-0", "Own ask"),
                                                 ("nokey", "", "Never keyed"), ("unknown", "task-u", "Unknown key")]))

    def test_a_ticked_box_that_day_is_neither_brought_back_nor_listed(self):
        t = doc("then", owner("Renew", "task-a").replace("- [ ]", "- [x]", 1), "- [x] [owner] Paid")
        r = self.rebuild(t, doc("now", agent("A")), book(open_bound=["task-a"], terms={"task-a": "Renew"}))
        self.assertEqual((r.readded, r.left), ([], []))

    def test_a_line_not_yet_stamped_that_day(self):
        """the registrar stamps a line later the night it appears: its text is the open item's terms"""
        r = self.rebuild(doc("then", owner("Renew the domain")), doc("now", agent("A")),
                         book(open_bound=["task-a"], terms={"task-a": "Renew the domain"}))
        self.assertEqual((r.readded, r.left), (["task-a"], []))

    def test_every_open_item_left_out_is_listed(self):
        """raised after that day and deleted since, or on an agent line that day: still open, said"""
        t = doc("then", owner("A", "task-a"), "- [ ] [agent] Renew" + todo_tokens.stamp("en", TAG, "task-g"))
        r = self.rebuild(t, doc("now", owner("A", "task-a")),
                         book(open_bound=["task-a", "task-c", "task-g"], terms={"task-a": "A", "task-c": "C",
                                                                                 "task-g": "Renew"}))
        self.assertEqual(sorted(r.left), [("open", "task-c", "C"), ("open", "task-g", "Renew")])

    def test_zh_hant(self):
        t = doc("then", owner("Renew the domain", "task-a", "zh-Hant"), lang="zh-Hant")
        c = doc("now", agent("Rewrite the notes", "zh-Hant"), lang="zh-Hant")
        r = self.rebuild(t, c, book(open_bound=["task-a"], terms={"task-a": "Renew the domain"}), write=ZH)
        self.assertIn(ZH.principal_line("Renew the domain", "task-a"), r.text)
        self.assertIn(f"{heading('zh-Hant')}\n\n{agent('Rewrite the notes', 'zh-Hant')}\n{ZH.principal_line('Renew the domain', 'task-a')}",
                      r.text)

    def test_mixed_sets(self):
        """an English instance whose restored version has the zh-Hant section: today's section and the writer's set"""
        t = doc("then", owner("Renew the domain", "task-a", "zh-Hant"), lang="zh-Hant")
        c = doc("now", agent("Rewrite"))
        r = self.rebuild(t, c, book(open_bound=["task-a"], terms={"task-a": "Renew the domain"}))
        self.assertIn(f"{heading()}\n\n{agent('Rewrite')}\n{EN.principal_line('Renew the domain', 'task-a')}", r.text)
        self.assertNotIn(heading("zh-Hant"), r.text)

    def test_crlf(self):
        t = doc("then", owner("Renew", "task-a")).replace("\n", "\r\n")
        c = doc("now", agent("A")).replace("\n", "\r\n")
        r = self.rebuild(t, c, book(open_bound=["task-a"], terms={"task-a": "Renew"}))
        self.assertNotIn("\n", r.text.replace("\r\n", ""))


# --- the run ---

P = "garden-app"
KA, KB = "task-garden-app-aaaaaaaaaa", "task-garden-app-bbbbbbbbbb"
DAY1 = doc("The demo runs on the shared box.", owner("Renew the domain", KA), agent("Write the deploy notes"))
DAY2 = doc("Everything was moved to a new box (wrong).", owner("Pick a host", KB), agent("Rewrite the deploy notes"))


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = Env(self.tmp, scenes={}, lang="en")
        self.hub = self.env.hub
        self.bare, self.copy = self.tmp / "bare" / f"{P}-memory.git", self.tmp / "copies" / P
        self.bare.parent.mkdir(exist_ok=True)
        self.git(None, "init", "-q", "--bare", "-b", "agent-memory", str(self.bare))
        self.git(None, "clone", "-q", str(self.bare), str(self.copy))
        self.git(self.copy, "symbolic-ref", "HEAD", "refs/heads/agent-memory")
        (self.hub / "projects" / f"{P}.yaml").write_text(f"v: 1\nname: {P}\npaths:\n  workspace: {self.copy}\n"
                                                         "memory: true\n")
        self.day1 = self.commit("STATE.md", DAY1, "2026-10-01T03:00:00", "librarian: update STATE.md")
        self.note = self.commit("memory/2026-10-01.md", "- moved things\n", "2026-10-01T20:00:00",
                                "brunna: sync memory/STATE.md")
        self.day2 = self.commit("STATE.md", DAY2, "2026-10-02T03:00:00", "librarian: update STATE.md")
        self.git(self.copy, "push", "-q", "-u", "origin", "agent-memory")
        self.ckpt = self.hub / ".state" / f"{P}.sha"
        self.ckpt.write_text(self.day2 + "\n")
        recs = [self.env.rec(P, f"r:{k}", "raised", "requester", "agent-hub:registrar", k, terms={"text": t},
                             terms_rev=1) for k, t in ((KA, "Renew the domain"), (KB, "Pick a host"))]
        r = self.env.hubbus("record-publish", "--json", "-", stdin=json.dumps(recs))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    # --- helpers ---
    def git(self, repo, *args, env=None):
        cmd = ["git", *(["-C", str(repo)] if repo else []), *args]
        r = subprocess.run(cmd, env=env or self.env.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def commit(self, path, text, when, msg, repo=None):
        repo = repo or self.copy
        f = repo / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        env = {**self.env.env, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
        self.git(repo, "add", path, env=env)
        self.git(repo, "commit", "-q", "-m", msg, env=env)
        return self.git(repo, "rev-parse", "HEAD")

    def session_push(self, path, text):
        """a project session pushing to the memory bare repo (not through the platform's copy)"""
        other = self.tmp / f"session-{len(list(self.tmp.glob('session-*')))}"
        self.git(None, "clone", "-q", "-b", "agent-memory", str(self.bare), str(other))
        sha = self.commit(path, text, "2026-10-03T10:00:00", "brunna: sync memory/STATE.md", repo=other)
        self.git(other, "push", "-q", "origin", "agent-memory")
        return sha

    def state_py(self, *args, crash=None):
        env = names.env_set(dict(self.env.env), "LANG_UI", "en")
        if crash:
            names.env_set(env, "RESTORE_CRASH_AFTER", crash)
        return subprocess.run([str(self.hub / "bin" / "py"), str(self.hub / "bin" / "state.py"), *args], env=env,
                              capture_output=True, text=True, timeout=120)

    def tip(self):
        return self.git(self.bare, "rev-parse", "agent-memory")

    def remote(self):
        return self.git(self.bare, "show", "agent-memory:STATE.md") + "\n"

    def snapshot(self):
        """everything a refused or read-only run must leave alone"""
        log = self.hub / "data" / "review-log.md"
        return (self.tip(), self.git(self.copy, "rev-parse", "HEAD"), self.git(self.copy, "status", "--porcelain"),
                self.git(self.copy, "for-each-ref"), (self.copy / "STATE.md").read_text(), self.ckpt.read_text(),
                log.read_text() if log.exists() else None)

    def out(self, kind):
        r = self.env.hubbus("records", "--project", P, "--direction", "out")
        return sorted(e["record"]["id"] for e in json.loads(r.stdout or "[]") if e["record"]["type"] == kind)

    def registrar(self):
        r = self.env.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout + r.stderr

    def from_of(self, preview):
        line = next(l for l in preview.splitlines() if "--from" in l)
        return line.split("--from", 1)[1].split()[0]

    # --- reading ---
    def test_history_reads_the_bare_repo_and_touches_nothing(self):
        later = self.session_push("STATE.md", DAY2.replace("(wrong)", "(still wrong)"))
        before = self.snapshot()
        r = self.state_py("history", P)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [l for l in r.stdout.splitlines() if l.startswith("  ")]
        self.assertIn(later[:7], rows[0])
        self.assertIn(self.day2[:7], rows[1])
        self.assertIn(self.day1[:7], rows[2])
        self.assertEqual(self.snapshot(), before)

    def test_preview_changes_nothing_and_names_the_version_it_read(self):
        before = self.snapshot()
        r = self.state_py("restore", P, "2026-10-01")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("+The demo runs on the shared box.", r.stdout)
        blob = self.git(self.bare, "rev-parse", "agent-memory:STATE.md")
        self.assertEqual(self.from_of(r.stdout), blob)
        self.assertEqual(self.snapshot(), before)

    # --- the whole run ---
    def test_a_deleted_line_and_its_claim(self):
        """the librarian deleted an open item's line; the registrar claimed it done; restore brings it back and says
        the claim was wrong, once"""
        self.registrar()
        self.assertEqual(self.out("claimed"), [f"c:{KA}:r0"])
        r = self.state_py("restore", P, "2026-10-01", "--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = self.remote()
        self.assertIn("The demo runs on the shared box.", text)
        self.assertIn(owner("Pick a host", KB), text)
        self.assertIn(agent("Rewrite the deploy notes"), text)
        self.assertIn(EN.principal_line("Renew the domain", KA), text)
        self.assertEqual((self.copy / "STATE.md").read_text(), text, "the copy is fast-forwarded")
        self.assertEqual(self.git(self.bare, "rev-parse", "agent-memory~1"), self.day2, "a new commit, history kept")
        self.assertEqual(self.out("note"), [f"x:{KA}:r0"])
        self.assertEqual(self.ckpt.read_text().strip(), self.day1, "the librarian reads the notes since that day")
        log = (self.hub / "data" / "review-log.md").read_text()
        self.assertEqual(log.count(self.tip()), 1)
        self.assertFalse((self.hub / ".state" / "restore" / P).exists())
        # a second run sends nothing; the registrar's next night leaves the file alone
        r = self.state_py("restore", P)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.out("note"), [f"x:{KA}:r0"])
        tip = self.tip()
        self.registrar()
        self.assertEqual(self.tip(), tip)
        self.assertEqual(self.out("claimed"), [f"c:{KA}:r0"])

    def test_no_claim_no_note(self):
        r = self.state_py("restore", P, "2026-10-01", "--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.out("note"), [])

    def test_keep_checkpoint(self):
        r = self.state_py("restore", P, self.day1[:7], "--apply", "--keep-checkpoint")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.ckpt.read_text().strip(), self.day2)

    def test_the_checkpoint_never_moves_forward(self):
        self.ckpt.write_text(self.day1 + "\n")
        r = self.state_py("restore", P, self.note[:7], "--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.ckpt.read_text().strip(), self.day1)

    # --- refusals: nothing written ---
    def refused(self, *args, says=None, **kw):
        before = self.snapshot()
        r = self.state_py(*args, **kw)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        if says:
            self.assertIn(says, r.stderr)
        self.assertEqual(self.snapshot(), before)
        return r

    def test_nothing_before_the_first_version(self):
        self.refused("restore", P, "2026-09-01", "--apply")

    def test_a_date_that_does_not_exist(self):
        self.refused("restore", P, "2026-02-31", says="2026-02-31")

    def test_an_unknown_project(self):
        self.refused("history", "nope", says="nope")

    def test_a_stale_preview(self):
        blob = self.from_of(self.state_py("restore", P, "2026-10-01").stdout)
        self.session_push("STATE.md", DAY2.replace("(wrong)", "(edited)"))
        self.git(self.copy, "pull", "-q")
        self.refused("restore", P, "2026-10-01", "--apply", "--from", blob, says="preview again")

    def test_a_note_pushed_since_the_preview_is_fine(self):
        blob = self.from_of(self.state_py("restore", P, "2026-10-01").stdout)
        self.session_push("memory/2026-10-03.md", "- a note\n")
        r = self.state_py("restore", P, "2026-10-01", "--apply", "--from", blob)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("The demo runs", self.remote())

    def test_a_dirty_copy(self):
        (self.copy / "STATE.md").write_text("half written\n")
        self.refused("restore", P, "2026-10-01", "--apply")

    def test_a_diverged_copy(self):
        self.commit("memory/x.md", "local\n", "2026-10-02T05:00:00", "librarian: local only")
        self.session_push("memory/y.md", "remote\n")
        self.refused("restore", P, "2026-10-01", "--apply")

    def test_a_copy_mid_merge(self):
        (self.copy / ".git" / "MERGE_HEAD").write_text(self.day1 + "\n")
        self.refused("restore", P, "2026-10-01", "--apply")

    def test_a_stale_index_lock_or_a_detached_head(self):
        """a killed git in the copy: the fast-forward after publishing would fail, so nothing is published"""
        (self.copy / ".git" / "index.lock").write_text("")
        self.refused("restore", P, "2026-10-01", "--apply")
        (self.copy / ".git" / "index.lock").unlink()
        self.git(self.copy, "checkout", "-q", "--detach")
        self.refused("restore", P, "2026-10-01", "--apply")

    def test_an_untracked_file_that_doesnt_get_in_the_way(self):
        (self.copy / ".DS_Store").write_text("x")
        r = self.state_py("restore", P, "2026-10-01", "--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_crlf_is_kept(self):
        self.session_push("STATE.md", DAY2.replace("\n", "\r\n"))
        self.git(self.copy, "pull", "-q")
        r = self.state_py("restore", P, "2026-10-01", "--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        raw = subprocess.run(["git", "--git-dir", str(self.bare), "show", "agent-memory:STATE.md"],
                             capture_output=True).stdout
        self.assertIn(b"The demo runs on the shared box.\n", raw)   # that day's lines as that day had them

    def test_an_unpublished_record_doesnt_hold_the_librarian(self):
        self.state_py("restore", P, "2026-10-01", "--apply", crash="record")
        self.assertFalse(self.pending())

    def test_the_lock(self):
        with memlock.hold(self.copy, "librarian") as got:
            self.assertTrue(got)
            r = self.refused("restore", P, "2026-10-01", "--apply")
        self.assertIn("librarian", r.stderr)

    def test_a_displaced_marker(self):
        moved = DAY2 + f"\n## Notes\n\n- archived{todo_tokens.stamp('en', TAG, KB)}\n"
        moved = moved.replace(owner("Pick a host", KB) + "\n", "")
        self.session_push("STATE.md", moved)
        self.git(self.copy, "pull", "-q")
        self.refused("restore", P, "2026-10-01", "--apply", says=KB)

    # --- stopped runs ---
    def finished(self):
        """the state an uninterrupted run leaves"""
        self.assertIn("The demo runs on the shared box.", self.remote())
        self.assertEqual((self.copy / "STATE.md").read_text(), self.remote())
        self.assertEqual(self.ckpt.read_text().strip(), self.day1)
        self.assertEqual((self.hub / "data" / "review-log.md").read_text().count(self.tip()), 1)
        self.assertEqual(self.out("note"), [f"x:{KA}:r0"])
        self.assertFalse((self.hub / ".state" / "restore" / P).exists())

    def test_stopped_after_each_step_then_finished(self):
        for step in ("record", "publish", "pull", "checkpoint", "log", "notes"):
            with self.subTest(step=step):
                self.setUp()
                self.registrar()
                r = self.state_py("restore", P, "2026-10-01", "--apply", crash=step)
                self.assertNotEqual(r.returncode, 0, step)
                self.assertTrue((self.hub / ".state" / "restore" / P).exists(), step)
                r = self.state_py("restore", P)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                if step == "record":   # nothing was published: dropped, the owner previews again
                    self.assertEqual(self.tip(), self.day2)
                    self.assertFalse((self.hub / ".state" / "restore" / P).exists())
                    continue
                self.finished()

    def test_the_librarian_waits_for_a_stopped_run(self):
        self.state_py("restore", P, "2026-10-01", "--apply", crash="pull")
        self.assertTrue(self.pending())
        self.state_py("restore", P)
        self.assertFalse(self.pending())

    def pending(self):
        r = subprocess.run([str(self.hub / "bin" / "py"), "-c", f"import state; print(state.pending('{P}'))"],
                           cwd=self.hub / "bin", env=self.env.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip() == "True"

    def test_moved_while_stopped(self):
        """stopped before publishing, and a session pushed meanwhile: dropped, never published over it"""
        self.state_py("restore", P, "2026-10-01", "--apply", crash="record")
        pushed = self.session_push("memory/z.md", "- z\n")
        r = self.state_py("restore", P)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.tip(), pushed)
        self.assertFalse((self.hub / ".state" / "restore" / P).exists())


if __name__ == "__main__":
    unittest.main()
