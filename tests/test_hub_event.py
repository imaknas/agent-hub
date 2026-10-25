"""bin/hub-event:平台的事件入口(執行環境抽象第 3 層)。用假的 runtime + 替身處理程式,不碰真的專案、匯流排。

    bin/py -m unittest discover -s tests
"""
import importlib.machinery
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import runtime_fake  # noqa: E402


def load_hub_event():
    loader = importlib.machinery.SourceFileLoader("hub_event", str(ROOT / "bin" / "hub-event"))
    spec = importlib.util.spec_from_loader("hub_event", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


class HubEvent(unittest.TestCase):
    def setUp(self):
        self.m = load_hub_event()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.m.LOG = self.tmp / "hub-event.log"
        self.m._workspace = lambda cwd: self.tmp
        self.calls = []

    def raw(self, **extra):
        return json.dumps({"sid": "S1", "dir": str(self.tmp), **extra})

    def stub(self, name, out=None, boom=False):
        def fn(ev, ws):
            self.calls.append((name, ev.event))
            if boom:
                raise RuntimeError("boom")
            return out
        return fn

    def test_texts_from_several_handlers_are_combined(self):
        self.m.HANDLERS["tool_used"] = [("nudge", self.stub("nudge", "記得寫筆記")), ("mail", self.stub("mail", "有信"))]
        out = json.loads(self.m.handle("tool_used", self.raw(cmd="git commit"), runtime_fake))
        self.assertEqual(out["say"], "記得寫筆記\n\n有信")

    def test_one_failure_does_not_stop_the_rest_and_is_logged(self):
        self.m.HANDLERS["session_start"] = [("memory_pull", self.stub("memory_pull", boom=True)),
                                            ("mail", self.stub("mail", "有信"))]
        out = json.loads(self.m.handle("session_start", self.raw(), runtime_fake))
        self.assertEqual(out["say"], "有信")
        log = self.m.LOG.read_text()
        self.assertIn("session_start", log)
        self.assertIn("failed=memory_pull:RuntimeError", log)

    def test_deny_wins(self):
        self.m.HANDLERS["open_session_attempt"] = [("guard", self.stub("guard", "那是別的專案"))]
        out = json.loads(self.m.handle("open_session_attempt", self.raw(where="/x"), runtime_fake))
        self.assertEqual(out["block"], "那是別的專案")

    def test_not_a_session_does_nothing(self):
        self.m.HANDLERS["prompt"] = [("mail", self.stub("mail", "有信"))]
        self.assertEqual(self.m.handle("prompt", json.dumps({"dir": "/x"}), runtime_fake), "")
        self.assertEqual(self.calls, [])

    def test_unknown_event_does_nothing(self):
        self.assertEqual(self.m.handle("nope", self.raw(), runtime_fake), "")

    def test_no_output_when_handlers_have_nothing(self):
        self.m.HANDLERS["prompt"] = [("mail", self.stub("mail", None))]
        self.assertEqual(self.m.handle("prompt", self.raw(), runtime_fake), "")

    def test_main_always_exits_zero(self):
        self.m.handle = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        self.assertEqual(self.m.main(["prompt"]), 0)

    def test_every_platform_event_has_handlers(self):
        import runtime_ports
        self.assertEqual(set(self.m.HANDLERS), set(runtime_ports.EVENTS))


if __name__ == "__main__":
    unittest.main()
