"""The session sources (bin/sessions.py, contracts/session-record.v1): the features read the owner's sessions from
whatever agent made them, found by presence, not from the platform's runtime.

- the contract: its example is valid, a Turn written as a record reads back the same
- the import folder alone gives the features the same numbers as the Claude Code tree they were pinned on
- Claude Code's source: what only it knows (worktree and subagent files, a format it doesn't recognise fails loudly)
- Codex's source: a made-up session in Codex's real shape (what the person typed counts, injected context and a
  program's prompt don't; calls paired with their results; subagent sessions apart)
- nothing anywhere: the features are off and say so, never an empty week

    bin/py -m unittest tests.test_sessions
"""
import datetime
import io
import json
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))
import contracts  # noqa: E402
import sessions  # noqa: E402
import sessions_claude  # noqa: E402
import sessions_codex  # noqa: E402
import test_sessions_pin as pin  # noqa: E402
from runtime_ports import Action  # noqa: E402
from sessions import Turn  # noqa: E402

C = ROOT / "contracts"
SCHEMA = C / "session-record.v1.schema.json"
UTC = datetime.timezone.utc


class Temp(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = pathlib.Path(d.name)
        self.home, self.data = self.tmp / "home", self.tmp / "data"
        self.home.mkdir()
        self.data.mkdir()

    def reading(self):
        return sessions.only(home=self.home, data=self.data)

    def drop(self, name: str, lines: list) -> pathlib.Path:
        d = self.data / sessions.IMPORT
        d.mkdir(exist_ok=True)
        f = d / name
        f.write_text("".join((x if isinstance(x, str) else json.dumps(x)) + "\n" for x in lines))
        return f


class Contract(unittest.TestCase):
    def test_example_is_valid(self):
        lines = (C / "fixtures" / "session-record.v1.example.jsonl").read_text().splitlines()
        self.assertGreater(len(lines), 3)
        for line in lines:
            self.assertEqual(contracts.check_json(SCHEMA, json.loads(line)), [], line)

    def test_rejects_text_and_unknown_kinds(self):
        ok = {"v": 1, "ts": "2026-10-20T10:00:00Z", "session": "s", "role": "human"}
        self.assertEqual(contracts.check_json(SCHEMA, ok), [])
        self.assertTrue(contracts.check_json(SCHEMA, {**ok, "text": "what I typed"}), "no message text travels")
        self.assertTrue(contracts.check_json(SCHEMA, {**ok, "role": "user"}))
        self.assertTrue(contracts.check_json(SCHEMA, {**ok, "actions": [{"kind": "Bash"}]}), "no tool names")
        self.assertTrue(contracts.check_json(SCHEMA, {**ok, "ts": "2026-10-20 10:00"}), "a time zone is required")

    def test_round_trip(self):
        t = Turn(datetime.datetime(2026, 10, 20, 10, tzinfo=UTC), "s", "assistant", "",
                 (Action("run_command", "", None, "c1", "make test", True), Action("other", "charts", "skill")),
                 "e1", "/w/x", True, ("c0",))
        rec = sessions.to_record(t)
        self.assertEqual(contracts.check_json(SCHEMA, rec), [])
        self.assertEqual(sessions.from_record(rec), t)


class ImportFolder(Temp):
    def test_alone_gives_the_pinned_numbers(self):
        """The Claude Code tree, turned into contract records and dropped in the import folder, with no tool's files
        anywhere: the same statistics."""
        src = self.tmp / "claude"
        pin.write_tree(src, pin.claude_tree())
        recs = [sessions.to_record(t) for f in sorted(src.rglob("*.jsonl")) for t in sessions_claude.records(f)]
        self.drop("from-elsewhere.jsonl", recs)
        self.assertEqual(pin.features(self.home, self.data), json.loads(pin.GOLDEN.read_text()))

    def test_invalid_lines_are_said_and_skipped(self):
        good = {"v": 1, "ts": "2026-10-20T10:00:00Z", "session": "s", "role": "human", "folder": "/w/x"}
        self.drop("a.jsonl", [good, "not json", {**good, "role": "person"}, {**good, "ts": "2026-02-30T10:00:00Z"}])
        err = io.StringIO()
        with self.reading(), redirect_stderr(err):
            got = list(sessions.turns({"x": ["/w/x"]}, datetime.datetime(2026, 1, 1, tzinfo=UTC)))
        self.assertEqual([(n, t.role) for n, t in got], [("x", "human")])
        self.assertIn("3", err.getvalue())

    def test_a_file_with_no_record_is_said_and_skipped(self):
        """A drop the reader can't read (here a later version's) never stops the other files' statistics."""
        self.drop("later.jsonl", [{"v": 2, "ts": "2026-10-20T10:00:00Z", "session": "s", "role": "human"}])
        self.drop("ok.jsonl", [{"v": 1, "ts": "2026-10-20T10:00:00Z", "session": "s", "role": "human",
                                "folder": "/w/x"}])
        err = io.StringIO()
        with self.reading(), redirect_stderr(err):
            got = list(sessions.turns({"x": ["/w/x"]}, datetime.datetime(2026, 1, 1, tzinfo=UTC)))
            self.assertIsNotNone(sessions.last_activity("/w/x"))
        self.assertEqual(len(got), 1)
        self.assertIn("later.jsonl", err.getvalue())

    def test_ids_are_per_source(self):
        """The same id from two tools is two entries; the same id twice from one source is one (a copy)."""
        rec = {"v": 1, "ts": "2026-10-20T10:00:00Z", "session": "s", "role": "human", "folder": "/w/alpha", "id": "a1"}
        self.drop("a.jsonl", [rec, rec])
        pin.claude_home(self.home)   # its entry a1 is a different entry
        with self.reading():
            got = [t for _n, t in sessions.turns({"alpha": ["/w/alpha"]}, datetime.datetime(2026, 1, 1, tzinfo=UTC))
                   if t.id == "a1"]
        self.assertEqual(len(got), 2)

    def test_last_activity_from_the_entries(self):
        recs = [{"v": 1, "ts": "2026-10-20T10:00:00Z", "session": "a", "role": "human", "folder": "/w/x"},
                {"v": 1, "ts": "2026-10-21T10:00:00Z", "session": "a", "role": "other"},
                {"v": 1, "ts": "2026-10-25T10:00:00Z", "session": "b", "role": "human", "folder": "/w/y"}]
        self.drop("a.jsonl", recs)
        with self.reading():
            self.assertEqual(sessions.last_activity("/w/x"), datetime.datetime(2026, 10, 21, 10, tzinfo=UTC))
            self.assertIsNone(sessions.last_activity("/w/z"))


class Belonging(Temp):
    """Which project a session belongs to: its first folder; inside a path only with worktrees=True; deepest wins."""

    def setUp(self):
        super().setUp()
        rec = lambda s, folder: {"v": 1, "ts": "2026-10-20T10:00:00Z", "session": s, "role": "human",  # noqa: E731
                                 "folder": folder}
        self.drop("a.jsonl", [rec("top", "/w/app"), rec("wt", "/w/app/.worktrees/try"), rec("inner", "/w/app/lib"),
                              rec("lib-wt", "/w/app/lib/sub"), rec("near", "/w/app-2"),
                              {"v": 1, "ts": "2026-10-20T10:01:00Z", "session": "top", "role": "other",
                               "folder": "/elsewhere"}])

    def owners(self, **kw):
        with self.reading():
            return sorted((n, t.session) for n, t in sessions.turns({"app": ["/w/app"], "lib": ["/w/app/lib"]},
                                                                    datetime.datetime(2026, 1, 1, tzinfo=UTC), **kw))

    def test_exact_folder_only(self):
        self.assertEqual(self.owners(), [("app", "top"), ("app", "top"), ("lib", "inner")])

    def test_inside_with_worktrees_deepest_wins(self):
        self.assertEqual(self.owners(worktrees=True), [("app", "top"), ("app", "top"), ("app", "wt"),
                                                       ("lib", "inner"), ("lib", "lib-wt")])


class ClaudeSource(Temp):
    def test_found_by_presence(self):
        with self.reading():
            self.assertEqual(sessions.present(), [])
        pin.claude_home(self.home)
        with self.reading():
            self.assertEqual(sessions.present(), [("claude-code", 6)])
            self.assertEqual(sessions.require(), ["claude-code"])

    def test_the_remote_machines_own_profile_is_never_read(self):
        """On the remote machine ~/.claude is the work profile: only the hub machine's home (hosts.mini_home), the
        personal profile's backup and ~/.claude-personal are read."""
        from unittest import mock
        laptop, hub = self.tmp / "laptop", self.tmp / "hub"
        for p in (laptop / ".claude" / "projects", laptop / "transcript-backups" / "claude-personal-projects",
                  laptop / ".claude-personal" / "projects", hub / ".claude" / "projects"):
            p.mkdir(parents=True)
        with mock.patch.object(sessions_claude, "_mini_home", lambda: str(hub)):
            got = sessions_claude.roots(laptop, True)
        self.assertEqual(sorted(got), sorted([hub / ".claude" / "projects",
                                              laptop / "transcript-backups" / "claude-personal-projects",
                                              laptop / ".claude-personal" / "projects"]))

    def test_no_text_and_neutral_actions(self):
        pin.claude_home(self.home)
        with self.reading():
            got = [t for _n, t in sessions.turns(pin.PROJECTS, pin.START, subagents=True)]
        self.assertTrue(all(t.text == "" for t in got), "no message text leaves the tool's files")
        run = next(a for t in got for a in t.actions if a.command == "bin/merge-branch --note x")
        self.assertEqual((run.kind, run.call), ("run_command", "c1"))
        self.assertEqual({a.detail for t in got for a in t.actions if a.collab == "skill"}, {"dataviz"})
        self.assertTrue(any(t.subagent for t in got))

    def test_the_same_session_in_two_places_counts_once(self):
        """The remote machine's backup holds the hub machine's sessions too (as ssh-<id> folders): each entry once."""
        pin.claude_home(self.home)
        once = pin.features(self.home, self.data)
        backup = self.home / "transcript-backups" / "claude-personal-projects" / "ssh-1234"
        pin.write_tree(backup, {"A.jsonl": pin.claude_tree()["-w-alpha/A.jsonl"]})
        self.assertEqual(pin.features(self.home, self.data), once)

    def test_last_activity(self):
        pin.claude_home(self.home)
        with self.reading():
            self.assertIsNotNone(sessions.last_activity(pin.ALPHA))
            self.assertIsNotNone(sessions.last_activity(pin.WT))
            self.assertIsNone(sessions.last_activity("/w/none"))

    def test_last_activity_of_a_worktree_a_session_moved_into(self):
        """Started at the project's root, then entered a worktree (its file moves to the worktree's folder): the
        worktree had activity, which worktree cleanup must see."""
        f = self.home / ".claude" / "projects" / "-w-alpha--claude-worktrees-wt2" / "M.jsonl"
        f.parent.mkdir(parents=True)
        wt2 = pin.ALPHA + "/.claude/worktrees/wt2"
        f.write_text("".join(json.dumps(r) + "\n" for r in (pin.human(pin.at(1, 9), "M", "m1", pin.ALPHA),
                                                              pin.human(pin.at(1, 10), "M", "m2", wt2))))
        with self.reading():
            self.assertIsNotNone(sessions.last_activity(wt2))
            self.assertIsNotNone(sessions.last_activity(pin.ALPHA))
            self.assertIsNone(sessions.last_activity(pin.ALPHA + "/.claude/worktrees/none"))

    def test_typed_messages_with_an_image_or_an_attached_block(self):
        f = self.tmp / "x.jsonl"
        base = {"type": "user", "timestamp": "2026-10-20T18:00:00+08:00", "sessionId": "s", "cwd": pin.ALPHA,
                "origin": {"kind": "human"}}
        lines = [{**base, "uuid": "1", "message": {"content": [{"type": "image"}, {"type": "text", "text": "x"}]}},
                 {**base, "uuid": "2", "message": {"content": [{"type": "text", "text": "<note/>"},
                                                               {"type": "text", "text": "go"}]}},
                 {**base, "uuid": "3", "message": {"content": [{"type": "tool_result", "tool_use_id": "c"}]}}]
        f.write_text("".join(json.dumps(x) + "\n" for x in lines))
        got = list(sessions_claude.records(f))
        self.assertEqual([t.role for t in got], ["human", "human", "other"])
        self.assertEqual(got[0].ts, datetime.datetime(2026, 10, 20, 10, tzinfo=UTC), "times are UTC")
        self.assertIs(got[0].ts.tzinfo, UTC)

    def test_a_malformed_entry_is_skipped_not_the_run(self):
        f = self.tmp / "x.jsonl"
        good = pin.human(pin.at(1, 9), "S", "g1", pin.ALPHA)
        bad = [{"type": ["user"]}, {**good, "uuid": "b1", "message": None}, {**good, "uuid": "b2", "timestamp": 5},
               pin.assistant(pin.at(1, 9, 1), "S", "b3", pin.ALPHA, {"type": "tool_use", "name": None, "input": "x"})]
        f.write_text("".join(json.dumps(x) + "\n" for x in [*bad, good]))
        got = list(sessions_claude.records(f))
        self.assertIn("g1", [t.id for t in got])
        self.assertNotIn("b2", [t.id for t in got])

    def test_the_platforms_show_file_tool_reads(self):
        import runtime_claude
        self.assertEqual(sessions_claude.SHOW_FILE_TOOL, runtime_claude.SHOW_FILE_TOOL)
        self.assertEqual(sessions_claude.kind_of(runtime_claude.SHOW_FILE_TOOL), ("read", None))

    def test_worktree_cleanup_survives_a_file_it_cannot_read(self):
        pin.claude_home(self.home)
        d = self.home / ".claude" / "projects" / "-w-elsewhere"
        d.mkdir(parents=True)
        (d / "x.jsonl").write_text("".join(json.dumps({"kind": "message", "at": i, "cwd": "/w/elsewhere"}) + "\n"
                                           for i in range(6)))
        with self.reading(), redirect_stderr(io.StringIO()):
            self.assertIsNotNone(sessions.last_activity(pin.ALPHA))
            self.assertIsNotNone(sessions.last_activity("/w/elsewhere"), "its file's time, as before")

    def test_unrecognised_format_fails_loudly(self):
        d = self.home / ".claude" / "projects" / "-w-alpha"
        d.mkdir(parents=True)
        (d / "x.jsonl").write_text("".join(json.dumps({"kind": "message", "at": i, "cwd": pin.ALPHA}) + "\n"
                                           for i in range(6)))
        with self.reading(), self.assertRaises(sessions.SessionFormatError):
            list(sessions.turns(pin.PROJECTS, pin.START))


# ---------- Codex: a made-up session in the shape Codex writes (generic paths, no account ids) ----------

def cx(ts: str, typ: str, payload: dict) -> dict:
    return {"timestamp": ts, "type": typ, "payload": payload}


def codex_session(sid: str, cwd: str, day: int, *, originator="codex_cli_rs", source="cli", thread_source="user",
                  old_style=False) -> list:
    t = lambda m: pin.at(day, 9, m)   # noqa: E731
    user = (cx(t(1), "event_msg", {"type": "user_message", "message": "run the tests"}) if old_style else
            cx(t(1), "event_msg", {"type": "item_completed", "thread_id": sid, "item": {
                "type": "UserMessage", "id": f"{sid}-u1", "content": [{"type": "text", "text": "run the tests"}]}}))
    return [
        cx(t(0), "session_meta", {"id": sid, "session_id": sid, "timestamp": t(0), "cwd": cwd, "originator": originator,
                                  "cli_version": "0.165.0", "source": source, "thread_source": thread_source,
                                  "model_provider": "openai", "base_instructions": {"text": "You are Codex."}}),
        cx(t(0), "event_msg", {"type": "task_started", "turn_id": f"{sid}-t1"}),
        cx(t(0.5), "response_item", {"type": "message", "id": f"{sid}-m0", "role": "developer",
                                     "content": [{"type": "input_text", "text": "<permissions instructions>"}]}),
        cx(t(0.5), "response_item", {"type": "message", "id": f"{sid}-m1", "role": "user", "content": [
            {"type": "input_text", "text": f"<environment_context>\n  <cwd>{cwd}</cwd>\n</environment_context>"}]}),
        cx(t(0.5), "turn_context", {"turn_id": f"{sid}-t1", "cwd": cwd, "approval_policy": "on-request"}),
        cx(t(1), "response_item", {"type": "message", "id": f"{sid}-m2", "role": "user",
                                   "content": [{"type": "input_text", "text": "run the tests"}]}),
        user,
        cx(t(2), "response_item", {"type": "reasoning", "id": f"{sid}-r1", "summary": []}),
        cx(t(2), "response_item", {"type": "message", "id": f"{sid}-m3", "role": "assistant", "phase": "commentary",
                                   "content": [{"type": "output_text", "text": "Running them."}]}),
        cx(t(3), "response_item", {"type": "custom_tool_call", "id": f"{sid}-ctc1", "status": "completed",
                                   "call_id": f"{sid}-call1", "name": "exec",
                                   "input": 'const r = await tools.exec_command({cmd:"bin/merge-branch --note x"});'}),
        cx(t(3.5), "event_msg", {"type": "item_completed", "item": {
            "type": "CommandExecution", "id": f"{sid}-x1", "command": ["/bin/zsh", "-lc", "bin/merge-branch --note x"]}}),
        cx(t(6), "response_item", {"type": "custom_tool_call_output", "id": f"{sid}-ctco1", "call_id": f"{sid}-call1",
                                   "output": [{"type": "input_text", "text": "ok"}]}),
        cx(t(7), "response_item", {"type": "function_call", "id": f"{sid}-fc1", "call_id": f"{sid}-call2",
                                   "name": "exec_command", "arguments": json.dumps({"cmd": "npm run test:e2e"})}),
        cx(t(9), "response_item", {"type": "function_call_output", "call_id": f"{sid}-call2", "output": "passed"}),
        cx(t(10), "response_item", {"type": "function_call", "id": f"{sid}-fc2", "call_id": f"{sid}-call3",
                                    "name": "spawn_agent", "arguments": json.dumps({"message": "look"})}),
        cx(t(10), "response_item", {"type": "function_call", "id": f"{sid}-fc3", "call_id": f"{sid}-call4",
                                    "name": "apply_patch", "arguments": "*** Begin Patch"}),
        cx(t(11), "token_usage_record", {"session_id": sid, "usage": {"input_tokens": 10}}),
        cx(t(11), "event_msg", {"type": "token_count", "info": None}),
        cx(t(12), "event_msg", {"type": "task_complete", "turn_id": f"{sid}-t1"}),
    ]


def codex_home(home: pathlib.Path) -> pathlib.Path:
    """~/.codex/sessions/YYYY/MM/DD/rollout-…: two of the person's sessions in alpha (one in an older version's
    shape), one in beta, one the platform opened, one a subagent's."""
    files = {"2026/10/20/rollout-2026-10-20T09-00-00-c1.jsonl": codex_session("c1", pin.ALPHA, 1),
             "2026/10/21/rollout-2026-10-21T09-00-00-c2.jsonl": codex_session("c2", pin.ALPHA, 2, old_style=True),
             "2026/10/22/rollout-2026-10-22T09-00-00-c3.jsonl": codex_session("c3", pin.BETA, 3),
             "2026/10/22/rollout-2026-10-22T09-00-00-c4.jsonl": codex_session("c4", pin.ALPHA, 3, originator="brunna"),
             "2026/10/23/rollout-2026-10-23T09-00-00-c5.jsonl": codex_session(
                 "c5", pin.ALPHA, 4, source={"subagent": {"thread_spawn": {"parent_thread_id": "c1"}}},
                 thread_source="subagent")}
    for rel, recs in files.items():
        f = home / ".codex" / "sessions" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("".join(json.dumps(r) + "\n" for r in recs))
    return home


class CodexSource(Temp):
    def test_entries(self):
        codex_home(self.home)
        f = next((self.home / ".codex" / "sessions").rglob("*-c1.jsonl"))
        got = list(sessions_codex.records(f))
        self.assertEqual(sessions_codex.folder(f), pin.ALPHA)
        self.assertEqual([t.role for t in got].count("human"), 1, "injected context and the developer message aren't")
        runs = [a for t in got for a in t.actions if a.kind == "run_command"]
        self.assertEqual([a.call for a in runs], ["c1-call1", "c1-call2"])
        self.assertIn("bin/merge-branch", runs[0].command)
        self.assertEqual(runs[1].command, "npm run test:e2e")
        self.assertEqual([r for t in got for r in t.results], ["c1-call1", "c1-call2"])
        self.assertEqual([a.collab for t in got for a in t.actions if a.collab], ["subagent"])
        self.assertIn("write_file", [a.kind for t in got for a in t.actions])
        self.assertTrue(all(t.ts.tzinfo and t.folder == pin.ALPHA and not t.subagent for t in got))

    def test_programs_and_subagents_are_not_the_person(self):
        codex_home(self.home)
        for sid, sub in (("c4", False), ("c5", True)):
            f = next((self.home / ".codex" / "sessions").rglob(f"*-{sid}.jsonl"))
            got = list(sessions_codex.records(f))
            self.assertNotIn("human", [t.role for t in got], sid)
            self.assertTrue(all(t.subagent is sub for t in got), sid)

    def test_a_message_written_both_ways_counts_once(self):
        """A version that writes the typed message as user_message and item_completed/UserMessage: one message."""
        recs = codex_session("d1", pin.ALPHA, 1)
        recs.insert(6, cx(pin.at(1, 9, 1), "event_msg", {"type": "user_message", "message": "run the tests"}))
        f = self.tmp / "rollout-d1.jsonl"
        f.write_text("".join(json.dumps(r) + "\n" for r in recs))
        self.assertEqual([t.role for t in sessions_codex.records(f)].count("human"), 1)

    def test_a_malformed_entry_is_skipped_not_the_run(self):
        recs = codex_session("d2", pin.ALPHA, 1)
        recs[1:1] = [{"timestamp": pin.at(1, 9), "type": ["x"], "payload": {}},
                     cx("garbage", "response_item", {"type": "message", "role": "assistant", "id": "bad"}),
                     cx(pin.at(1, 9), "response_item", {"type": "local_shell_call", "call_id": "z", "action": "ls"})]
        f = self.tmp / "rollout-d2.jsonl"
        f.write_text("".join(json.dumps(r) + "\n" for r in recs))
        got = list(sessions_codex.records(f))
        self.assertEqual([t.role for t in got].count("human"), 1)
        self.assertNotIn("bad", [t.id for t in got])

    def test_codex_only_machine(self):
        """No Claude Code, no import folder: the features still have real numbers."""
        codex_home(self.home)
        with self.reading():
            self.assertEqual(sessions.require(), ["codex"])
        got = pin.features(self.home, self.data)
        self.assertEqual(got["weekly"]["this"]["prompts"], {"alpha": 2, "beta": 1})
        self.assertEqual(got["weekly"]["this"]["agents"], 4, "spawn_agent in each top-level session (as with Claude "
                                                            "Code, a program's run in the folder counts here too)")
        self.assertEqual(got["memory_gaps"], {"alpha": 2, "beta": 1})
        alpha = got["waiting"]["alpha"]
        self.assertEqual(alpha["by_kind"]["merge"], {"min": 9.0, "runs": 3})
        self.assertEqual(alpha["subagents"]["by_kind"]["merge"]["runs"], 1)
        self.assertEqual(alpha["sessions"], 3, "the person's two and the platform's own run")

    def test_unrecognised_format_fails_loudly(self):
        f = self.home / ".codex" / "sessions" / "2026" / "10" / "20" / "rollout-x.jsonl"
        f.parent.mkdir(parents=True)
        f.write_text("".join(json.dumps({"kind": "event", "n": i}) + "\n" for i in range(6)))
        with self.assertRaises(sessions.SessionFormatError):
            list(sessions_codex.records(f))


class VersionsAndSkips(Temp):
    """Each entry carries the version of the tool that wrote it, and a reader counts per version what it skips, so a
    version that changed the format is seen (docs/design/runtime-versions.md section 6)."""

    def test_claude_versions_and_what_it_skips(self):
        f = self.tmp / "x.jsonl"
        a = {**pin.human(pin.at(1, 9), "S", "g1", pin.ALPHA), "version": "2.1.300"}
        b = {**pin.human(pin.at(1, 10), "S", "g2", pin.ALPHA), "version": "2.1.301"}
        opening = json.dumps({"type": "custom-title", "title": "t"})   # metadata before any version: the first one's
        f.write_text("".join(x + "\n" for x in (opening, json.dumps(a), "not json", json.dumps({"type": "brand-new", "version":
                     "2.1.301"}), json.dumps(b), "", json.dumps({**b, "uuid": "g3", "timestamp": 5}),
                     json.dumps({"type": "api-request", "version": "2.1.301"}))))
        tally = {}
        got = list(sessions_claude.records(f, tally))
        self.assertEqual([(t.id, t.version) for t in got], [("g1", "2.1.300"), ("g2", "2.1.301")])
        self.assertEqual(tally, {"2.1.300": [3, 1], "2.1.301": [4, 2]},
                         "a line that isn't JSON counts under the last version seen, the opening metadata under the "
                         "first; a type it passes over on purpose isn't skipped, a type it doesn't know is; an empty "
                         "line isn't counted")
        self.assertEqual([t.id for t in sessions_claude.records(f)], ["g1", "g2"], "the tally is optional")

    def test_codex_version_from_the_session_meta(self):
        codex_home(self.home)
        f = next((self.home / ".codex" / "sessions").rglob("*-c1.jsonl"))
        tally = {}
        got = list(sessions_codex.records(f, tally))
        self.assertEqual({t.version for t in got}, {"0.165.0"})
        self.assertEqual(list(tally), ["0.165.0"])
        self.assertEqual(tally["0.165.0"][1], 0)

    def test_a_record_keeps_its_version(self):
        t = Turn(datetime.datetime(2026, 10, 1, 9, tzinfo=UTC), "S", "human", version="2.1.300")
        o = sessions.to_record(t)
        self.assertEqual(o["version"], "2.1.300")
        self.assertEqual(sessions.from_record(o), t)
        self.assertNotIn("version", sessions.to_record(t._replace(version="")))

    def test_skipped_per_source_and_version(self):
        d = self.home / ".claude" / "projects" / "-w-alpha"
        d.mkdir(parents=True)
        ok = [{**pin.human(pin.at(1, 9, i), "S", f"g{i}", pin.ALPHA), "version": "2.1.300"} for i in range(19)]
        (d / "a.jsonl").write_text("".join(json.dumps(x) + "\n" for x in ok) + "not json\n")
        new = [{**pin.human(pin.at(1, 10, i), "T", f"h{i}", pin.ALPHA), "version": "2.1.301"} for i in range(5)]
        (d / "b.jsonl").write_text("".join(json.dumps(x) + "\n" for x in new)
                                   + "".join(json.dumps({"type": "brand-new", "version": "2.1.301"}) + "\n"
                                             for _ in range(5)))
        since = datetime.datetime(2000, 1, 1, tzinfo=UTC)
        with self.reading():
            rows = {r["version"]: r for r in sessions.skipped(since)}
        self.assertEqual((rows["2.1.300"]["lines"], rows["2.1.300"]["skipped"], rows["2.1.300"]["over"]), (20, 1, False),
                         "5% exactly is not over")
        self.assertEqual((rows["2.1.301"]["lines"], rows["2.1.301"]["skipped"], rows["2.1.301"]["over"]), (10, 5, True))
        self.assertEqual({r["source"] for r in rows.values()}, {"claude-code"})
        with self.reading():
            self.assertEqual(sessions.skipped(datetime.datetime.now(UTC) + datetime.timedelta(days=1)), [],
                             "only files written since")


class Nothing(Temp):
    def test_no_source_is_off_and_says_where_it_looked(self):
        with self.reading(), self.assertRaises(sessions.NoSessions) as cm:
            sessions.require()
        self.assertIn("Codex", str(cm.exception))

    def test_every_source_is_read_together(self):
        pin.claude_home(self.home)
        codex_home(self.home)
        self.drop("x.jsonl", [{"v": 1, "ts": "2026-10-20T10:00:00Z", "session": "imp", "role": "human",
                               "folder": pin.BETA}])
        with self.reading():
            self.assertEqual(sessions.require(), ["claude-code", "codex", "import"])
        got = pin.features(self.home, self.data)
        self.assertEqual(got["weekly"]["this"]["prompts"], {"alpha": 2 + 2, "beta": 2 + 1 + 1})


if __name__ == "__main__":
    unittest.main()
