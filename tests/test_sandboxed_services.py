"""A sandboxed project's own services and command jobs run isolated (bin/sandboxed-run → isolation.py → the OS's
adapter: seatbelt.service_profile on macOS, landlock.py on Linux), never unsandboxed: a manifest.update adds them
without the owner's approval, so they must not get more than the project's sessions (10-31, Linux the same day).

    bin/py -m unittest tests.test_sandboxed_services
"""
import importlib.machinery
import importlib.util
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import isolation  # noqa: E402
import landlock  # noqa: E402
import registry  # noqa: E402
import seatbelt  # noqa: E402


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


render_services = load("render_services_sb", BIN / "render-services.py")
sandboxed_run = load("sandboxed_run_sb", BIN / "sandboxed-run")


def after_cli_last(args):
    """A service started by path runs through a shell that adds the platform's CLI folder to its PATH
    (render-services.cli_last): what that shell execs."""
    assert args[:2] == ["/bin/sh", "-c"] and args[3] == "sh", args
    return args[4:]


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws, self.rt = self.tmp / "ws", self.tmp / "rt"
        for d in (self.ws, self.rt):
            d.mkdir()
        (self.ws / "bin").mkdir()   # no runtime: the service runs in the workspace
        (self.ws / "bin" / "serve.sh").write_text("#!/bin/sh\n")
        (self.ws / "bin" / "serve.sh").chmod(0o755)
        self.doc = {"name": "proj", "paths": {"workspace": str(self.ws)},
                    "enforce": {"sandbox": True},
                    "services": [{"name": "viewer", "run": "bin/serve.sh", "keepalive": True,
                                  "expose": {"tailnet_https": 8445, "local_port": 18123}}],
                    "jobs": [{"name": "nightly", "at": "03:00", "run": "python3 x.py"},
                             # a sandboxed project's agent job runs only confined, so it declares allow:
                             {"name": "think", "at": "04:00", "kind": "agent", "allow": ["Read"]}]}


class Render(Fixture):
    def test_sandboxed_service_goes_through_sandboxed_run(self):
        with mock.patch.object(isolation, "can_sandbox", lambda: None):
            plist, problem = render_services.render("proj", self.doc, self.doc["services"][0])
        self.assertIsNone(problem)
        args = after_cli_last(plist["ProgramArguments"])
        self.assertTrue(args[1].endswith("sandboxed-run"), args)
        i = args.index("--")
        self.assertEqual(args[2:i], ["--project", "proj", "--cwd", str(self.ws), "--port", "18123"])
        self.assertEqual(args[i + 1:], [str(self.ws / "bin" / "serve.sh")])

    def test_unsandboxed_and_exempt_services_are_unchanged(self):
        for doc in ({**self.doc, "enforce": {}}, self.doc):
            with mock.patch.object(isolation, "can_sandbox", lambda: None), \
                    mock.patch.object(isolation.hubconf, "get",
                                      lambda k, d=None: ["proj"] if k == "service_sandbox_exempt" else d):
                plist, _ = render_services.render("proj", doc, doc["services"][0])
            self.assertEqual(after_cli_last(plist["ProgramArguments"]), [str(self.ws / "bin" / "serve.sh")])

    def test_no_adapter_means_not_rendered(self):
        with mock.patch.object(isolation, "can_sandbox", lambda: "this system has no Seatbelt (linux)"):
            _, problem = render_services.render("proj", self.doc, self.doc["services"][0])
        self.assertIn("linux", problem or "")   # reported (in the UI language), so it is not rendered


class Jobs(Fixture):
    def jobs(self, doc):
        out, _, _ = registry.scheduler_jobs({"projects": {"proj": doc}}, set())
        return {j["name"]: j["command"] for j in out}

    def test_command_job_is_wrapped_agent_job_is_not(self):
        cmds = self.jobs(self.doc)
        self.assertIn("sandboxed-run --project proj --cwd", cmds["proj/nightly"])
        self.assertTrue(cmds["proj/nightly"].endswith("-- /bin/sh -c 'python3 x.py'"), cmds["proj/nightly"])
        self.assertNotIn("sandboxed-run", cmds["proj/think"])
        self.assertNotIn("sandboxed-run", self.jobs({**self.doc, "enforce": {}})["proj/nightly"])


class Launcher(unittest.TestCase):
    def test_refuses_without_an_adapter_and_never_runs_the_command(self):
        with mock.patch.object(isolation, "can_sandbox", lambda: "this system has no Seatbelt (linux)"), \
                mock.patch.object(os, "execv") as ex, redirect_stderr(io.StringIO()) as err:
            rc = sandboxed_run.main(["--project", "proj", "--cwd", "/", "--", "true"])
        self.assertEqual(rc, 78)
        ex.assert_not_called()
        self.assertIn("never runs unsandboxed", err.getvalue())

    def test_unknown_project_is_refused(self):
        with mock.patch.object(isolation, "can_sandbox", lambda: None), \
                mock.patch.object(sandboxed_run.hubconf, "projects", lambda: {}), \
                mock.patch.object(os, "execv") as ex, redirect_stderr(io.StringIO()):
            self.assertEqual(sandboxed_run.main(["--project", "nope", "--cwd", "/", "--", "true"]), 78)
        ex.assert_not_called()


class Adapter(unittest.TestCase):
    def test_picks_the_adapter_for_this_os(self):
        with mock.patch.object(isolation.sys, "platform", "darwin"):
            self.assertEqual(isolation.adapter()[0], "seatbelt")
        with mock.patch.object(isolation.sys, "platform", "linux"):
            self.assertEqual(isolation.adapter()[0], "landlock")
        with mock.patch.object(isolation.sys, "platform", "win32"):
            self.assertIsNone(isolation.adapter())
            self.assertIn("no sandbox adapter", isolation.can_sandbox())

    def test_landlock_refuses_an_old_kernel(self):
        with mock.patch.object(landlock.sys, "platform", "linux"), \
                mock.patch.object(landlock.platform, "machine", lambda: "x86_64"), \
                mock.patch.object(landlock, "abi", lambda: 3):
            self.assertIn("ABI is 3", landlock.can_sandbox())

    def test_an_adapter_missing_a_required_guarantee_is_refused(self):
        class Weak:
            can_sandbox = staticmethod(lambda: None)
            guarantees = staticmethod(lambda: {"own-writes-only", "isolated-reads"})
            exec_service = mock.Mock()
        with mock.patch.object(isolation, "adapter", lambda: ("weak", Weak)):
            self.assertIn("doesn't guarantee declared-port-only", isolation.can_sandbox())
            self.assertEqual(isolation.guarantees(), [])
            with self.assertRaises(OSError):
                isolation.exec_service({}, [], None, ["true"])
        Weak.exec_service.assert_not_called()

    def test_an_adapter_declaring_an_unknown_guarantee_is_refused(self):
        class Odd:
            can_sandbox = staticmethod(lambda: None)
            guarantees = staticmethod(lambda: {*isolation.REQUIRED, "network-allowlist"})
        with mock.patch.object(isolation, "adapter", lambda: ("odd", Odd)):
            self.assertIn("unknown guarantees: network-allowlist", isolation.can_sandbox())

    def test_exec_service_refuses_when_the_adapter_cant(self):
        with mock.patch.object(isolation.sys, "platform", "linux"), \
                mock.patch.object(landlock, "can_sandbox", lambda: "no Landlock here"), \
                mock.patch.object(landlock, "exec_service") as ex:
            with self.assertRaises(OSError):
                isolation.exec_service({}, [], None, ["true"])
        ex.assert_not_called()


class LandlockPlan(Fixture):
    """The deny-list policy → Landlock's allow-only rules: a denied path is cut out by allowing its siblings."""

    def pol(self, other, cred):
        return {"workspace": self.ws, "write": [str(self.tmp / "bus")], "other_projects": [other],
                "deny_read": [str(self.tmp / "copies" / "*" / "memory")], "credentials": [self.tmp / "home" / ".ssh"],
                "credential_files": [cred]}

    def walk_here(self):
        """plan() walks the real filesystem from /, through every folder that holds a denied path: here, the system's
        temporary folder (240,000 entries on the owner's machine on 11-01, 35 s a test). What it decides is the same
        from the test's own folder."""
        orig = landlock.read_roots
        return mock.patch.object(landlock, "read_roots", lambda deny, root=None: orig(deny, self.tmp))

    def plan(self):
        home = self.tmp / "home"
        for d in (home / ".ssh", home / ".config", self.tmp / "copies" / "a" / "memory", self.tmp / "other"):
            d.mkdir(parents=True)
        (home / ".netrc").write_text("x")
        (home / ".gitconfig").write_text("x")
        (home / "link-to-ssh").symlink_to(home / ".ssh")
        (self.tmp / "copies" / "a" / "STATE.md").write_text("x")
        with mock.patch.object(landlock.hubconf, "p", lambda k, d=None: None), \
                mock.patch.object(landlock.hubconf, "STATE", self.tmp / "state"), \
                mock.patch.object(landlock.hubconf, "BUS_DIR", self.tmp / "bus"), self.walk_here():
            deny = landlock.denied(self.pol(self.tmp / "other", home / ".netrc"))
            return landlock.read_roots(deny, self.tmp), landlock.plan(self.pol(self.tmp / "other", home / ".netrc"),
                                                                     [self.rt, self.ws], 18123)

    def test_denied_paths_are_cut_out(self):
        read, pl = self.plan()
        home = self.tmp / "home"
        self.assertIn(home / ".config", read)
        self.assertIn(home / ".gitconfig", read)
        self.assertIn(self.tmp / "copies" / "a" / "STATE.md", read)
        self.assertIn(self.ws, read)
        for p in (home, home / ".ssh", home / ".netrc", home / "link-to-ssh", self.tmp / "other",
                  self.tmp / "copies", self.tmp / "copies" / "a", self.tmp / "copies" / "a" / "memory", self.tmp):
            self.assertNotIn(p, read)
        self.assertEqual(pl["bind"], [18123])
        self.assertIn(self.rt, pl["write"])
        self.assertIn(self.tmp / "bus", pl["write"])

    def test_no_port_no_bind_rule(self):
        with mock.patch.object(landlock.hubconf, "p", lambda k, d=None: None), self.walk_here():
            pl = landlock.plan(self.pol(self.tmp / "other", self.tmp / "cred"), [self.ws])
        self.assertEqual(pl["bind"], [])


# what the real run checks, from a shell and from a child python (rules are inherited); every line is the same on
# macOS and Linux, so one probe runs through whichever adapter this machine has
PROBE = """
touch "$RT/a" && echo rt-write
touch "$WS/a" && echo ws-write
touch "$SHARED/a" && echo linked-write
touch "$BUS/a" 2>/dev/null || echo bus-write-blocked
ls "$BUS" >/dev/null 2>&1 || echo bus-read-blocked
touch "$TOP/outside" 2>/dev/null || echo outside-write-blocked
[ -d "$ROOT" ] && echo root-stat
ls "$ROOT" >/dev/null 2>&1 || echo root-list-blocked
mkdir -p "$WS/.ctl" && { touch "$WS/.ctl/settings" 2>/dev/null || echo control-blocked; }
cat "$OTHER/secret" >/dev/null 2>&1 || echo other-blocked
cat "$CRED" >/dev/null 2>&1 || echo cred-blocked
cat "$SECRETS_LINK/key" >/dev/null 2>&1 || echo linked-secrets-blocked
cat "$SECRETS_REAL/key" >/dev/null 2>&1 || echo linked-secrets-target-blocked
cat /etc/hosts >/dev/null && echo system-read
kill -0 "$OUTSIDE_PID" 2>/dev/null || echo signal-blocked
/usr/bin/security list-keychains >/dev/null 2>&1 || echo keychain-blocked
"$PY" - <<'PY'
import socket
def bind(port):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port)); return "ok"
    except OSError as e:
        return "blocked" if e.errno in (1, 13) else str(e)
    finally:
        s.close()
print("bind-declared-" + bind(int(__import__("os").environ["PORT"])))
print("bind-other-" + bind(int(__import__("os").environ["PORT"]) + 1))
try:
    socket.socket(socket.AF_UNIX).close(); print("unix-ok")
except OSError:
    print("unix-blocked")
a, b = socket.socketpair(); print("socketpair-ok")
PY
"""
# what a sandboxed service must still be able to do, whatever the adapter; root-stat: the folder above its own (the
# workspace root, whose contents it can't read) can be stat'ed, or a program resolving the path to its own folder fails
# (git 2.56 resolves every component of `git -C <dir>`: "Invalid path '<workspace root>': Operation not permitted")
WORKS = {"rt-write", "ws-write", "linked-write", "system-read", "bind-declared-ok", "socketpair-ok", "root-stat"}
# guarantee (isolation.GUARANTEES) → the probe lines that show it holds
PROBES = {"own-writes-only": {"outside-write-blocked", "bus-write-blocked"},
          "isolated-reads": {"other-blocked", "cred-blocked", "bus-read-blocked", "root-list-blocked",
                             "linked-secrets-blocked", "linked-secrets-target-blocked"},
          "declared-port-only": {"bind-other-blocked"}, "unix-sockets-blocked": {"unix-blocked"},
          "keychain-blocked": {"keychain-blocked"}, "signals-blocked": {"signal-blocked"},
          "runtime-files-locked": {"control-blocked"}}
# guarantees no probe here can show, with why (checked by hand when an adapter declares them)
UNPROBED = {"localhost-inbound-only": "needs a second address to connect from (macOS checked live 10-31)"}

# runs sandboxed-run's main() in a child: the registry, the bus and hub.json's credentials replaced with the fixture's
RUNNER = """
import importlib.machinery, importlib.util, pathlib, sys, json
args = json.loads(sys.argv[1])
sys.path.insert(0, args["bin"])
import hubconf, landlock, policy, seatbelt
policy.protect = lambda: [{"name": ".ctl/settings", "tree": True}]   # what a runtime's control port would name
# the fixture lives in the temp area: a writable temp area would hide writes outside and make every path in it readable
landlock.TMP = []
seatbelt.TMP = []
hubconf.BUS_DIR = pathlib.Path(args["bus"])
real_p = hubconf.p
hubconf.p = lambda k, d=None: pathlib.Path(args["root"]) if k == "workspace_root" else real_p(k, d)
hubconf.projects = lambda: args["projects"]
real_get = hubconf.get
hubconf.get = lambda k, d=None: [args["cred"]] if k == "credentials.files" else real_get(k, d)
loader = importlib.machinery.SourceFileLoader("sr", args["bin"] + "/sandboxed-run")
spec = importlib.util.spec_from_loader("sr", loader); m = importlib.util.module_from_spec(spec); loader.exec_module(m)
sys.exit(m.main(["--project", "proj", "--cwd", args["ws"], "--port", str(args["port"]), "--", "/bin/sh", "-c",
                 args["probe"]]))
"""


class Conformance(unittest.TestCase):
    def test_what_counts_as_a_temp_area_sandboxes_write(self):
        for t in [*seatbelt.TMP, *landlock.TMP]:
            self.assertTrue(isolation.in_system_tmp(pathlib.Path(t) / "x"), t)
        self.assertFalse(isolation.in_system_tmp("/etc"))   # not BIN: bin/linux-test copies the tree into /tmp

    def test_every_guarantee_has_a_probe_or_a_reason(self):
        self.assertEqual(set(isolation.GUARANTEES), set(PROBES) | set(UNPROBED))
        self.assertFalse(set(PROBES) & set(UNPROBED))

    def test_the_adapters_declare_known_guarantees_and_the_required_ones(self):
        with mock.patch.object(landlock, "abi", lambda: 6):
            for mod in (seatbelt, landlock):
                self.assertLessEqual(mod.guarantees(), set(isolation.GUARANTEES), mod.__name__)
                self.assertLessEqual(set(isolation.REQUIRED), mod.guarantees(), mod.__name__)

    @unittest.skipUnless(isolation.can_sandbox() is None, "needs this machine's adapter (sandbox-exec, Landlock ABI 4)")
    def test_this_machines_adapter_holds_what_it_declares(self):
        tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = tmp / "workspace"   # the workspace root: the projects' folders are in it
        ws, other, bus = root / "ws", root / "other", tmp / "bus"
        for d in (ws, other, bus):
            d.mkdir(parents=True)
        (other / "secret").write_text("x")
        cred = tmp / "cred"
        cred.write_text("token")
        # the secrets folder named through a link (as one reached through the instance folder .hub-home points to):
        # the target is what gets opened, so it must be refused too
        secrets_real, secrets_link = tmp / "secrets-real", tmp / "secrets-link"
        secrets_real.mkdir()
        (secrets_real / "key").write_text("x")
        secrets_link.symlink_to(secrets_real)
        # an allow_write entry under a linked folder: what it resolves to is what gets written
        (tmp / "shared-real" / "shared").mkdir(parents=True)
        (tmp / "shared-link").symlink_to(tmp / "shared-real")
        shared = tmp / "shared-link" / "shared"
        outside = subprocess.Popen(["sleep", "60"])
        self.addCleanup(outside.wait)
        self.addCleanup(outside.kill)
        port = 18125
        args = {"bin": str(BIN), "ws": str(ws), "cred": str(cred), "port": port, "probe": PROBE, "bus": str(bus),
                "root": str(root),
                "projects": {"proj": {"name": "proj", "paths": {"workspace": str(ws)}, "enforce": {"sandbox": True},
                                      "allow_write": [str(shared)]},
                             "other": {"name": "other", "paths": {"workspace": str(other)}}}}
        # no runtime in the fixture (it would be under services_root): the workspace stands in for it
        env = {**os.environ, "RT": str(ws), "WS": str(ws), "BUS": str(bus), "TOP": str(tmp), "OTHER": str(other),
               "ROOT": str(root),
               "CRED": str(cred), "PORT": str(port), "OUTSIDE_PID": str(outside.pid),
               "SECRETS_LINK": str(secrets_link), "SECRETS_REAL": str(secrets_real),
               "AGENT_HUB_SECRETS": str(secrets_link), "SHARED": str(shared),
               "PY": sys.executable}   # the platform's Python: never rely on a python3 on PATH (AGENTS.md)
        r = subprocess.run([sys.executable, "-c", RUNNER, __import__("json").dumps(args)], capture_output=True,
                           text=True, env=env)
        got = set(r.stdout.split())
        name = isolation.adapter()[0]
        self.assertLessEqual(WORKS, got, f"{name}: {r.stdout} {r.stderr}")
        for g in isolation.guarantees():
            self.assertLessEqual(PROBES.get(g, set()), got, f"{name} declares {g}: {r.stdout} {r.stderr}")


class Profile(Fixture):
    def pol(self, other, **kw):
        return {"workspace": self.ws, "write": [str(self.tmp / "bus")], "other_projects": [other], "deny_read": [],
                "credentials": [], "credential_files": [], **kw}

    def test_a_denied_link_is_denied_by_both_spellings(self):
        real = self.tmp / "real"
        (real / "secrets").mkdir(parents=True)
        (real / "secrets" / "key").write_text("x")
        (real / "copies" / "a").mkdir(parents=True)
        link = self.tmp / "link"
        link.symlink_to(real)
        (self.tmp / "alias").mkdir()
        own = self.tmp / "alias" / "lws"   # the own folder named through a link in another folder
        own.symlink_to(self.ws)
        (self.tmp / "wreal" / "shared").mkdir(parents=True)
        (self.tmp / "wlink").symlink_to(self.tmp / "wreal")
        shared = self.tmp / "wlink" / "shared"   # an allow_write entry under a linked folder
        pol = self.pol(link / "other", deny_read=[link / "secrets", link / "copies" / "*" / "memory"],
                       credential_files=[link / "secrets" / "key"], write=[str(shared)])
        p = seatbelt.service_profile(pol, [self.rt, own])
        deny = next(x for x in p.splitlines() if x.startswith("(deny file-read*"))
        for d in ("other", "secrets"):
            self.assertIn(f'(subpath "{link / d}")', deny)
            self.assertIn(f'(subpath "{real / d}")', deny)
        self.assertIn(f'(literal "{link / "secrets" / "key"}")', deny)
        self.assertIn(f'(literal "{real / "secrets" / "key"}")', deny)
        for d in (link, real):
            self.assertIn(f"(regex {seatbelt.glob_regex(d / 'copies' / '*' / 'memory')})", deny)
        # the allows are only what they were: the own folders resolved, nothing under either spelling of a denied path
        allow = next(x for x in p.splitlines() if x.startswith("(allow file-read*"))
        self.assertIn(f'(subpath "{self.ws}")', allow)
        self.assertNotIn(str(own), allow)
        self.assertNotIn(str(real), allow)
        self.assertNotIn(str(link), allow)
        # writes: what the entries resolve to (Seatbelt matches the real path: an allow naming the link never matches)
        write = next(x for x in p.splitlines() if x.startswith("(allow file-write*"))
        for d in (self.ws, self.rt, self.tmp / "wreal" / "shared"):
            self.assertIn(f'(subpath "{d}")', write)
        self.assertNotIn(str(own), write)
        self.assertNotIn(str(shared), write)
        # stat only, for the folders above each own folder whichever way it is named: above the link as given (resolving
        # it walks those first), and above what it resolves to; never the own folders themselves
        meta = next(x for x in p.splitlines() if x.startswith("(allow file-read-metadata"))
        for d in (self.tmp, self.tmp / "alias", self.tmp / "wlink", self.tmp / "wreal"):
            self.assertIn(f'(literal "{d}")', meta)
        for d in (own, self.ws, shared, self.tmp / "wreal" / "shared"):
            self.assertNotIn(f'"{d}"', meta)

    def test_port_rule_only_with_a_port(self):
        p = seatbelt.service_profile(self.pol(self.tmp / "other"), [self.rt, self.ws])
        self.assertNotIn("allow network-bind", p)
        p = seatbelt.service_profile(self.pol(self.tmp / "other"), [self.rt, self.ws], 18123)
        self.assertIn('(allow network-bind (local ip "*:18123"))', p)
        self.assertIn('(allow network-inbound (local ip "localhost:*"))', p)


class RunAsProject(unittest.TestCase):
    """isolation.run_as_project (docs/design/git-config-fence.md, r2 B3): a platform command that runs a sandboxed
    project's own commands goes on only inside a sandbox that holds; outside one it is wrapped in a fixed environment,
    or refused, never run as is."""
    DOC = {"enforce": {"sandbox": True}}
    GUARD = "TEST_RUN_AS_PROJECT_GUARD"

    def setUp(self):
        p = mock.patch.dict(os.environ, {}, clear=False)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop(self.GUARD, None)

    def test_not_sandboxed_or_confined_goes_on(self):
        self.assertIsNone(isolation.run_as_project("p", {}, ["x"], self.GUARD))
        with mock.patch.object(isolation, "confined_now", lambda marked=False: True):
            self.assertIsNone(isolation.run_as_project("p", self.DOC, ["x"], self.GUARD))

    def test_outside_a_sandbox_it_is_wrapped_in_a_fixed_environment(self):
        os.environ.update(OWNER_TOKEN="s3cret", GIT_INDEX_FILE="/x/index", AGENT_HUB_STATE="/s", PATH="/usr/bin")
        with mock.patch.object(isolation, "confined_now", lambda marked=False: False), \
                mock.patch.object(isolation, "can_sandbox", lambda: None):
            argv, env = isolation.run_as_project("p", self.DOC, ["x", "y"], self.GUARD)
        self.assertIn("--project", argv)
        self.assertEqual(argv[-3:], ["--", "x", "y"])
        self.assertEqual(env[self.GUARD], "1")
        self.assertNotIn("OWNER_TOKEN", env, "nothing of the starter's beyond the kept names")
        self.assertEqual((env["GIT_INDEX_FILE"], env["AGENT_HUB_STATE"], env["PATH"]), ("/x/index", "/s", "/usr/bin"))
        self.assertNotIn(self.GUARD, os.environ, "this process's own environment is left as it was")

    def test_no_sandbox_here_or_a_mark_outside_one_refuses(self):
        with mock.patch.object(isolation, "confined_now", lambda marked=False: False), \
                mock.patch.object(isolation, "can_sandbox", lambda: "no adapter"):
            with self.assertRaises(isolation.SandboxRefused):
                isolation.run_as_project("p", self.DOC, ["x"], self.GUARD)
        os.environ[self.GUARD] = "1"
        with mock.patch.object(isolation, "confined_now", lambda marked=False: False), \
                mock.patch.object(isolation, "can_sandbox", lambda: None):
            with self.assertRaises(isolation.SandboxRefused):
                isolation.run_as_project("p", self.DOC, ["x"], self.GUARD)

    def test_some_sandbox_is_not_enough(self):
        """Review F4: the adapter's own check passes inside any profile; confined needs the platform's state unreadable
        too, and Landlock's check needs the mark."""
        fake = type("A", (), {"confined_now": staticmethod(lambda marked=False: True)})
        with mock.patch.object(isolation, "adapter", lambda: ("seatbelt", fake)):
            with mock.patch.object(isolation, "reads_blocked", lambda: False):
                self.assertFalse(isolation.confined_now())
            with mock.patch.object(isolation, "reads_blocked", lambda: True):
                self.assertTrue(isolation.confined_now())
        self.assertFalse(landlock.confined_now(False), "without the mark, never")

    def test_a_refusal_says_which_part_of_confinement_is_missing(self):
        """CI, Linux: the fixture's platform state sat in the temp area Landlock grants whole, so the wrapped run saw it;
        the refusal says that, not only "can't be confirmed"."""
        fake = type("A", (), {"confined_now": staticmethod(lambda marked=False: True)})
        with mock.patch.object(isolation, "adapter", lambda: ("landlock", fake)), \
                mock.patch.object(isolation, "readable_platform_folder", lambda: pathlib.Path("/t/state")):
            self.assertIn("/t/state", isolation.confinement_gap(True))
        blind = type("A", (), {"confined_now": staticmethod(lambda marked=False: False)})
        with mock.patch.object(isolation, "adapter", lambda: ("landlock", blind)):
            self.assertIn("can't be seen", isolation.confinement_gap(True))

    def test_reads_blocked_is_a_probe(self):
        self.assertFalse(isolation.reads_blocked(), "this test process can read the platform's state")

    @unittest.skipUnless(seatbelt.can_sandbox() is None and not seatbelt.confined_now(), "needs Seatbelt, outside a profile")
    def test_seatbelt_sees_its_own_profile(self):
        r = subprocess.run([seatbelt.SANDBOX_EXEC, "-p", "(version 1)(allow default)", sys.executable, "-c",
                            "import sys; sys.path.insert(0, sys.argv[1]); import seatbelt; print(seatbelt.confined_now())",
                            str(pathlib.Path(seatbelt.__file__).parent)], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "True", r.stderr)


if __name__ == "__main__":
    unittest.main()
