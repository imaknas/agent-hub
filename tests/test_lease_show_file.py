"""深談裡把專案的檔案給使用者看(docs/design/lease-show-file.md,Muninn m10251606)。

- lease_files:資料夾外/symlink/不是一般檔案/類型/內容對不上/太大/租約上限/像憑證/登錄的 secrets 都擋;送的是複製的那份。
- runner:/v1/internal/<lease>/show 送 v2 的 file 事件(合 schema);GET …/files/<id> 回檔案跟 header;拒絕回原因。
- relay 的 show_file 工具:轉給 runner、把結果回給 session。

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
import lease_files  # noqa: E402
import minischema  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
V2 = json.loads((ROOT / "contracts" / "lease-runner.v2.schema.json").read_text())


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Take(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws, self.store = self.tmp / "ws", self.tmp / "store"
        (self.ws / "results").mkdir(parents=True)
        (self.ws / "results" / "a.png").write_bytes(PNG)
        (self.tmp / "outside.png").write_bytes(PNG)

    def take(self, path, **kw):
        return lease_files.take(self.ws, path, "看這張", self.store, **kw)

    def refused(self, path, **kw):
        meta, why = self.take(path, **kw)
        self.assertIsNone(meta, path)
        self.assertTrue(why)
        return why

    def test_ok_and_copy_is_kept(self):
        meta, why = self.take("results/a.png")
        self.assertEqual(why, "")
        self.assertEqual((meta["path"], meta["name"], meta["mime"], meta["size"], meta["summary"]),
                         ("results/a.png", "a.png", "image/png", len(PNG), "看這張"))
        (self.ws / "results" / "a.png").write_bytes(b"changed")  # 原檔改掉、刪掉,送的還是當時那份
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
        self.assertIn("對不上", self.refused("fake.png"))
        (self.ws / "bin.txt").write_bytes(b"\xff\xfe\x00bad")
        self.refused("bin.txt")
        for name, data in (("n.md", "# 筆記".encode()), ("d.json", b"{}"), ("t.csv", b"a,b"),
                           ("p.pdf", b"%PDF-1.7"), ("m.glb", b"glTF\x02"), ("j.jpg", b"\xff\xd8\xff\xe0"),
                           ("g.gif", b"GIF89a"), ("w.webp", b"RIFF\0\0\0\0WEBPVP8 ")):
            (self.ws / name).write_bytes(data)
            self.assertIsNotNone(self.take(name)[0], name)

    def test_size_and_lease_limits(self):
        with mock.patch.object(lease_files, "MAX_FILE", 10):
            self.assertIn("太大", self.refused("results/a.png"))
        with mock.patch.object(lease_files, "MAX_COUNT", 1):
            self.assertIsNotNone(self.take("results/a.png")[0])
            self.assertIn("上限", self.refused("results/a.png"))
        with mock.patch.object(lease_files, "MAX_TOTAL", len(PNG) + 1):
            self.refused("results/a.png")

    def test_secret_like_files(self):
        for name in ("api_token.json", "secrets.json", "credentials.json", "id_rsa.txt", "password.txt", "server.key.txt"):
            (self.ws / name).write_text("{}")
        for name in ("api_token.json", "secrets.json", "credentials.json", "id_rsa.txt", "password.txt"):
            self.assertIn("憑證", self.refused(name))
        (self.ws / "data.json").write_text("{}")
        self.refused("data.json", secret_paths=[self.ws / "data.json"])

    def test_hard_link_refused(self):
        """10-25 review:hard link 指到資料夾外的檔,resolve/O_NOFOLLOW 看不出來(runner 不在 sandbox,不能讓它代讀)。"""
        (self.tmp / "out.txt").write_text("outside")
        os.link(self.tmp / "out.txt", self.ws / "looks-fine.txt")
        self.assertIn("hard link", self.refused("looks-fine.txt"))

    def test_opened_file_must_be_inside(self):
        """10-25 review:resolve 到 open 之間路徑被換掉——以實際打開的 fd 為準。"""
        real_open = os.open

        def swap(path, *a, **k):  # 打開前把資料夾換成指到外面的 symlink
            if str(path).endswith("a.png") and not (self.ws / "results").is_symlink():
                shutil.move(str(self.ws / "results"), str(self.tmp / "moved"))
                (self.tmp / "evil").mkdir()
                (self.tmp / "evil" / "a.png").write_bytes(PNG)
                (self.ws / "results").symlink_to(self.tmp / "evil")
            return real_open(path, *a, **k)
        with mock.patch.object(lease_files.os, "open", swap):
            self.assertIn("不是資料夾裡的那個檔", self.refused("results/a.png"))

    def test_secret_content_refused(self):
        for name, text in (("service-account.json", '{"type": "service_account", "private_key": "x"}'),
                           ("notes.md", "-----BEGIN OPENSSH PRIVATE KEY-----\nabc"),
                           ("env.txt", "ANTHROPIC=sk-ant-api03-abcdefghijklmnop"),
                           ("gh.txt", "ghp_" + "a" * 36), ("aws.csv", "id,AKIAABCDEFGHIJKLMNOP")):
            (self.ws / name).write_text(text)
            self.assertIn("內容像憑證", self.refused(name), name)
        (self.ws / "plain.md").write_text("# 房間尺寸\n寬 3.2m")
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
        code, _, body = self.call(f"/v1/internal/{self.lease.id}/show", {"path": "r.png", "summary": "俯視圖"})
        self.assertEqual(code, 200)
        fid = json.loads(body)["id"]
        ev = [e for e in self.events() if e["type"] == "file"][0]
        out = self.m.render(ev)
        self.assertEqual(minischema.errors(V2["$defs"]["event"], out, root=V2), [])
        self.assertEqual(minischema.errors(V2["$defs"]["file_event"], out, root=V2), [])
        self.assertEqual((out["id"], out["path"], out["summary"]), (fid, "r.png", "俯視圖"))
        code, headers, data = self.call(f"/v1/leases/{self.lease.id}/files/{fid}")
        self.assertEqual((code, data, headers["Content-Type"], headers["X-Content-Type-Options"]),
                         (200, PNG, "image/png", "nosniff"))
        self.assertIn("r.png", headers["Content-Disposition"])

    def test_refused_says_why_and_sends_nothing(self):
        code, _, body = self.call(f"/v1/internal/{self.lease.id}/show", {"path": "secret.txt"})
        self.assertEqual((code, json.loads(body)["error"]), (400, "file_refused"))
        self.assertIn("憑證", json.loads(body)["detail"])
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
        self.assertIn("憑證", why)

    def test_preamble_mentions_show_file(self):
        self.assertIn("show_file", self.m.preamble("zz-none", "discuss"))
        self.assertIn("show_file", self.m.preamble("zz-none", "work"))


class Relay(unittest.TestCase):
    def test_tool_passes_through(self):
        m = load("lease_relay_sf", "lease-relay.py")
        with mock.patch.object(m, "http", return_value=(200, {"id": "a" * 16, "path": "r.png", "mime": "image/png", "size": 9})):
            text, err = m.show_file({"path": "r.png"})
        self.assertFalse(err)
        self.assertIn("r.png", text)
        with mock.patch.object(m, "http", return_value=(400, {"error": "file_refused", "detail": "x.svg 的類型不能送"})):
            text, err = m.show_file({"path": "x.svg"})
        self.assertTrue(err)
        self.assertIn("類型不能送", text)

    def test_tool_is_listed(self):
        m = load("lease_relay_sf2", "lease-relay.py")
        sent = []
        with mock.patch.object(m, "send", sent.append), \
                mock.patch.object(m.sys, "stdin", ['{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}\n']):
            m.main()
        self.assertIn("show_file", [t["name"] for t in sent[0]["result"]["tools"]])


if __name__ == "__main__":
    unittest.main()
