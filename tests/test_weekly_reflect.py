"""weekly-reflect's statistics: read only through the transcripts interface (with a fake runtime); collaboration
styles are counted by Action.collab. Its weeks follow reflect-week v2, and the page reads v1 weeks too.

    bin/py -m unittest discover -s tests
"""
import copy
import datetime
import importlib.util
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import contracts  # noqa: E402
import runtime_fake  # noqa: E402
from runtime_ports import Action, Turn  # noqa: E402

C = ROOT / "contracts"
WEEKS = json.loads((ROOT / "tests" / "fixtures" / "i18n-golden" / "reflect-weeks.json").read_text())   # v1 weeks

UTC = datetime.timezone.utc
START = datetime.datetime(2026, 10, 19, tzinfo=UTC)   # this week's Monday
END = START + datetime.timedelta(days=7)


def load():
    spec = importlib.util.spec_from_file_location("weekly_reflect_t", ROOT / "bin" / "weekly-reflect.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TimeZone(unittest.TestCase):
    """The hours are counted in the owner's time zone (hub.json owner.timezone)."""

    def test_the_owners_zone(self):
        import hubconf
        from unittest import mock
        with mock.patch.object(hubconf, "_cfg", {"owner": {"timezone": "America/New_York"}}):
            m = load()
        self.assertEqual(str(m.TZ), "Asia/Taipei")

    def test_no_time_zone_data(self):
        """a bare Ubuntu has no tzdata package"""
        import hubconf
        import zoneinfo
        from unittest import mock

        def missing(key):
            raise zoneinfo.ZoneInfoNotFoundError(f"No time zone found with key {key}")
        with mock.patch.object(hubconf, "_cfg", {"owner": {"timezone": "America/New_York"}}), \
                mock.patch.object(zoneinfo, "ZoneInfo", missing):
            with self.assertRaises(zoneinfo.ZoneInfoNotFoundError):
                load()


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
        self.add("/w/proj", 2, "human", text="this week", sid="a")
        self.add("/w/proj", -3, "human", text="last week", sid="b")
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


class Versions(unittest.TestCase):
    """reflect-week v2: the analysis's classifications are fixed English values; a v1 week (zh-Hant words) is read as
    v2 through the contract's x-v1-values, and every v1 value has its v2 value."""

    def setUp(self):
        self.m = load()
        self.v2 = json.loads((C / "reflect-week.v2.schema.json").read_text())

    def enums(self, schema: dict) -> dict:
        a = schema["properties"]
        a = a["analysis"]["properties"] if "analysis" in a and "properties" in a["analysis"] else a
        return {"status": a["projects"]["items"]["properties"]["status"]["enum"],
                "dimension": a["fluency"]["items"]["properties"]["dimension"]["enum"],
                "level": a["fluency"]["items"]["properties"]["level"]["enum"]}

    def test_the_model_is_asked_for_the_v2_values(self):
        point = json.loads((C / "llm" / "reflect-week.schema.json").read_text())
        self.assertEqual(self.enums(point), self.enums(self.v2))

    def test_every_v1_value_has_a_v2_value(self):
        for field, values in self.v2["x-v1-values"].items():
            self.assertEqual(sorted(set(values.values())), sorted(self.enums(self.v2)[field]), field)

    def test_v1_weeks_read_as_v2(self):
        for w in WEEKS:
            with self.subTest(week=w["week"]):
                self.assertEqual(contracts.check_json(C / "reflect-week.v1.schema.json", {"v": 1, **w}), [])
                got = self.m.as_v2(copy.deepcopy(w))
                self.assertEqual(contracts.check_json(C / "reflect-week.v2.schema.json", {**got, "v": 2}), [])
                a = got.get("analysis") or {}
                seen = [p["status"] for p in a.get("projects") or []] + \
                    [x for f in a.get("fluency") or [] for x in (f["dimension"], f["level"])]
                self.assertTrue(all(v.isascii() for v in seen), seen)
        self.assertNotEqual(WEEKS[0]["analysis"]["projects"][0]["status"], "advancing", "the fixture stays v1")
        self.assertNotEqual(contracts.check_json(C / "reflect-week.v2.schema.json", {**WEEKS[0], "v": 2}), [],
                            "v1's values are not v2's")

    def test_a_v2_week_is_left_alone(self):
        w = {"v": 2, "analysis": {"projects": [{"name": "x", "status": "stuck"}],
                                  "fluency": [{"dimension": "diligence", "level": "strength"}]}}
        self.assertEqual(self.m.as_v2(copy.deepcopy(w)), w)


if __name__ == "__main__":
    unittest.main()
