"""The platform's Python floor is 3.13 (decided by the owner on 10-25): everything goes through bin/py, never relies on
whichever python3 happens to be on PATH, and never hard-codes another interpreter.

    bin/py -m unittest discover -s tests

Deliberate exceptions (EXEMPT): scheduler-watchdog (it must still send alerts when the platform's Python is broken: uses
the system python3, standard library only), migrate-to-mini (runs on the laptop), test_cap_profile (uses the system
python as a probe inside the sandbox), and this file itself.
"""
import pathlib
import shutil
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHEBANG = '#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"'
OTHER_PYTHONS = ("/usr/bin/" + "python3", "/opt/homebrew/bin/" + "python3")
EXEMPT = {"bin/scheduler-watchdog.py", "bin/migrate-to-mini.py", "launchd/local.agent-hub-scheduler-watchdog.plist",
          "tests/test_cap_profile.py", "tests/test_python_floor.py"}
# Temporary exceptions (need a reason, to be removed): switching the lease-runner service to bin/py reloads launchd,
# which cuts off a discussion in progress; render-services does not yet wait for the service to go idle. When no
# discussion is running, change the registry line to `bin/py bin/lease-runner.py`, then delete this entry.
TEMPORARY: set = set()  # no exceptions left since lease-runner moved to bin/py on 10-25
SCANNED = ["bin", "launchd", "tests", "githooks", "agent-jobs", "templates", "projects/agent-hub.yaml", "schedule.yaml"]


def files():
    for top in SCANNED:
        p = ROOT / top
        for f in ([p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file() and "__pycache__" not in x.parts)):
            rel = str(f.relative_to(ROOT))
            if rel in EXEMPT:
                continue
            try:
                yield rel, f.read_text()
            except UnicodeDecodeError:
                continue


class Floor(unittest.TestCase):
    def test_running_on_313_or_newer(self):
        self.assertGreaterEqual(sys.version_info[:2], (3, 13), "tests must run with the platform's Python (bin/py)")

    def test_every_python_script_goes_through_bin_py(self):
        bad = [rel for rel, text in files() if rel.startswith("bin/") and text.startswith("#!")
               and "python" in text.split("\n", 1)[0] and text.split("\n", 1)[0] != SHEBANG]
        self.assertEqual(bad, [], f"the first line must be {SHEBANG}")

    def test_no_other_interpreter_named(self):
        bad = [f"{rel}:{i}" for rel, text in files() for i, line in enumerate(text.splitlines(), 1)
               if any(p in line for p in OTHER_PYTHONS) and not any(rel == f and t in line for f, t in TEMPORARY)]
        self.assertEqual(bad, [], "do not hard-code another interpreter: Python code uses sys.executable, "
                                  "shell uses \"$HUB/bin/py\"")

    def test_platform_never_relies_on_the_first_line(self):
        """A platform Python script's first line only works on macOS (Linux's kernel takes everything after #! as one
        argument): when the platform calls a script itself it always goes through the interpreter: hubconf.launch() /
        sys.executable in Python, "$HUB/bin/py" in shell, hubconf.sh_py() in hook strings."""
        import re
        pys = sorted((f.name for f in (ROOT / "bin").iterdir()
                      if f.is_file() and f.read_text(errors="ignore").startswith("#!/bin/sh -c exec")), key=len, reverse=True)
        names = "|".join(re.escape(n) for n in pys)
        argv0 = re.compile(r"\[\s*str\(\s*(?:BIN|HUB\s*/\s*\"bin\"|(?:hubconf\.)?ROOT\s*/\s*\"bin\")\s*/\s*\"(" + names + r")\"\s*\)")
        sh_cmd = re.compile(r"(?:^|[;&|(]\s*|\bexec\s+|\bthen\s+|\bdo\s+)\"?\$\{?(?:HUB|root|hub|SCRIPT_DIR|DIR|BIN|0%/\*)\}?"
                            r"(?:/bin)?/(" + names + r")\b")
        hook = re.compile(r"&&\s*~/[\w./-]*/bin/(" + names + r")\b")
        bad = []
        for f in sorted([*(ROOT / "bin").iterdir(), *(ROOT / "githooks").rglob("*")]):
            if not f.is_file():
                continue
            text = f.read_text(errors="ignore")
            py = text.startswith("#!/bin/sh -c exec") or f.suffix == ".py"
            for i, line in enumerate(text.splitlines(), 1):
                code = line.split("#", 1)[0] if py or not line.lstrip().startswith("#!") else ""
                for rx in ((argv0, hook) if py else (sh_cmd, hook)):
                    m = rx.search(code)
                    # MCP config: command is already the interpreter
                    if m and not re.search(r"[\"']args[\"']\s*:\s*$", code[:m.start()]):
                        bad.append(f"{f.relative_to(ROOT)}:{i}:{m.group(1)}")
        self.assertEqual(bad, [], "runs a platform Python script directly (relies on the first line, fails on Linux)")

    @unittest.skipUnless(sys.platform == "darwin", "the first line only works on macOS; elsewhere the static check "
                                                   "above ensures the platform does not rely on it")
    def test_shebang_really_runs_on_the_platform_python(self):
        # run the script directly, not through python: goes first line → bin/py → the platform's Python
        out = subprocess.run([str(ROOT / "bin" / "hub-runtime"), "local-ignores"], capture_output=True, text=True,
                             timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        probe = subprocess.run([str(ROOT / "bin" / "py"), "-c", "import sys, yaml; print(sys.version_info[:2] >= (3, 13))"],
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(probe.stdout.strip(), "True", probe.stderr)

    @unittest.skipUnless(shutil.which("/usr/bin/" + "python3"), "no system python3")
    def test_watchdog_exemption_stays_honest(self):
        # the watchdog must still run when the platform's Python is broken: compiles with the system python3,
        # imports only the standard library
        wd = ROOT / "bin" / "scheduler-watchdog.py"
        # (the system 3.9 has no sys.stdlib_module_names: instead every import must be found by the system python3,
        # which has no third-party packages installed)
        r = subprocess.run(["/usr/bin/" + "python3", "-c",
                            "import ast, sys, importlib.util; src = open(sys.argv[1]).read(); compile(src, sys.argv[1], 'exec');"
                            "mods = {a.name.split('.')[0] for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Import) for a in n.names}"
                            " | {n.module.split('.')[0] for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom) and n.module};"
                            "bad = sorted(m for m in mods if m != '__future__' and importlib.util.find_spec(m) is None);"
                            "print(','.join(bad))", str(wd)], capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""), r.stderr)


if __name__ == "__main__":
    unittest.main()
