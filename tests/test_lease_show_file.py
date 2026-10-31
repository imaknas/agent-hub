"""Showing the user a project's file during a discussion (docs/design/lease-show-file.md, the assistant's
m10251606).

- lease_files: outside the folder / symlink / not a regular file / type / content mismatch / too big / lease limit /
  looks like credentials / registered secrets are all blocked; what is sent is the copy.
- runner: /v1/internal/<lease>/show sends a v2 file event (valid per schema); GET …/files/<id> returns the file and
  headers; a refusal returns the reason.
- relay's show_file tool: forwards to the runner, returns the result to the session.

    bin/py -m unittest discover -s tests
"""
import importlib.util
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import i18n  # noqa: E402
import lease_files  # noqa: E402
import minischema  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
V2 = json.loads((ROOT / "contracts" / "lease-runner.v2.schema.json").read_text())


def T(msgid, **kw):
    """lease_files' refusal reason in zh-Hant (the classes below run in zh-Hant and assert those texts)"""
    return i18n.translate("lease_files", msgid, kw, "zh-Hant")


SECRET_NAME = "{name} looks like a credential or a secret; not sent"
SECRET_TEXT = "{path}: the content looks like a credential (a private key or an API key); not sent"
TOO_MANY = ("This lease has already shown {n} {n:file|files} in the last {hours} {hours:hour|hours}, which is the limit; "
            "show it later, or say in text where the file is for now")
HOURS = lease_files.WINDOW_S // 3600


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "zh-Hant"})   # the assertions are the zh-Hant texts
class Take(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws, self.store = self.tmp / "ws", self.tmp / "store"
        (self.ws / "results").mkdir(parents=True)
        (self.ws / "results" / "a.png").write_bytes(PNG)
        (self.tmp / "outside.png").write_bytes(PNG)

    def take(self, path, **kw):
        return lease_files.take(self.ws, path, "look at this", self.store, **kw)

    def refused(self, path, **kw):
        meta, why = self.take(path, **kw)
        self.assertIsNone(meta, path)
        self.assertTrue(why)
        return why

    def test_ok_and_copy_is_kept(self):
        meta, why = self.take("results/a.png")
        self.assertEqual(why, "")
        self.assertEqual((meta["path"], meta["name"], meta["mime"], meta["size"], meta["summary"]),
                         ("results/a.png", "a.png", "image/png", len(PNG), "look at this"))
        # original changed, then deleted: what is sent is still the copy from then
        (self.ws / "results" / "a.png").write_bytes(b"changed")
        (self.ws / "results" / "a.png").unlink()
        got_meta, data = lease_files.read(self.store, meta["id"])
        self.assertEqual(data, PNG)
        self.assertEqual(got_meta["mime"], "image/png")

    def test_absolute_inside_ok(self):
        self.assertIsNotNone(self.take(str(self.ws / "results" / "a.png"))[0])

    def test_outside(self):
        self.refused(str(self.tmp / "outside.png"))
        self.refused("../outside.png")
        self.refused("results/../../outside.png")
        (self.ws / "link.png").symlink_to(self.tmp / "outside.png")
        self.refused("link.png")
        (self.ws / "dirlink").symlink_to(self.tmp)
        self.refused("dirlink/outside.png")

    def test_symlink_inside_is_fine(self):
        (self.ws / "alias.png").symlink_to(self.ws / "results" / "a.png")
        self.assertIsNotNone(self.take("alias.png")[0])

    def test_not_a_regular_file(self):
        (self.ws / "d.png").mkdir()
        self.refused("d.png")
        self.refused("missing.png")
        self.refused("")

    def test_types(self):
        for name, data in (("x.svg", b"<svg/>"), ("x.html", b"<html>"), ("x.exe", b"MZ"), ("noext", b"x")):
            (self.ws / name).write_bytes(data)
            self.refused(name)
        (self.ws / "fake.png").write_bytes(b"not really a png")
        self.assertIn(T("{path}: the content is not {type} (the extension does not match the content)", path="fake.png",
                        type="png"), self.refused("fake.png"))
        (self.ws / "bin.txt").write_bytes(b"\xff\xfe\x00bad")
        self.refused("bin.txt")
        for name, data in (("n.md", "# café notes".encode()), ("d.json", b"{}"), ("t.csv", b"a,b"),
                           ("p.pdf", b"%PDF-1.7"), ("m.glb", b"glTF\x02"), ("j.jpg", b"\xff\xd8\xff\xe0"),
                           ("g.gif", b"GIF89a"), ("w.webp", b"RIFF\0\0\0\0WEBPVP8 ")):
            (self.ws / name).write_bytes(data)
            self.assertIsNotNone(self.take(name)[0], name)

    def test_size_and_lease_limits(self):
        with mock.patch.object(lease_files, "MAX_FILE", 10):
            self.assertIn(T("{path} is too big ({mb} MB; the limit is {limit} MB)", path="results/a.png", mb=0, limit=0),
                          self.refused("results/a.png"))
        with mock.patch.object(lease_files, "MAX_COUNT", 1):
            self.assertIsNotNone(self.take("results/a.png")[0])
            self.assertIn(T(TOO_MANY, n=1, hours=HOURS), self.refused("results/a.png"))
        with mock.patch.object(lease_files, "MAX_TOTAL", len(PNG) + 1):
            self.refused("results/a.png")

    def test_limits_count_only_the_recent_window(self):
        # a long-lived lease (the same discussion resumed over several days): old ones do not count, so it never
        # gets stuck (m10252240)
        with mock.patch.object(lease_files, "MAX_COUNT", 2):
            self.assertIsNotNone(self.take("results/a.png")[0])
            self.assertIsNotNone(self.take("results/a.png")[0])
            self.assertIn(T(TOO_MANY, n=2, hours=HOURS), self.refused("results/a.png"))
            for m in self.store.glob("*.json"):  # make the first two given two days ago
                d = json.loads(m.read_text())
                d["at"] -= 2 * 86400
                m.write_text(json.dumps(d))
            self.assertIsNotNone(self.take("results/a.png")[0])

    def test_secret_like_files(self):
        for name in ("api_token.json", "secrets.json", "credentials.json", "id_rsa.txt", "password.txt", "server.key.txt"):
            (self.ws / name).write_text("{}")
        for name in ("api_token.json", "secrets.json", "credentials.json", "id_rsa.txt", "password.txt"):
            self.assertIn(T(SECRET_NAME, name=name), self.refused(name))
        (self.ws / "data.json").write_text("{}")
        self.refused("data.json", secret_paths=[self.ws / "data.json"])

    def test_hard_link_refused(self):
        """10-25 review: a hard link to a file outside the folder, which resolve/O_NOFOLLOW cannot see (the runner is
        not in the sandbox, so it must not read on the session's behalf)."""
        (self.tmp / "out.txt").write_text("outside")
        os.link(self.tmp / "out.txt", self.ws / "looks-fine.txt")
        self.assertIn("hard link", self.refused("looks-fine.txt"))

    def test_opened_file_must_be_inside(self):
        """10-25 review: the path swapped between resolve and open: the fd actually opened is what counts."""
        real_open = os.open

        def swap(path, *a, **k):  # swap the folder for a symlink pointing outside before opening
            if str(path).endswith("a.png") and not (self.ws / "results").is_symlink():
                shutil.move(str(self.ws / "results"), str(self.tmp / "moved"))
                (self.tmp / "evil").mkdir()
                (self.tmp / "evil" / "a.png").write_bytes(PNG)
                (self.ws / "results").symlink_to(self.tmp / "evil")
            return real_open(path, *a, **k)
        with mock.patch.object(lease_files.os, "open", swap):
            self.assertIn(T("{path}: what was opened is not that file in the folder (was the path swapped?); not sent",
                            path="results/a.png"), self.refused("results/a.png"))

    def test_secret_content_refused(self):
        for name, text in (("service-account.json", '{"type": "service_account", "private_key": "x"}'),
                           ("notes.md", "-----BEGIN OPENSSH PRIVATE KEY-----\nabc"),
                           ("env.txt", "ANTHROPIC=sk-ant-api03-abcdefghijklmnop"),
                           ("gh.txt", "ghp_" + "a" * 36), ("aws.csv", "id,AKIAABCDEFGHIJKLMNOP")):
            (self.ws / name).write_text(text)
            self.assertIn(T(SECRET_TEXT, path=name), self.refused(name), name)
        (self.ws / "plain.md").write_text("# Room size\nwidth 3.2 m, café corner")
        self.assertIsNotNone(self.take("plain.md")[0])

    def test_prune_removes_orphans(self):
        self.store.mkdir()
        orphan = self.store / ("f" * 16)
        orphan.write_bytes(b"x")
        os.utime(orphan, (0, 0))
        self.assertEqual(lease_files.prune(self.store, 3600), 1)
        self.assertFalse(orphan.exists())

    def test_read_rejects_bad_ids_and_prune(self):
        meta, _ = self.take("results/a.png")
        self.assertIsNone(lease_files.read(self.store, "../x"))
        self.assertIsNone(lease_files.read(self.store, "0" * 16))
        self.assertEqual(lease_files.prune(self.store, 3600), 0)
        self.assertEqual(lease_files.prune(self.store, -1), 1)
        self.assertIsNone(lease_files.read(self.store, meta["id"]))


@mock.patch.dict(os.environ, {"AGENT_HUB_LANG_UI": "zh-Hant"})   # the assertions are the zh-Hant texts
class Runner(unittest.TestCase):
    def setUp(self):
        import http.server
        self.m = m = load("lease_runner_sf", "lease-runner.py")
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        m.DIR = self.tmp / "state"
        m.token = lambda: "t"
        self.ws = self.tmp / "ws"
        self.ws.mkdir()
        (self.ws / "r.png").write_bytes(PNG)
        (self.ws / "secret.txt").write_text("x")
        self.lease = m.Lease({"lease_id": "99999999-9999-4999-8999-999999999999", "project": "zz-none", "cwd": str(self.ws)})
        self.lease.state = "idle_stopped"
        m.LEASES[self.lease.id] = self.lease
        self.addCleanup(m.LEASES.clear)
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), m.H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     method="POST" if body is not None else "GET", headers={"Authorization": "Bearer t"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def events(self):
        return [json.loads(l) for l in (self.lease.dir / "events.jsonl").read_text().splitlines()]

    def test_show_then_fetch(self):
        code, _, body = self.call(f"/v1/internal/{self.lease.id}/show", {"path": "r.png", "summary": "vue aérienne"})
        self.assertEqual(code, 200)
        fid = json.loads(body)["id"]
        ev = [e for e in self.events() if e["type"] == "file"][0]
        out = self.m.render(ev)
        self.assertEqual(minischema.errors(V2["$defs"]["event"], out, root=V2), [])
        self.assertEqual(minischema.errors(V2["$defs"]["file_event"], out, root=V2), [])
        self.assertEqual((out["id"], out["path"], out["summary"]), (fid, "r.png", "vue aérienne"))
        code, headers, data = self.call(f"/v1/leases/{self.lease.id}/files/{fid}")
        self.assertEqual((code, data, headers["Content-Type"], headers["X-Content-Type-Options"]),
                         (200, PNG, "image/png", "nosniff"))
        self.assertIn("r.png", headers["Content-Disposition"])

    def test_refused_says_why_and_sends_nothing(self):
        code, _, body = self.call(f"/v1/internal/{self.lease.id}/show", {"path": "secret.txt"})
        self.assertEqual((code, json.loads(body)["error"]), (400, "file_refused"))
        self.assertIn(T(SECRET_NAME, name="secret.txt"), json.loads(body)["detail"])
        self.assertFalse((self.lease.dir / "events.jsonl").exists())

    def test_fetch_unknown(self):
        self.assertEqual(self.call(f"/v1/leases/{self.lease.id}/files/{'0' * 16}")[0], 404)
        self.assertEqual(self.call(f"/v1/leases/{self.lease.id}/files/..%2Fx")[0], 404)
        self.assertEqual(self.call(f"/v1/leases/nope/files/{'0' * 16}")[0], 404)

    def test_needs_token(self):
        req = urllib.request.Request(f"{self.base}/v1/leases/{self.lease.id}/files/{'0' * 16}")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 401)

    def test_registry_secrets_are_refused(self):
        with mock.patch.object(self.m.hubconf, "projects", return_value={"zz-none": {
                "paths": {"workspace": str(self.ws)}, "secrets": [{"name": "k", "path": "r.png"}]}}), \
                mock.patch.object(self.m.hubconf, "workspace", return_value=self.ws):
            meta, why = self.m.show_file(self.lease, "r.png", None)
        self.assertIsNone(meta)
        self.assertIn(T(SECRET_NAME, name="r.png"), why)

    def test_preamble_mentions_show_file(self):
        self.assertIn("show_file", self.m.preamble("zz-none", "discuss"))
        self.assertIn("show_file", self.m.preamble("zz-none", "work"))

    def test_preamble_is_filled_with_the_configured_names(self):
        """the names and the content language come from settings; an idea's purpose is inserted as it is"""
        idea = {"type": "idea", "purpose": "try {{x}}", "enforce": {"capabilities": ["gpu"]}}
        with mock.patch.object(self.m.hubconf, "projects", return_value={"idea-1": idea}):
            work = self.m.preamble("idea-1", "work")
        discuss = self.m.preamble("zz-none", "discuss")
        for text in (work, discuss):
            self.assertIn(self.m.hubconf.owner_tag(), text)
            self.assertIn(self.m.hubconf.assistant_name(), text)
            self.assertNotIn("{{owner", text)
        self.assertIn('"try {{x}}"', work)
        self.assertIn("hub-run --cap gpu", work)
        self.assertIn("read-only", discuss)
        self.assertNotIn("hub-run", discuss)


class Relay(unittest.TestCase):
    def test_tool_passes_through(self):
        m = load("lease_relay_sf", "lease-relay.py")
        with mock.patch.object(m, "http", return_value=(200, {"id": "a" * 16, "path": "r.png", "mime": "image/png", "size": 9})):
            text, err = m.show_file({"path": "r.png"})
        self.assertFalse(err)
        self.assertIn("r.png", text)
        with mock.patch.object(m, "http", return_value=(400, {"error": "file_refused", "detail": "x.svg: this type cannot be sent"})):
            text, err = m.show_file({"path": "x.svg"})
        self.assertTrue(err)
        self.assertIn("type cannot be sent", text)

    def test_http_keeps_the_refusal_reason(self):
        # http() used to drop the body on a 4xx, so the session only saw "Not sent: 400" (m10252240); tested with a
        # real HTTP server, not by mocking http()
        import http.server
        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.dumps({"v": 2, "error": "file_refused", "detail": "100 files shown in the last 24 hours, which is the limit"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        m = load("lease_relay_sf3", "lease-relay.py")
        code, res = m.http("POST", f"http://127.0.0.1:{srv.server_port}/v1/internal/x/show", "t", {"path": "a.png"})
        self.assertEqual((code, res.get("error")), (400, "file_refused"))
        with mock.patch.object(m, "RUNNER", f"http://127.0.0.1:{srv.server_port}"), \
                mock.patch.object(m, "secret", lambda p: "t"):
            text, err = m.show_file({"path": "a.png"})
        self.assertTrue(err)
        self.assertIn("which is the limit", text)

    def test_tool_is_listed(self):
        m = load("lease_relay_sf2", "lease-relay.py")
        sent = []
        with mock.patch.object(m, "send", sent.append), \
                mock.patch.object(m.sys, "stdin", ['{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}\n']):
            m.main()
        self.assertIn("show_file", [t["name"] for t in sent[0]["result"]["tools"]])


if __name__ == "__main__":
    unittest.main()
