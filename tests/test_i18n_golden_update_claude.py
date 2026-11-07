"""E1 golden (docs/design/i18n.md) for bin/update-claude.py, on zh-Hant: the result line of every branch of update()
(no nvm, npm view fails, already latest, dry-run, the current version already fails, installed, install fails and is
put back or not), the probe's timeout text, and the two alert texts of main().

npm, claude and llm.py are never run: run(), version() and smoke() are canned answers.

    bin/py -m unittest tests.test_i18n_golden_update_claude
"""
import importlib.util
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import golden_lang  # noqa: E402

GOLDEN = golden_lang.GOLDEN
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
ZH = {"AGENT_HUB_LANG_UI": golden_lang.LANG}


def proc(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class Golden(unittest.TestCase):
    maxDiff = None

    def load(self):
        spec = importlib.util.spec_from_file_location("update_claude_golden", ROOT / "bin" / "update-claude.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def case(self, m, *, nvm=True, has_claude=True, runs=(), versions=(), smokes=(), dry=False, flag=None):
        tmp = pathlib.Path(tempfile.mkdtemp())
        bin_dir = tmp / "v22.1.0" / "bin"
        bin_dir.mkdir(parents=True)
        if has_claude:
            (bin_dir / "claude").write_text("")
        with mock.patch.object(m.hubconf, "nvm_bin", return_value=bin_dir if nvm else None), \
                mock.patch.object(m, "run", side_effect=list(runs)), \
                mock.patch.object(m, "version", side_effect=list(versions)), \
                mock.patch.object(m, "smoke", side_effect=list(smokes)), mock.patch.object(m, "restricted_check", return_value=flag):
            return repr(m.update(dry))

    def scenario(self) -> str:
        m = self.load()
        view = proc(0, "2.1.5\n")
        out = []
        with mock.patch.dict(os.environ, ZH):
            out.append(("no nvm", self.case(m, nvm=False)))
            out.append(("npm view fails", self.case(m, runs=[proc(1, "", "npm ERR! 404")], versions=["2.1.4"])))
            out.append(("already latest", self.case(m, runs=[view], versions=["2.1.5"])))
            out.append(("dry-run", self.case(m, runs=[view], versions=["2.1.4"], dry=True)))
            out.append(("dry-run, not installed", self.case(m, has_claude=False, runs=[view], dry=True)))
            out.append(("current version fails", self.case(m, runs=[view], versions=["2.1.4"], smokes=["Login expired"])))
            out.append(("installed", self.case(m, runs=[view, proc()], versions=["2.1.4", "2.1.5"], smokes=[None, None])))
            out.append(("installed, none before", self.case(m, has_claude=False, runs=[view, proc()], versions=["2.1.5"],
                                                            smokes=[None])))
            out.append(("install fails, put back", self.case(m, runs=[view, proc(1, "", "npm ERR! boom"), proc()],
                                                             versions=["2.1.4", "2.1.4"], smokes=[None])))
            out.append(("new fails probe, put back fails",
                        self.case(m, runs=[view, proc(), proc(1)], versions=["2.1.4", "2.1.5", None],
                                  smokes=[None, "unknown flag -p"])))
            out.append(("a completion fails on the new one, put back",
                        self.case(m, runs=[view, proc(), proc()], versions=["2.1.4", "2.1.5", "2.1.4"], smokes=[None, None], flag="unknown option")))
            out.append(("a completion fails on the new one, none before",
                        self.case(m, has_claude=False, runs=[view, proc()], versions=["2.1.5"], smokes=[None], flag="unknown option")))
            out.append(("install fails, none before", self.case(m, has_claude=False, runs=[view, proc(1, "", "EACCES")],
                                                                smokes=[])))
            with mock.patch.object(m.subprocess, "run", side_effect=subprocess.TimeoutExpired(["llm.py"], 180)):
                out.append(("probe timeout", repr(m.smoke())))
            for ok in (False, True):
                sent = []
                o, e = io.StringIO(), io.StringIO()
                with mock.patch.object(m, "update", return_value=(ok, "MSG")), mock.patch.object(m, "probe_new_version"), \
                        mock.patch.object(m.alert, "once", side_effect=lambda *a: sent.append(a)), \
                        mock.patch.object(sys, "argv", ["update-claude.py"]), redirect_stdout(o), redirect_stderr(e):
                    code = m.main()
                out.append((f"main ok={ok}", f"exit {code}\nstdout: {o.getvalue()!r}\nstderr: {e.getvalue()!r}\n"
                                             f"alert: {sent!r}"))
        return "\n".join(f"=== {k}\n{v}" for k, v in out) + "\n"

    def test_texts(self):
        text = self.scenario()
        f = GOLDEN / "update-claude.txt"
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())


if __name__ == "__main__":
    sys.exit(unittest.main())
