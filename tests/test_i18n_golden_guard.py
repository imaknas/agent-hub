"""E1 golden (docs/design/i18n.md) for bin/guard.py on zh-Hant: the usage text and every reason decide() gives
(the reasons are also compared word for word in tests/test_guard.py).

    bin/py -m unittest tests.test_i18n_golden_guard
"""
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_lang  # noqa: E402

GOLDEN = golden_lang.GOLDEN
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"


class Golden(unittest.TestCase):
    maxDiff = None

    def scenario(self, tmp: pathlib.Path) -> str:
        out = []
        env = {**os.environ, "AGENT_HUB_LANG_UI": golden_lang.LANG}
        r = subprocess.run([sys.executable, str(ROOT / "bin" / "guard.py")], capture_output=True, text=True, env=env)
        out.append(("usage", f"exit {r.returncode}\n{r.stdout}{r.stderr}"))
        spec = importlib.util.spec_from_file_location("guard_golden", ROOT / "bin" / "guard.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        root = tmp / "proj"
        root.mkdir()
        m.own_project = lambda cwd: ("proj", root)
        with mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": golden_lang.LANG}):
            for label, target in (("other cwd", {"cwd": str(tmp / "other")}),
                                  ("repository", {"elsewhere": {"kind": "repository", "name": "o/r"}}),
                                  ("machine", {"elsewhere": {"kind": "machine"}}),
                                  ("unknown place", {"elsewhere": {"kind": "unknown_place"}}),
                                  ("same project", {"cwd": str(root)})):
                out.append((label, str(m.decide(target, str(root)))))
        return "\n".join(f"=== {k}\n{v}" for k, v in out).replace(str(tmp), "<TMP>") + "\n"

    def test_texts(self):
        with tempfile.TemporaryDirectory() as d:
            text = self.scenario(pathlib.Path(d).resolve())
        f = GOLDEN / "guard.txt"
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())


if __name__ == "__main__":
    sys.exit(unittest.main())
