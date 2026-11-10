"""doctor's feature table (runtime_ports.features) asks for the ports the code needs, derived here from the judgment points'
declarations (contracts/llm-points.yaml) the way llm.py reads them, not written down twice (docs/design/runtime-codex.md, the
11-08 blind review's second finding).

    bin/py -m unittest tests.test_runtime_ports_features
"""
import pathlib
import sys
import unittest
from unittest import mock

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import runtime_ports  # noqa: E402

POINTS = yaml.safe_load((ROOT / "contracts" / "llm-points.yaml").read_text())["points"]


def librarian_parts() -> dict:
    """librarian.PARTS ({part: (agent point, completion point)}), read from the file, not imported (it is a script)."""
    import ast
    tree = ast.parse((ROOT / "bin" / "librarian.py").read_text())
    [node] = [n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "PARTS" for t in n.targets)]
    return ast.literal_eval(node.value)


# Each row the code serves through judgment points: its position in features(), the points it runs, and whether dispatch
# has to know which sessions run before it starts one (hubbus dispatch loads agent + sessions). A librarian part runs its
# agent point where it can, else its completion point (librarian.completion_in_charge): what the row needs from the runtime is
# what its fallback needs.
ROWS = {
    0: ((*(completion for _agent, completion in librarian_parts().values()), "reflect-week"), False),
    1: (("consolidate",), False),   # no answer-only fallback
    2: (("mailbox-escalation",), True),
    3: (("agent-job-confined", "agent-job-event"), False),
    4: (("mailbox-conversation",), True),
}
LEASING = 5


def needs(points, providers: bool) -> set:
    """What llm.py asks a runtime for to run these points."""
    out = set()
    for name in points:
        spec = POINTS[name]
        if spec.get("shape") == "completion":
            if not providers:   # llm.completion_target: else the runtime's completion interface
                out.add("completion")
            continue
        out.add("agent")
        if spec.get("settings") or spec.get("allow_from_env"):   # llm.py: ports=("agent", "confine")
            out.add("confine")
        if spec.get("settings") == "platform":                   # llm.py: and "rule_check"
            out.add("rule_check")
        if spec.get("tool_servers") == "allowed_only":           # llm.deny_unnamed_servers
            out.add("tool_servers")
    return out


class FeatureNeeds(unittest.TestCase):
    def test_rows_ask_for_what_the_points_need(self):
        for providers in (False, True):
            table = runtime_ports.features(providers=providers, propose=False)
            for i, (points, dispatched) in ROWS.items():
                with self.subTest(table[i][0], providers=providers):
                    want = needs(points, providers) | ({"sessions"} if dispatched else set())
                    self.assertEqual(set(table[i][1]), want)

    def test_no_row_asks_for_a_login_but_leasing(self):
        """llm.py takes auth_env only from a runtime that has the port: without it the tool logs in itself."""
        for i, (feature, need) in enumerate(runtime_ports.features(providers=False, propose=False)):
            if i != LEASING:
                self.assertNotIn("auth", need, feature)
        self.assertIn("auth", runtime_ports.features()[LEASING][1])

    def test_propose_needs_only_an_agent_and_the_session_state(self):
        """mailbox.handler: propose: letters go to mailbox-propose in the throwaway copy, which declares no settings: platform
        and no tool_servers (it may be pinned on a throwaway-only runtime)."""
        spec = POINTS["mailbox-propose"]
        self.assertFalse(spec.get("settings") or spec.get("tool_servers"))
        self.assertEqual(set(runtime_ports.features(providers=False, propose=True)[2][1]), {"agent", "sessions"})

    def test_settings_decide_when_not_given(self):
        import hubconf
        conf = {"llm": {"providers": {"x": {}}}, "mailbox": {"handler": "propose"}}
        with mock.patch.object(hubconf, "get", lambda k, d=None: conf.get(k, d)):
            table = runtime_ports.features()
        self.assertEqual((set(table[0][1]), set(table[2][1])), (set(), {"agent", "sessions"}))
        with mock.patch.object(hubconf, "get", lambda k, d=None: d):
            table = runtime_ports.features()
        self.assertEqual(set(table[0][1]), {"completion"})

    def test_the_rows_are_the_ones_named(self):
        labels = [f for f, _n in runtime_ports.features(providers=False, propose=False)]
        for i, label in ((0, "judgment points (librarian, index, weekly report analysis)"),
                         (1, "memory consolidation (the consolidate module)"),
                         (2, "mailbox tasks (the platform sends an agent for letters nobody picked up)"),
                         (3, "unattended agent jobs with allow:, and event-triggered agent jobs"),
                         (4, "mailbox follow-up (replying with an idle session's context)"),
                         (LEASING, "channel leasing (also needs the assistant module)")):
            self.assertEqual(labels[i], runtime_ports._(label))


class ThrowawayOnlyRuntimes(unittest.TestCase):
    def test_no_feature_is_on_for_a_runtime_that_runs_only_in_the_copy(self):
        for name in ("command", "acp"):
            with self.subTest(name):
                self.assertTrue(runtime_ports.throwaway_only(name))
                self.assertIn("agent", runtime_ports.capabilities(name))
                with mock.patch.object(runtime_ports, "_feature_settings", lambda: (False, False)):   # no provider set
                    self.assertEqual([f for f, ok, _m in runtime_ports.feature_table(name) if ok], [])

    def test_with_a_provider_the_librarian_runs_on_any_runtime(self):
        """With llm.providers set, the librarian falls back to its -edits points where no agent can run (here: none, and a
        throwaway-only runtime), so its row is on; consolidate, with no fallback, stays off."""
        import hubconf
        conf = {"llm": {"providers": {"x": {}}}}
        with mock.patch.object(hubconf, "get", lambda k, d=None: conf.get(k, d)):
            for name in ("none", "command"):
                with self.subTest(name):
                    rows = runtime_ports.feature_table(name)
                    self.assertTrue(rows[0][1], rows[0])
                    self.assertFalse(rows[1][1], rows[1])

    def test_claude_has_every_row(self):
        self.assertTrue(all(ok for _f, ok, _m in runtime_ports.feature_table("claude")))


if __name__ == "__main__":
    unittest.main()
