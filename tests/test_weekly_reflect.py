"""weekly-reflect's statistics: read from the session sources (here only the import folder); collaboration
styles are counted by Action.collab. Its weeks follow reflect-week v2, and the page reads v1 weeks too.

    bin/py -m unittest discover -s tests
"""
import copy
import datetime
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import contracts  # noqa: E402
import sessions  # noqa: E402
from runtime_ports import Action  # noqa: E402
from sessions import Turn  # noqa: E402

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
        self.assertEqual(str(m.TZ), "America/New_York")
        start, _end, _label = m.week_bounds("2026-W43")
        self.assertEqual(start.utcoffset(), datetime.timedelta(hours=-4))

    def test_no_time_zone_data(self):
        """a bare Ubuntu has no tzdata package: the local zone, said on stderr, and the job goes on"""
        import contextlib
        import hubconf
        import io
        import zoneinfo
        from unittest import mock

        def missing(key):
            raise zoneinfo.ZoneInfoNotFoundError(f"No time zone found with key {key}")
        err = io.StringIO()
        with mock.patch.object(hubconf, "_cfg", {"owner": {"timezone": "America/New_York"}}), \
                mock.patch.object(zoneinfo, "ZoneInfo", missing), contextlib.redirect_stderr(err):
            m = load()
        self.assertEqual(m.TZ, datetime.datetime.now().astimezone().tzinfo)
        self.assertIn("America/New_York", err.getvalue())
        self.assertIn("setup-python", err.getvalue())

class Scan(unittest.TestCase):
    def setUp(self):
        self.m = load()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.drop = pathlib.Path(tmp.name) / "data" / sessions.IMPORT
        self.drop.mkdir(parents=True)
        ctx = sessions.only(home=pathlib.Path(tmp.name) / "home", data=pathlib.Path(tmp.name) / "data")
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        # hublib is a shared module: a swapped function must be swapped back, or it leaks into other tests
        self.addCleanup(setattr, self.m.hublib, "project_paths", self.m.hublib.project_paths)
        self.m.hublib.project_paths = lambda: {"proj": ["/w/proj"], "other": ["/w/other"]}

    def add(self, path, days, role, *actions, sid="s1"):
        t = Turn(START + datetime.timedelta(days=days), sid, role, "", tuple(actions), folder=path)
        with open(self.drop / "t.jsonl", "a") as fh:
            fh.write(json.dumps(sessions.to_record(t)) + "\n")

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
        self.add("/w/proj", 2, "human", sid="a")
        self.add("/w/proj", -3, "human", sid="b")
        self.add("/w/proj", -3, "assistant", Action("other", "", "subagent"))
        weeks = self.m.scan(START, END)
        self.assertEqual(weeks["this"].prompts["proj"], 1)
        self.assertEqual(weeks["prev"].prompts["proj"], 1)
        self.assertEqual((weeks["this"].agents, weeks["prev"].agents), (0, 1))

    def test_only_human_turns_are_prompts_and_projects_kept_apart(self):
        self.add("/w/proj", 1, "human", sid="a")
        self.add("/w/proj", 1, "other")
        self.add("/w/other", 1, "human", sid="c")
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


class Rerun(unittest.TestCase):
    """Re-running a week without --llm rebuilds its statistics but keeps the analysis the model wrote: it used to be
    dropped, and a week's analysis can't be had back without paying for it again (11-03, a verification run did that
    to a real week)."""

    def run_week(self, out, llm=False):
        from unittest import mock
        m = load()
        argv = ["weekly-reflect.py", "--week", "2026-W43"] + (["--llm"] if llm else [])
        with mock.patch.object(m, "OUT", out), mock.patch.object(m, "build_html", return_value=""), \
                mock.patch.object(m.sessions, "require", side_effect=m.sessions.NoSessions("no sessions here")), \
                mock.patch.object(sys, "argv", argv):
            m.main()
        return json.loads((out / "2026-W43.json").read_text())

    def test_the_analysis_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d)
            analysis = {"headline": "kept", "summary": "the model's reading"}
            (out / "2026-W43.json").write_text(json.dumps({"v": 2, "week": "2026-W43", "analysis": analysis}))
            week = self.run_week(out)
            self.assertEqual(week["analysis"], analysis)

    def test_a_week_without_one_gets_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertNotIn("analysis", self.run_week(pathlib.Path(d)))
