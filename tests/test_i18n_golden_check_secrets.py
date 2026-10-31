"""E1 golden (docs/design/i18n.md) for bin/check-secrets.py, on zh-Hant: every problem, note and line it prints, and
what it hands the alert channel (problems, the recovery text, the probe failures).

In-process: the registry index, backup.covers, the alert channel and today's date are replaced; the secrets are empty
or dummy files in a temp workspace (no value is ever read or printed).

    bin/py -m unittest tests.test_i18n_golden_check_secrets
"""
import contextlib
import datetime
import importlib.util
import io
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
GOLDEN = ROOT / "tests" / "fixtures" / "i18n-golden"
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
ZH = {"AGENT_HUB_LANG_UI": "zh-Hant"}


def load():
    spec = importlib.util.spec_from_file_location("check_secrets_golden", ROOT / "bin" / "check-secrets.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class FixedDate(datetime.date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 31)


class Golden(unittest.TestCase):
    maxDiff = None

    def scenario(self, tmp: pathlib.Path) -> str:
        m = load()
        ws = tmp / "ws"
        ws.mkdir()

        def put(name: str, text: str = "x", mode: int = 0o600, mtime: datetime.datetime | None = None):
            p = ws / name
            p.write_text(text)
            p.chmod(mode)
            if mtime:
                os.utime(p, (mtime.timestamp(), mtime.timestamp()))
        put("empty.key", "")
        put("app.env", "A=1\nB=\n")
        put("nobackup.key")
        put("old.key")
        put("soon.key")
        put("aged.key", mtime=datetime.datetime(2026, 10, 1, 12, 0))
        put("open.key", mode=0o644)
        put("api-token.txt")
        secrets = [
            {"name": "gone", "path": "gone.key", "rotate": "ROTATE-HOW"},
            {"name": "gone2", "path": "gone2.key"},
            {"name": "empty", "path": "empty.key"},
            {"name": "envkey", "path": "app.env", "env_key": "B"},
            {"name": "nobackup", "path": "nobackup.key"},
            {"name": "old", "path": "old.key", "expires": "2026-10-01", "rotate": "ROTATE-HOW"},
            {"name": "soon", "path": "soon.key", "expires": "2026-11-05"},
            {"name": "aged", "path": "aged.key", "max_age_days": 30},
            {"name": "open", "path": "open.key"},
        ]
        index = {"projects": {"demo": {"name": "demo", "paths": {"workspace": str(ws)}, "secrets": secrets}}, "modules": {}}
        calls = []
        out = []

        def run(argv, probe: bool, configured: list):
            buf = io.StringIO()
            with mock.patch.object(m.registry, "refresh", lambda: index), \
                    mock.patch.object(m.backup, "covers", lambda p: "nobackup" not in p.name), \
                    mock.patch.object(m.datetime, "date", FixedDate), \
                    mock.patch.object(m.alert, "probe", lambda: probe), \
                    mock.patch.object(m.alert, "configured", lambda: configured), \
                    mock.patch.object(m.alert, "record_failure", lambda k, t: calls.append(("record_failure", k, t))), \
                    mock.patch.object(m.alert, "clear_failure", lambda k: calls.append(("clear_failure", k))), \
                    mock.patch.object(m.alert, "once", lambda k, probs, ok: calls.append(("once", k, list(probs), ok))), \
                    contextlib.redirect_stdout(buf):
                rc = m.main(argv)
            return f"exit {rc}\n{buf.getvalue()}"

        with mock.patch.dict(os.environ, ZH):
            out.append(("main demo", run(["demo"], True, ["discord"])))
            index["projects"]["demo"]["secrets"] = []
            for f in ws.glob("*.key"):
                f.unlink()
            for name, probe, conf in (("no channel", False, []), ("discord broken", False, ["discord"]),
                                      ("probe ok", True, ["discord"])):
                calls.clear()
                out.append((f"main all: {name}", run([], probe, conf)))
                out.append((f"alert calls: {name}", "\n".join(repr(c) for c in calls)))
        text = "\n".join(f"=== {k}\n{v}" for k, v in out) + "\n"
        return text.replace(str(tmp), "<TMP>")

    def test_texts(self):
        with tempfile.TemporaryDirectory() as d:
            text = self.scenario(pathlib.Path(d).resolve())
        f = GOLDEN / "check-secrets.txt"
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())


if __name__ == "__main__":
    sys.exit(unittest.main())
