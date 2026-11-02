"""The rename to brunna (docs/design/rename-brunna.md): the name table's rules, and a ratchet on spellings written
outside it.

The ratchet counts, per file under bin/, the literals of each class that code should take from bin/names.py instead
(environment variables in either old spelling, unit labels, rules-file and hook markers). The counts are recorded in
tests/fixtures/name-pairs-baseline.json and may only go down: moving a read onto names.env() lowers its file's count;
`bin/py tests/test_name_pairs.py --update` lowers the baseline (it never raises it). A new literal fails the test.

    bin/py -m unittest tests.test_name_pairs
"""
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import names  # noqa: E402

BASELINE = ROOT / "tests" / "fixtures" / "name-pairs-baseline.json"
CLASSES = {
    "env": re.compile(r"\b(?:AGENT_HUB|AGENTHUB|BRUNNA)_[A-Z][A-Z_]*"),
    "unit": re.compile(r"local\.(?:agent-hub|brunna)\b"),
    "marker": re.compile(r"(?:agent-hub|brunna):(?:begin|end|gate)\b"),
}
# the table itself; the inline copies are compared with it below instead
EXEMPT = {"bin/names.py"}


def files() -> list[str]:
    """bin/'s tracked files; in an exported tree (the gate's: no git) every file under bin/."""
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--", "bin"], capture_output=True)
    if out.returncode == 0:
        paths = [f for f in out.stdout.decode().split("\0") if f]
    else:
        paths = [str(p.relative_to(ROOT)) for p in (ROOT / "bin").rglob("*") if "__pycache__" not in p.parts]
    return sorted(f for f in paths if f not in EXEMPT and (ROOT / f).is_file())


def counts() -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {cls: {} for cls in CLASSES}
    for f in files():
        try:
            text = (ROOT / f).read_text()
        except UnicodeDecodeError:
            continue
        # a shell read in both spellings, ${AGENT_HUB_X:-${BRUNNA_X:-…}}, is the sanctioned form where bin/names.py
        # can't be imported: its new spelling isn't counted again
        text = re.sub(r"(:?-)\$\{BRUNNA_", r"\1${", text)
        # an inline copy of the table (NAMES = … / NAMES_<class>=…) is compared with it by InlineCopies instead
        text = re.sub(r"(?m)^\s*NAMES(?:_\w+)?\s*=.*$", "", text)
        for cls, rx in CLASSES.items():
            if (n := len(rx.findall(text))):
                out[cls][f] = n
    return out


class Table(unittest.TestCase):
    def test_spellings(self):
        self.assertFalse(names.SWITCHED, "v0.6 reads both and writes the old spellings")
        for cls in names.PAIRS:
            with self.subTest(cls=cls):
                self.assertEqual(set(names.all(cls)), set(names.PAIRS[cls]))
                self.assertEqual(names.all(cls)[0], names.current(cls))

    def test_resolve(self):
        base = pathlib.Path(tempfile.mkdtemp())
        self.assertEqual(names.resolve(base, "repo_dir").name, "agent-hub", "neither exists: the current one (old, before the switch)")
        (base / "agent-hub").mkdir()
        self.assertEqual(names.resolve(base, "repo_dir").name, "agent-hub", "an existing one is used as it is")
        (base / "brunna").mkdir()
        self.assertEqual(len(names.resolve_both(base, "repo_dir")), 2)

    def test_env(self):
        self.assertEqual(names.env_names("ROOT"), ("AGENT_HUB_ROOT", "BRUNNA_ROOT"))
        self.assertEqual(names.env_names("PROJECT"), ("AGENTHUB_PROJECT", "BRUNNA_PROJECT"))
        self.assertEqual(names.env("ROOT", environ={"BRUNNA_ROOT": "n"}), "n")
        self.assertEqual(names.env("ROOT", environ={"BRUNNA_ROOT": "n", "AGENT_HUB_ROOT": "o"}), "o",
                         "an old spelling is an explicit override")
        e = {"BRUNNA_STATE": "stale"}
        names.env_set(e, "STATE", "x")
        self.assertEqual(e, {"AGENT_HUB_STATE": "x"}, "v0.6 writes the old spelling and removes the other")
        e = {}
        names.env_set(e, "JOB_CHAIN", "c")
        self.assertEqual(e, {"AGENT_HUB_JOB_CHAIN": "c", "BRUNNA_JOB_CHAIN": "c"}, "documented ones in both")
        names.env_unset(e, "JOB_CHAIN")
        self.assertEqual(e, {})
        self.assertEqual(names.env_strip({"AGENT_HUB_X": 1, "AGENTHUB_Y": 2, "BRUNNA_Z": 3, "HOME": 4}), {"HOME": 4})


class Markers(unittest.TestCase):
    def test_put_block_takes_either_spelling(self):
        cur_b, cur_e = names.block()
        old_b, old_e = names.block(names.other("marker"))
        self.assertEqual(names.put_block("", "x"), f"{cur_b}\nx\n{cur_e}\n")
        mine = "my line\n\n"
        self.assertEqual(names.put_block(mine + f"{old_b}\nold\n{old_e}\n", "x"), mine + f"{cur_b}\nx\n{cur_e}\n",
                         "a block in the other spelling is rewritten in the current one")
        both = f"{cur_b}\na\n{cur_e}\n{old_b}\nb\n{old_e}\n"
        self.assertEqual(names.put_block(both, "x").count(":begin -->"), 1, "never two blocks")

    def test_uninstall_strips_either(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("uninstall_n", ROOT / "bin" / "uninstall.py")
        un = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(un)
        f = pathlib.Path(tempfile.mkdtemp()) / "CLAUDE.local.md"
        old_b, old_e = names.block(names.other("marker"))
        f.write_text(f"mine\n{old_b}\nx\n{old_e}\n")
        self.assertTrue(un.strip_block(f, True))
        self.assertEqual(f.read_text(), "mine\n")

    def test_gate_hooks_in_either_spelling_are_the_platforms(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("rps_n", ROOT / "bin" / "render-project-settings.py")
        rps = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rps)
        self.assertEqual(set(rps.MARKS), {names.gate_mark(s) for s in names.PAIRS["marker"]})


class HubNames(unittest.TestCase):
    def test_party_ids_in_either_spelling_are_the_platforms(self):
        import hubconf
        self.assertEqual(hubconf.party_ids("registrar"), {"agent-hub:registrar", "brunna:registrar"})
        self.assertEqual(hubconf.party("registrar"), f"{names.current('party')}:registrar")
        self.assertEqual(hubconf.party("requester", names.other("project")), f"project:{names.current('party')}")

    def test_letters_to_either_name_reach_the_platform(self):
        import importlib.machinery
        import importlib.util
        from unittest import mock
        loader = importlib.machinery.SourceFileLoader("hubbus_n", str(ROOT / "bin" / "hubbus"))
        spec = importlib.util.spec_from_loader("hubbus_n", loader)
        hb = importlib.util.module_from_spec(spec)
        loader.exec_module(hb)
        hub = names.current("project")
        with mock.patch.object(hb, "projects", lambda: {hub: {}, "app": {}}):
            for addr in names.all("project"):
                self.assertTrue(hb.valid_address(addr), addr)
                self.assertEqual(hb.now_named(addr), hub)
            self.assertFalse(hb.valid_address("nobody"))
            self.assertIn(names.other("project"), hb.own_names(hub))

    def test_both_names_are_reserved(self):
        import registry
        doc = {"v": 2, "name": names.other("project"), "paths": {"workspace": "/tmp/x"}}
        errs = registry.validate_v2(doc, names.other("project"))
        self.assertTrue(any(names.other("project") in e for e in errs), errs)

    def test_contracts_from_either_name_are_the_platforms(self):
        """A consumes: from: names the platform in either spelling; its entry is found by type, whatever its stem."""
        import importlib.util
        import shutil
        spec = importlib.util.spec_from_file_location("contracts_check_n", ROOT / "bin" / "contracts-check.py")
        cc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cc)
        schema = "contracts/hubbus.v1.schema.json"
        ws = pathlib.Path(tempfile.mkdtemp()).resolve()
        (ws / "contracts").mkdir()
        shutil.copy(ROOT / schema, ws / schema)
        platform = {"name": "whatever-stem", "type": "platform",
                    "provides": [{"contract": "hubbus", "v": 1, "schema": schema}]}
        for producer in names.all("project"):
            with self.subTest(producer=producer):
                app = {"name": "app", "paths": {"workspace": str(ws)},
                       "consumes": [{"contract": "hubbus", "v": 1, "copy": schema, "from": producer}]}
                self.assertEqual(cc.cross_copies({"projects": {"whatever-stem": platform, "app": app}}), [])

    def test_unit_labels_in_either_spelling(self):
        old, new = names.all("unit")
        self.assertEqual(names.unit_spellings(f"{old}-scheduler"), [f"{old}-scheduler", f"{new}-scheduler"])
        self.assertEqual(names.unit_spellings(f"{new}.lease-runner"), [f"{new}.lease-runner", f"{old}.lease-runner"])
        self.assertEqual(names.unit_spellings("local.web"), ["local.web"])
        self.assertEqual(names.unit_spellings(f"{old}x"), [f"{old}x"])

    def test_recover_service_takes_either_spelling(self):
        """services.tsv is written by a person: its label, its location's agent-hub/ or brunna/, and a template filed
        under the other spelling all resolve; the unit carries the label services.tsv names."""
        import os
        old, new = names.all("unit")
        p_old, p_new = names.all("product")
        tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        hub, home, stub = tmp / "hub", tmp / "home", tmp / "stub"
        for d in (hub / "bin", hub / "launchd", home / "Library" / "LaunchAgents", stub):
            d.mkdir(parents=True)
        for f in ("recover-service.sh", "hubpaths.sh", "i18n.sh", "py"):
            if (ROOT / "bin" / f).exists():
                (hub / "bin" / f).symlink_to(ROOT / "bin" / f)
        (hub / "launchd" / f"{old}-scheduler.plist").write_text(
            f"<plist><string>{old}-scheduler</string><string>{{{{hub}}}}/bin</string></plist>\n")
        (stub / "launchctl").write_text("#!/bin/sh\nexit 0\n")
        (stub / "launchctl").chmod(0o755)
        rows = [(f"{old}-scheduler", "-", p_old, f"{p_old}/launchd/{old}-scheduler.plist", "ticks", ""),
                (f"{new}-scheduler-x", "-", p_new, f"{p_new}/launchd/{new}-scheduler.plist", "ticks too", "")]
        (hub / "services.tsv").write_text("".join("\t".join(r) + "\n" for r in rows))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_HUB", "AGENTHUB", "BRUNNA"))}
        env.update(HOME=str(home), PATH=f"{stub}:/usr/bin:/bin", AGENT_HUB_ROOT=str(hub), AGENT_HUB_CONFIG=str(hub),
                   AGENT_HUB_LANG_UI="en")
        agents = home / "Library" / "LaunchAgents"
        for asked, written in ((f"{new}-scheduler", f"{old}-scheduler"), (f"{old}-scheduler-x", f"{new}-scheduler-x")):
            with self.subTest(asked=asked):
                r = subprocess.run(["bash", str(hub / "bin" / "recover-service.sh"), asked], env=env,
                                   capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                plist = (agents / f"{written}.plist").read_text()
                self.assertIn(f"<string>{written}</string>", plist)
                self.assertIn(f"{hub}/bin", plist)

    def test_the_test_runner_finds_the_platform_entry_by_type(self):
        """The commit that renames the platform's entry is tested by a runner that still calls it by the old name."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("test_runner_n", ROOT / "bin" / "test-runner.py")
        tr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tr)
        tree = pathlib.Path(tempfile.mkdtemp())
        (tree / "projects").mkdir()
        (tree / "bin").symlink_to(ROOT / "bin")   # what the entry's run: lines name
        entry = (ROOT / "projects" / "agent-hub.yaml").read_text().replace(
            "name: agent-hub\n", f"name: {names.new('project')}\n")
        if "\ntype: platform\n" not in entry:
            entry = entry.replace(f"name: {names.new('project')}\n", f"name: {names.new('project')}\ntype: platform\n")
        (tree / "projects" / f"{names.new('project')}.yaml").write_text(entry)
        doc = tr.declaration(names.old("project"), {}, tree, tree / "run")
        self.assertIn("tests", doc)


class SchedulerState(unittest.TestCase):
    def test_daily_state_moves_to_the_current_name_newest_wins(self):
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location("scheduler_n", ROOT / "bin" / "scheduler.py")
        sch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sch)
        hub, other = names.current("project"), names.other("project")
        state = {f"{other}/librarian": "2026-11-02", f"{other}/librarian.attempts": {"date": "2026-11-02", "count": 1},
                 f"{hub}/librarian": "2026-11-01"}
        failing = pathlib.Path(tempfile.mkdtemp()) / "failing.json"
        failing.write_text(json.dumps({f"{other}/librarian": {"since": "x"}}))
        with mock.patch.object(sch, "FAILING_FILE", failing):
            sch.adopt_state({"name": f"{hub}/librarian", "project": hub}, state)
        self.assertEqual(state, {f"{hub}/librarian": "2026-11-02",
                                 f"{hub}/librarian.attempts": {"date": "2026-11-02", "count": 1}},
                         "ran today under the other name: it doesn't run again")
        self.assertEqual(json.loads(failing.read_text()), {f"{hub}/librarian": {"since": "x"}})


class Wrapper(unittest.TestCase):
    def test_an_older_wrapper_is_still_found(self):
        """install-cli's wrapper from before its mark was in English: found by its exec line, under either name."""
        import os
        from unittest import mock
        sys.path.insert(0, str(ROOT / "bin"))
        import upgrade
        d = pathlib.Path(tempfile.mkdtemp())
        for name in names.all("cli"):
            with self.subTest(name=name):
                for f in d.iterdir():
                    f.unlink()
                w = d / name
                w.write_text('#!/bin/sh\n# an older comment\nexec "/h/bin/py" "/h/bin/agenthub" "$@"\n')
                w.chmod(0o755)
                with mock.patch.dict(os.environ, {"PATH": str(d)}):
                    self.assertEqual(upgrade.cli_wrapper(), w)
        (d / names.all("cli")[0]).write_text("#!/bin/sh\nexec something-else\n")
        (d / names.all("cli")[1]).unlink(missing_ok=True)
        with mock.patch.dict(os.environ, {"PATH": str(d)}):
            self.assertIsNone(upgrade.cli_wrapper(), "someone else's program of that name")


class ConfigPath(unittest.TestCase):
    def run_py(self, cfg: pathlib.Path, code: str) -> str:
        env = names.env_strip(__import__("os").environ)
        names.env_set(env, "CONFIG", str(cfg))
        names.env_set(env, "STATE", str(cfg / ".state"))
        r = subprocess.run([sys.executable, "-c", code], env={**env, "PYTHONPATH": str(ROOT / "bin")},
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_whichever_exists(self):
        cfg = pathlib.Path(tempfile.mkdtemp())
        read = "import hubconf; print(hubconf.config_path().name, hubconf.get('owner.tag'), hubconf.config_conflict())"
        self.assertEqual(self.run_py(cfg, read), "hub.json None None", "neither: this release's own name")
        (cfg / "brunna.json").write_text('{"owner": {"tag": "B"}}')
        self.assertEqual(self.run_py(cfg, read), "brunna.json B None", "a renamed instance is read as it is")
        (cfg / "hub.json").write_text('{"owner": {"tag": "H"}}')
        out = self.run_py(cfg, read).split(" ", 2)
        self.assertEqual(out[:2], ["hub.json", "H"])
        self.assertIn("brunna.json", out[2], "both: reported, not guessed")
        r = subprocess.run([sys.executable, str(ROOT / "bin" / "config-check")],
                           env={**names.env_set(names.env_strip(__import__("os").environ), "CONFIG", str(cfg)),
                                "AGENT_HUB_LANG_UI": "en"}, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("both hub.json and brunna.json exist", r.stdout + r.stderr)

    def test_shell_finds_whichever_exists(self):
        cfg = pathlib.Path(tempfile.mkdtemp())
        env = {**names.env_strip(__import__("os").environ), "BRUNNA_CONFIG": str(cfg)}
        sh = f'source "{ROOT}/bin/hubpaths.sh"; printf "%s|" "$HUB_JSON"; hub_json_get owner.tag'
        def run():
            return subprocess.run(["bash", "-c", sh], env=env, capture_output=True, text=True).stdout.strip()
        self.assertEqual(run(), f"{cfg}/hub.json|", "neither: this release's name; a new-spelling variable is read")
        (cfg / "brunna.json").write_text('{"owner": {"tag": "B"}}')
        self.assertEqual(run(), f"{cfg}/brunna.json|B")


class InlineCopies(unittest.TestCase):
    """The places that can't import bin/names.py carry copies of the pairs they use, current spelling first."""

    def shell_copy(self, rel: str, cls: str) -> list[str]:
        m = re.search(rf'^NAMES_{cls}="([^"]*)"$', (ROOT / rel).read_text(), re.M)
        self.assertIsNotNone(m, f"{rel} has no NAMES_{cls}")
        return m.group(1).split()

    def test_shell_copies(self):
        for rel, cls in (("bin/hubpaths.sh", "config_file"), ("bin/hubpaths.sh", "product"), ("bin/hubpaths.sh", "unit"),
                         ("bin/py", "share_dir"), ("bin/setup-python", "share_dir")):
            with self.subTest(rel=rel):
                self.assertEqual(self.shell_copy(rel, cls), list(names.all(cls)))

    def test_watchdog_copy(self):
        import ast
        tree = ast.parse((ROOT / "bin" / "scheduler-watchdog.py").read_text())
        node = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "NAMES")
        for cls, pair in ast.literal_eval(node.value).items():
            with self.subTest(cls=cls):
                self.assertEqual(tuple(pair), names.all(cls))


class Ratchet(unittest.TestCase):
    def test_literals_only_go_down(self):
        base = json.loads(BASELINE.read_text())
        problems = [f"{cls} {f}: {n} (baseline {base.get(cls, {}).get(f, 0)})"
                    for cls, per in counts().items() for f, n in per.items() if n > base.get(cls, {}).get(f, 0)]
        self.assertEqual(problems, [], "a name written as a literal outside bin/names.py: use names.current/all/env")


def update() -> None:
    """Lower the baseline to today's counts (a file whose count rose stays at the old count, so the test still fails)."""
    base = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    now = counts()
    out = {cls: {f: min(n, base.get(cls, {}).get(f, n)) for f, n in sorted(now[cls].items())} for cls in CLASSES}
    BASELINE.write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    if sys.argv[1:] == ["--update"]:
        update()
    else:
        unittest.main()
