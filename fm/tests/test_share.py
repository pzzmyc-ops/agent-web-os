import base64
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import webauth
from fm.backend import share
from fm.backend.pathutil import IS_WINDOWS, to_fs

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(share.router)
    webauth.install_webauth(app, "secret-pass")
    return app


class ExpiryTests(unittest.TestCase):
    def test_durations(self):
        leap = datetime(2024, 1, 31, 15, 4, 5)
        self.assertEqual(share.expiry_at("7d", leap), int(datetime(2024, 2, 7, 15, 4, 5).timestamp()))
        self.assertEqual(share.expiry_at("1m", leap), int(datetime(2024, 2, 29, 15, 4, 5).timestamp()))
        self.assertEqual(share.expiry_at("3m", leap), int(datetime(2024, 4, 30, 15, 4, 5).timestamp()))
        self.assertEqual(share.expiry_at("12m", leap), int(datetime(2025, 1, 31, 15, 4, 5).timestamp()))
        self.assertIsNone(share.expiry_at("forever", leap))
        december = datetime(2024, 12, 15, 0, 0, 0)
        self.assertEqual(share.expiry_at("1m", december), int(datetime(2025, 1, 15, 0, 0, 0).timestamp()))

    def test_unknown_ttl_errors(self):
        with self.assertRaises(ValueError):
            share.expiry_at("2d", datetime(2024, 1, 1))


class ShareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_store = share.STORE_PATH
        share.STORE_PATH = Path(self.tmp.name) / "state" / "shares.json"
        self.addCleanup(self.restore)
        webauth._tokens.clear()
        self.folder = os.path.join(self.tmp.name, "资料")
        os.makedirs(os.path.join(self.folder, "子目录"))
        self.write(os.path.join(self.folder, "说明.txt"), "你好")
        self.write(os.path.join(self.folder, "子目录", "b.txt"), "inner")
        self.owner = TestClient(make_app())
        self.guest = TestClient(make_app())
        self.owner.__enter__()
        self.guest.__enter__()
        self.addCleanup(self.owner.__exit__, None, None, None)
        self.addCleanup(self.guest.__exit__, None, None, None)

    def restore(self):
        share.STORE_PATH = self.old_store
        webauth._tokens.clear()

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def login(self):
        res = self.owner.post("/api/auth/login", json={"password": "secret-pass"})
        self.assertEqual(res.status_code, 200, res.text)

    def create(self, ttl="7d", path=None):
        self.login()
        res = self.owner.post("/api/share/create", json={"path": to_fs(self.folder if path is None else path), "ttl": ttl})
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body["code"])
        data = body["data"]
        self.assertTrue(data["token"])
        self.assertEqual(data["urlPath"], "/s/" + data["token"])
        return data

    def guest_get(self, token, suffix, params=None, **kwargs):
        return self.guest.get("/s/" + token + suffix, params=params, **kwargs)

    def test_static_shell_is_public_and_files_are_not(self):
        self.assertTrue(webauth.is_public("/js/explorer.js"))
        self.assertTrue(webauth.is_public("/css/app.css"))
        self.assertTrue(webauth.is_public("/assets/kod/style/lib/font-icon/style.css"))
        self.assertTrue(webauth.is_public("/images/win-close.svg"))
        self.assertTrue(webauth.is_public("/images/win-min.svg"))
        self.assertTrue(webauth.is_public("/images/win-max.svg"))
        self.assertFalse(webauth.is_public("/api/list"))
        self.assertFalse(webauth.is_public("/api/delete"))

    def test_name_prefix_is_outside(self):
        self.assertTrue(share.inside_root(self.folder, self.folder))
        self.assertTrue(share.inside_root(self.folder, os.path.join(self.folder, "说明.txt")))
        self.assertFalse(share.inside_root(self.folder, self.folder + "_旁"))

    def test_create_requires_login(self):
        res = self.owner.post("/api/share/create", json={"path": to_fs(self.folder), "ttl": "7d"})
        self.assertEqual(res.status_code, 401)
        self.assertFalse(share.STORE_PATH.exists())
        res = self.guest.post("/api/share/create", json={"path": to_fs(self.folder), "ttl": "7d"})
        self.assertEqual(res.status_code, 401)

    def test_rejects_file_bad_ttl_and_computer_root(self):
        self.login()
        res = self.owner.post("/api/share/create", json={"path": to_fs(os.path.join(self.folder, "说明.txt")), "ttl": "7d"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"], "只能分享文件夹")
        res = self.owner.post("/api/share/create", json={"path": to_fs(os.path.join(self.tmp.name, "没有")), "ttl": "7d"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"], "只能分享文件夹")
        res = self.owner.post("/api/share/create", json={"path": to_fs(self.folder), "ttl": "2d"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"], "过期时间不正确")
        if IS_WINDOWS:
            res = self.owner.post("/api/share/create", json={"path": "/", "ttl": "7d"})
            self.assertEqual(res.status_code, 400)
            self.assertEqual(res.json()["detail"], "不能分享此电脑")

    def test_guest_can_read_only_inside_the_folder(self):
        self.write(os.path.join(self.folder, "a.html"), "<script>alert(1)</script>")
        with open(os.path.join(self.folder, "a.png"), "wb") as handle:
            handle.write(PNG)
        with open(os.path.join(self.folder, "a.bin"), "wb") as handle:
            handle.write(b"\x00\x01")
        self.write(os.path.join(self.folder, ".hidden"), "hide")
        other = os.path.join(self.tmp.name, "资料_旁")
        os.makedirs(other)
        self.write(os.path.join(other, "secret.txt"), "secret")
        data = self.create()
        self.assertEqual(data["name"], "资料")
        self.assertGreater(data["expiresAt"], time.time() + 6 * 86400)
        self.assertLess(data["expiresAt"], time.time() + 8 * 86400)
        saved = share.STORE_PATH.read_text(encoding="utf-8")
        self.assertNotIn(data["token"], saved)
        self.assertEqual(len(json.loads(saved)["shares"][0]["token_hash"]), 64)

        listed = self.guest_get(data["token"], "/api/list")
        self.assertEqual(listed.status_code, 200, listed.text)
        body = listed.json()
        self.assertTrue(body["code"], body)
        page = body["data"]
        root_fs = to_fs(os.path.realpath(self.folder))
        self.assertEqual(page["current"]["name"], "资料")
        self.assertEqual(page["current"]["path"], root_fs)
        self.assertEqual([item["name"] for item in page["folderList"]], ["子目录"])
        files = {item["name"]: item for item in page["fileList"]}
        self.assertEqual(files["说明.txt"]["path"], to_fs(os.path.realpath(os.path.join(self.folder, "说明.txt"))))
        self.assertEqual(files["说明.txt"]["ext"], "txt")
        self.assertEqual(files["a.png"]["ext"], "png")
        self.assertEqual(files["a.html"]["ext"], "html")
        self.assertEqual(files["a.bin"]["ext"], "bin")
        self.assertNotIn(".hidden", listed.text)
        by_abs = self.guest_get(data["token"], "/api/list", params={"path": root_fs})
        self.assertEqual(by_abs.status_code, 200, by_abs.text)
        self.assertEqual(by_abs.json()["data"]["current"]["path"], root_fs)
        self.assertNotIn(self.folder, listed.text)
        self.assertNotIn("secret", listed.text)

        self.write(os.path.join(self.folder, "后来.txt"), "new")
        again = self.guest_get(data["token"], "/api/list")
        self.assertIn("后来.txt", [item["name"] for item in again.json()["data"]["fileList"]])

        nested = self.guest_get(data["token"], "/api/list", params={"path": "子目录"})
        self.assertEqual(nested.json()["data"]["current"]["path"], to_fs(os.path.realpath(os.path.join(self.folder, "子目录"))))
        self.assertEqual(nested.json()["data"]["fileList"][0]["path"], to_fs(os.path.realpath(os.path.join(self.folder, "子目录", "b.txt"))))

        downloaded = self.guest_get(data["token"], "/api/download", params={"path": "说明.txt"})
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, "你好".encode("utf-8"))
        self.assertIn("attachment", downloaded.headers["content-disposition"].lower())
        self.assertEqual(downloaded.headers["x-content-type-options"], "nosniff")

        preview = self.guest_get(data["token"], "/api/preview", params={"path": "说明.txt"})
        self.assertEqual(preview.json()["data"]["content"], "你好")
        html = self.guest_get(data["token"], "/api/preview", params={"path": "a.html"})
        self.assertEqual(html.status_code, 200)
        self.assertIn("application/json", html.headers["content-type"])
        self.assertEqual(html.json()["data"]["content"], "<script>alert(1)</script>")
        image = self.guest_get(data["token"], "/api/preview", params={"path": "a.png"})
        self.assertEqual(image.content, PNG)
        self.assertIn("inline", image.headers["content-disposition"].lower())
        self.assertIn("sandbox", image.headers["content-security-policy"])
        binary = self.guest_get(data["token"], "/api/preview", params={"path": "a.bin"})
        self.assertEqual(binary.status_code, 400)
        self.assertEqual(binary.json()["data"], "这个文件不能预览")
        binary_file = self.guest_get(data["token"], "/api/download", params={"path": "a.bin"})
        self.assertEqual(binary_file.content, b"\x00\x01")
        folder_preview = self.guest_get(data["token"], "/api/preview", params={"path": "子目录"})
        self.assertEqual(folder_preview.status_code, 400)
        file_list = self.guest_get(data["token"], "/api/list", params={"path": "说明.txt"})
        self.assertEqual(file_list.status_code, 400)
        self.assertEqual(file_list.json()["data"], "这不是文件夹")

        hidden = self.guest_get(data["token"], "/api/download", params={"path": ".hidden"})
        self.assertEqual(hidden.status_code, 400)
        self.assertNotIn("hide", hidden.text)
        parent = self.guest_get(data["token"], "/api/download", params={"path": "../资料_旁/secret.txt"})
        self.assertEqual(parent.status_code, 400)
        self.assertNotIn("secret", parent.text)
        self.assertNotIn(self.folder, parent.text)
        sibling = self.guest_get(data["token"], "/api/download", params={"path": "资料_旁/secret.txt"})
        self.assertEqual(sibling.status_code, 404)
        self.assertNotIn("secret", sibling.text)
        absolute = self.guest_get(data["token"], "/api/download", params={"path": to_fs(os.path.join(other, "secret.txt"))})
        self.assertEqual(absolute.status_code, 400)
        self.assertNotIn("secret", absolute.text)

        packed = self.guest_get(data["token"], "/api/download")
        self.assertEqual(packed.status_code, 200, packed.text)
        with zipfile.ZipFile(io.BytesIO(packed.content)) as archive:
            names = archive.namelist()
            self.assertEqual(archive.read("资料/说明.txt"), "你好".encode("utf-8"))
            self.assertIn("资料/子目录/b.txt", names)
        self.assertFalse(any(".hidden" in name or "secret" in name or "shares.json" in name for name in names))
        sub = self.guest_get(data["token"], "/api/download", params={"path": "子目录"})
        with zipfile.ZipFile(io.BytesIO(sub.content)) as archive:
            self.assertEqual(archive.read("子目录/b.txt"), b"inner")

        meta = self.guest_get(data["token"], "/api/meta")
        self.assertEqual(meta.json()["data"]["name"], "资料")
        self.assertEqual(meta.json()["data"]["path"], to_fs(os.path.realpath(self.folder)))
        page = self.guest_get(data["token"], "", headers={"accept": "text/html"}, follow_redirects=False)
        self.assertEqual(page.status_code, 200)
        self.assertIn("/js/explorer.js", page.text)
        self.assertIn("taskbar", page.text)
        self.assertIn("我的文件", page.text)
        policy = page.headers["content-security-policy"]
        self.assertIn("script-src 'self'", policy)
        self.assertIn("style-src 'self'", policy)
        self.assertNotIn(self.folder, page.text)
        self.assertNotIn("location", [key.lower() for key in page.headers if key.lower() == "location"])

    def test_subfolder_cannot_read_parent(self):
        data = self.create("7d", os.path.join(self.folder, "子目录"))
        listed = self.guest_get(data["token"], "/api/list")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual([item["name"] for item in listed.json()["data"]["fileList"]], ["b.txt"])
        self.assertEqual(listed.json()["data"]["folderList"], [])
        parent = self.guest_get(data["token"], "/api/download", params={"path": "../说明.txt"})
        self.assertEqual(parent.status_code, 400)
        self.assertNotIn("你好", parent.text)
        up = self.guest_get(data["token"], "/api/list", params={"path": ".."})
        self.assertEqual(up.status_code, 400)

    def test_links_are_independent_and_revocable(self):
        sub = self.create("7d", os.path.join(self.folder, "子目录"))
        first = self.create("7d")
        second = self.create("forever")
        self.assertIsNone(second["expiresAt"])
        listed = self.owner.get("/api/share/list", params={"path": to_fs(self.folder)})
        self.assertEqual(listed.status_code, 200, listed.text)
        rows = listed.json()["data"]
        self.assertEqual([row["id"] for row in rows], [second["id"], first["id"]])
        self.assertNotIn(first["token"], listed.text)
        self.assertNotIn(second["token"], listed.text)
        self.assertNotIn(sub["id"], [row["id"] for row in rows])
        self.assertEqual(rows[0]["status"], "forever")
        self.assertEqual(rows[1]["status"], "active")
        gone = self.owner.post("/api/share/revoke", json={"id": first["id"]})
        self.assertEqual(gone.status_code, 200)
        self.assertTrue(gone.json()["code"])
        self.assertEqual(self.guest_get(first["token"], "/api/meta").status_code, 404)
        still = self.guest_get(second["token"], "/api/meta")
        self.assertEqual(still.status_code, 200)
        self.assertIsNone(still.json()["data"]["expiresAt"])
        missing = self.owner.post("/api/share/revoke", json={"id": first["id"]})
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()["detail"], "分享不存在")

    def test_expired_and_bad_token_do_not_open(self):
        data = self.create()
        raw = json.loads(share.STORE_PATH.read_text(encoding="utf-8"))
        raw["shares"][0]["expires_at"] = int(time.time()) - 5
        share.STORE_PATH.write_text(json.dumps(raw), encoding="utf-8")
        res = self.guest_get(data["token"], "/api/meta")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["data"], "链接无效或已过期")
        page = self.guest_get(data["token"], "", headers={"accept": "text/html"}, follow_redirects=False)
        self.assertEqual(page.status_code, 404)
        self.assertIn("链接无效或已过期", page.text)
        self.assertNotIn("location", [key.lower() for key in page.headers if key.lower() == "location"])
        bad = self.guest.get("/s/missing-token", headers={"accept": "text/html"}, follow_redirects=False)
        self.assertEqual(bad.status_code, 404)
        self.assertNotEqual(bad.status_code, 303)

    def test_deleted_folder_errors(self):
        data = self.create()
        shutil.rmtree(self.folder)
        res = self.guest_get(data["token"], "/api/list")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["data"], "分享的文件夹不存在")

    def test_share_token_is_not_a_login(self):
        data = self.create()
        self.owner.cookies.clear()
        self.owner.cookies.set(webauth.COOKIE_NAME, data["token"])
        res = self.owner.get("/api/share/list", params={"path": to_fs(self.folder)})
        self.assertEqual(res.status_code, 401)
        self.owner.cookies.clear()
        res = self.owner.get("/api/share/list", params={"path": to_fs(self.folder)})
        self.assertEqual(res.status_code, 401)

    def test_ledger_file_is_not_shared(self):
        share.STORE_PATH = Path(self.folder) / "shares.json"
        data = self.create()
        self.assertTrue(share.STORE_PATH.is_file())
        listed = self.guest_get(data["token"], "/api/list")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertNotIn("shares.json", listed.text)
        hidden = self.guest_get(data["token"], "/api/download", params={"path": "shares.json"})
        self.assertEqual(hidden.status_code, 404)
        self.assertNotIn(data["token"], hidden.text)
        packed = self.guest_get(data["token"], "/api/download")
        with zipfile.ZipFile(io.BytesIO(packed.content)) as archive:
            self.assertFalse(any("shares.json" in name for name in archive.namelist()))

    def test_corrupt_record_errors(self):
        data = self.create()
        share.STORE_PATH.write_text("{", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            self.guest_get(data["token"], "/api/meta")
        raw = {"shares": [{"id": "x", "token_hash": "short", "path": to_fs(self.folder), "ttl": "7d", "created_at": 1, "expires_at": None}]}
        share.STORE_PATH.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.guest_get(data["token"], "/api/meta")

    def test_junction_outside_is_rejected(self):
        if not IS_WINDOWS:
            return
        folder = os.path.join(self.tmp.name, "box")
        outside = os.path.join(self.tmp.name, "box_extra")
        os.makedirs(folder)
        os.makedirs(outside)
        self.write(os.path.join(outside, "secret.txt"), "secret")
        link = os.path.join(folder, "jump")
        subprocess.run(["cmd", "/c", "mklink", "/J", link, outside], check=True)
        data = self.create("7d", folder)
        res = self.guest_get(data["token"], "/api/list")
        self.assertEqual(res.status_code, 400)
        self.assertIn("超出分享范围", res.json()["data"])
        self.assertNotIn("secret", res.text)
