"""weekly-reflect's statistics: read only through the transcripts interface (with a fake runtime); collaboration
styles are counted by Action.collab.

    bin/py -m unittest discover -s tests
"""
import datetime
import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import runtime_fake  # noqa: E402
from runtime_ports import Action, Turn  # noqa: E402

UTC = datetime.timezone.utc
START = datetime.datetime(2026, 10, 19, tzinfo=UTC)   # this week's Monday
END = START + datetime.timedelta(days=7)


def load():
    spec = importlib.util.spec_from_file_location("weekly_reflect_t", ROOT / "bin" / "weekly-reflect.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Scan(unittest.TestCase):
    def setUp(self):
        runtime_fake.reset()
        self.addCleanup(runtime_fake.reset)
        self.m = load()
        # runtime_ports and hublib are shared modules: swapped functions must be swapped back, or they leak into
        # other tests
        for mod, name, fake in ((self.m.runtime_ports, "load", lambda *a, **k: runtime_fake),
                                (self.m.hublib, "project_paths", lambda: {"proj": ["/w/proj"], "other": ["/w/other"]})):
            self.addCleanup(setattr, mod, name, getattr(mod, name))
            setattr(mod, name, fake)

    def add(self, path, days, role, *actions, text="", sid="s1"):
        runtime_fake.add_turn(path, Turn(START + datetime.timedelta(days=days), sid, role, text, tuple(actions)))

    def test_collaboration_counted_by_collab_not_kind(self):
        self.add("/w/proj", 1, "assistant",
                 Action("other", "", "subagent"), Action("other", "", "subagent"),
                 Action("other", "", "workflow"), Action("other", "", "peer_message"),
                 Action("other", "dataviz", "skill"), Action("other", "dataviz", "skill"), Action("other", "pdf", "skill"),
                 Action("run_command"), Action("write_file"), Action("read"))
        w = self.m.scan(START, END)["this"]
        self.assertEqual((w.agents, w.workflows, w.peer_msgs), (2, 1, 1))
        self.assertEqual(dict(w.skills), {"dataviz": 2, "pdf": 1})

    def test_this_week_and_previous_week_split(self):
        self.add("/w/proj", 2, "human", text="這週", sid="a")
        self.add("/w/proj", -3, "human", text="上週", sid="b")
        self.add("/w/proj", -3, "assistant", Action("other", "", "subagent"))
        weeks = self.m.scan(START, END)
        self.assertEqual(weeks["this"].prompts["proj"], 1)
        self.assertEqual(weeks["prev"].prompts["proj"], 1)
        self.assertEqual((weeks["this"].agents, weeks["prev"].agents), (0, 1))

    def test_only_human_turns_are_prompts_and_projects_kept_apart(self):
        self.add("/w/proj", 1, "human", text="hi", sid="a")
        self.add("/w/proj", 1, "other")
        self.add("/w/other", 1, "human", text="hey", sid="c")
        w = self.m.scan(START, END)["this"]
        self.assertEqual(dict(w.prompts), {"proj": 1, "other": 1})
        self.assertEqual({k: len(v) for k, v in w.sessions.items()}, {"proj": 1, "other": 1})


if __name__ == "__main__":
    unittest.main()
