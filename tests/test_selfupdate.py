"""程式自動更新（modules/selfupdate.py、tools/make_release.py、webui 的更新流程）：全部離線測試，不碰網路。"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules import i18n, selfupdate  # noqa: E402
from modules.selfupdate import UpdateError  # noqa: E402
from tools import make_release  # noqa: E402

REPO_DL = "https://github.com/Firefox0304/yt-dlp-Python-UI/releases/download"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def manifest_for(files, version="7.2.0", **extra):
    man = {"format": 1, "name": "yt-dlp-Python-UI", "version": version, "requires_new_exe": False,
           "files": {rel: {"sha256": sha(data), "size": len(data)} for rel, data in files.items()}, "remove": []}
    man.update(extra)
    return man


def build_zip(path, files, manifest=None, extra=None, with_manifest=True):
    """files：{相對路徑: bytes}；extra：{zip 內名稱或 ZipInfo: bytes}，用來塞不該出現的東西。"""
    with zipfile.ZipFile(path, "w") as z:
        if with_manifest:
            z.writestr("manifest.json", json.dumps(manifest or manifest_for(files)))
        for rel, data in files.items():
            z.writestr(rel, data)
        for name, data in (extra or {}).items():
            z.writestr(name, data)


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name

    def path(self, *parts):
        return os.path.join(self.tmp, *parts)


# --------------------------------------------------------------------------- #
class PathTests(unittest.TestCase):
    def test_safe_paths_accepted(self):
        for name in ("modules/updater.py", "web/app.js", "locales/zh-TW/strings.json", "CHANGELOG.md", "README.md",
                     "fonts/NotoSansTC-Bold.ttf", "fonts/OFL.txt", "tests/test_x.py", "tools/make_release.py",
                     "requirements.txt", ".gitignore", "LICENSE"):
            self.assertEqual(selfupdate.normalize_path(name), name)
            self.assertTrue(selfupdate.is_allowed(name), name)

    def test_dangerous_paths_rejected(self):
        for name in ("../evil.py", "modules/../../evil.py", "/etc/passwd", "C:/x.py", "C:\\x.py", "modules\\x.py",
                     "modules//x.py", "modules/./x.py", "modules/x.py.", "modules/x .py", "modules/CON.py",
                     "modules/nul.txt", "modules/com1.py", "modules/日本.py", "", None, 5, "a" * 201, "modules/x\x00.py",
                     "modules/x:stream.py", "modules/ x.py"):
            self.assertIsNone(selfupdate.normalize_path(name), repr(name))

    def test_whitelist(self):
        for rel in ("yt-dlp-Python-UI.exe", "yt-dlp-Python-UI.pyw", "modules/evil.exe", "modules/x.dll", "modules/x.bat",
                    "modules/x.ps1", "fonts/evil.exe", "tests/x.exe", "tools/x.bat", "config/settings.json", "Download/a.mp4",
                    "logs/latest.log", "example/example.txt", "backup/x.py", ".git/config", "yt-dlp.exe", "ffmpeg.exe",
                    "modules/__pycache__/x.py", "Modules/x.py", "modules", "web/x.exe", "icon.ico", "setup.py",
                    "sub/.gitignore", "modules/.gitignore"):
            self.assertFalse(selfupdate.is_allowed(rel), rel)

    def test_checked_path_raises(self):
        for name in ("../x.py", "modules/x.exe", "config/settings.json", "fonts/sub/../x.ttf", "tests/x.bat"):
            with self.assertRaises(UpdateError) as cm:
                selfupdate.checked_path(name)
            self.assertEqual(cm.exception.code, "unsafe_path")


class ManifestTests(unittest.TestCase):
    FILES = {"modules/a.py": b"print(1)\n", "web/app.js": b"x"}

    def parse(self, man, version="7.2.0"):
        return selfupdate.parse_manifest(json.dumps(man).encode("utf-8"), version)

    def test_valid(self):
        res = self.parse(manifest_for(self.FILES, remove=["modules/old.py"], requires_new_exe=True))
        self.assertEqual(sorted(res["files"]), ["modules/a.py", "web/app.js"])
        self.assertEqual(res["remove"], ["modules/old.py"])
        self.assertTrue(res["requires_new_exe"])
        self.assertEqual(res["files"]["web/app.js"], {"sha256": sha(b"x"), "size": 1})

    def assert_bad(self, man, code="bad_manifest", version="7.2.0", raw=None):
        with self.assertRaises(UpdateError) as cm:
            selfupdate.parse_manifest(raw if raw is not None else json.dumps(man).encode("utf-8"), version)
        self.assertEqual(cm.exception.code, code)

    def test_rejections(self):
        good = manifest_for(self.FILES)
        self.assert_bad(None, raw=b"not json")
        self.assert_bad(None, raw=b"\xff\xfe")
        self.assert_bad([1, 2])
        self.assert_bad(dict(good, format=2))
        self.assert_bad(dict(good, name="other"))
        self.assert_bad(dict(good, version="7.2.1"))
        self.assert_bad(dict(good, requires_new_exe="yes"))
        self.assert_bad(dict(good, files={}))
        self.assert_bad(dict(good, files=["modules/a.py"]))
        self.assert_bad(dict(good, remove="modules/a.py"))
        for meta in ({"sha256": "zz" * 32, "size": 1}, {"sha256": "ab" * 32, "size": -1}, {"sha256": "ab" * 32, "size": True},
                     {"sha256": "ab" * 32, "size": selfupdate.MAX_FILE_BYTES + 1}, {"sha256": "ab" * 32}, "text"):
            self.assert_bad(dict(good, files={"modules/a.py": meta}))

    def test_unsafe_paths_rejected(self):
        for bad in ("../evil.py", "modules/evil.exe", "/abs.py"):
            self.assert_bad(dict(manifest_for(self.FILES), files={bad: {"sha256": "ab" * 32, "size": 1}}), code="unsafe_path")
            self.assert_bad(manifest_for(self.FILES, remove=[bad]), code="unsafe_path")

    def test_case_insensitive_duplicates_rejected(self):
        meta = {"sha256": "ab" * 32, "size": 1}
        self.assert_bad(dict(manifest_for(self.FILES), files={"modules/A.py": meta, "modules/a.py": meta}))

    def test_cannot_update_and_remove_same_file(self):
        self.assert_bad(manifest_for(self.FILES, remove=["modules/a.py"]))


# --------------------------------------------------------------------------- #
class ExtractTests(TempCase):
    FILES = {"modules/a.py": b"print('a')\n", "web/index.html": b"<html></html>", "locales/en/strings.json": b"{}"}

    def extract(self, zip_name="u.zip", version="7.2.0"):
        staging = self.path("staging")
        return selfupdate.extract_verified(self.path(zip_name), staging, version), staging

    def assert_rejected(self, code):
        with self.assertRaises(UpdateError) as cm:
            self.extract()
        self.assertEqual(cm.exception.code, code)
        return cm.exception

    def test_valid_zip_extracts_every_file(self):
        build_zip(self.path("u.zip"), self.FILES)
        manifest, staging = self.extract()
        self.assertEqual(sorted(manifest["files"]), sorted(self.FILES))
        for rel, data in self.FILES.items():
            with open(os.path.join(staging, *rel.split("/")), "rb") as f:
                self.assertEqual(f.read(), data)

    def test_zip_slip_and_odd_names_rejected(self):
        for name in ("../evil.py", "modules/../../evil.py", "/abs/evil.py", "modules\\evil.py", "C:/evil.py", "modules/evil.exe"):
            build_zip(self.path("u.zip"), self.FILES, extra={name: b"x"})
            self.assert_rejected("unsafe_path")
            self.assertFalse(os.path.exists(self.path("evil.py")))

    def test_entry_not_in_manifest_rejected(self):
        build_zip(self.path("u.zip"), self.FILES, extra={"modules/extra.py": b"x"})
        self.assertIn("not in manifest", self.assert_rejected("unsafe_path").detail)

    def test_manifest_file_missing_from_zip(self):
        files = dict(self.FILES)
        man = manifest_for(dict(files, **{"modules/ghost.py": b"x"}))
        build_zip(self.path("u.zip"), files, manifest=man)
        self.assertIn("ghost", self.assert_rejected("bad_manifest").detail)

    def test_hash_or_size_mismatch(self):
        man = manifest_for(self.FILES)
        man["files"]["modules/a.py"]["sha256"] = sha(b"other")
        build_zip(self.path("u.zip"), self.FILES, manifest=man)
        self.assertEqual(self.assert_rejected("bad_checksum").detail, "modules/a.py")
        man = manifest_for(self.FILES)
        man["files"]["modules/a.py"]["size"] += 1
        build_zip(self.path("u.zip"), self.FILES, manifest=man)
        self.assert_rejected("bad_checksum")

    def test_missing_manifest(self):
        build_zip(self.path("u.zip"), self.FILES, with_manifest=False)
        self.assert_rejected("bad_manifest")

    def test_wrong_version_in_manifest(self):
        build_zip(self.path("u.zip"), self.FILES, manifest=manifest_for(self.FILES, version="7.1.9"))
        self.assert_rejected("bad_manifest")

    def test_not_a_zip(self):
        with open(self.path("u.zip"), "wb") as f:
            f.write(b"this is not a zip")
        self.assert_rejected("bad_zip")
        with self.assertRaises(UpdateError) as cm:
            selfupdate.extract_verified(self.path("missing.zip"), self.path("s"), "7.2.0")
        self.assertEqual(cm.exception.code, "bad_zip")

    def test_symlink_entry_rejected(self):
        build_zip(self.path("u.zip"), self.FILES)
        link = zipfile.ZipInfo("modules/link.py")
        link.external_attr = 0o120777 << 16
        files = dict(self.FILES, **{"modules/link.py": b"/etc/passwd"})
        with zipfile.ZipFile(self.path("u.zip"), "w") as z:
            z.writestr("manifest.json", json.dumps(manifest_for(files)))
            for rel, data in self.FILES.items():
                z.writestr(rel, data)
            z.writestr(link, b"/etc/passwd")
        self.assertIn("symlink", self.assert_rejected("unsafe_path").detail)

    def test_duplicate_entries_rejected(self):
        with zipfile.ZipFile(self.path("u.zip"), "w") as z:
            z.writestr("manifest.json", json.dumps(manifest_for(self.FILES)))
            for rel, data in self.FILES.items():
                z.writestr(rel, data)
            with self.assertWarns(UserWarning):
                z.writestr("modules/a.py", b"evil")
        self.assert_rejected("bad_zip")

    def test_size_limits(self):
        big = b"0" * 4096
        # 清單謊報大小（宣稱 10 位元組），實際內容超過單檔上限：以實際讀到的位元組為準，擋下（防壓縮炸彈）
        lying = manifest_for({"modules/big.py": big})
        lying["files"]["modules/big.py"]["size"] = 10
        build_zip(self.path("u.zip"), {"modules/big.py": big}, manifest=lying)
        with mock.patch.object(selfupdate, "MAX_FILE_BYTES", 1000):
            with self.assertRaises(UpdateError) as cm:
                self.extract()
        self.assertEqual(cm.exception.code, "too_big")
        # 清單本身宣稱的大小就超過上限：更早在清單檢查就擋下
        build_zip(self.path("u.zip"), {"modules/big.py": big})
        with mock.patch.object(selfupdate, "MAX_FILE_BYTES", 1000):
            with self.assertRaises(UpdateError) as cm:
                self.extract()
        self.assertEqual(cm.exception.code, "bad_manifest")
        files = {f"modules/f{i}.py": big for i in range(3)}
        build_zip(self.path("u.zip"), files)
        with mock.patch.object(selfupdate, "MAX_UNPACKED_BYTES", 9000):
            with self.assertRaises(UpdateError) as cm:
                self.extract()
        self.assertEqual(cm.exception.code, "too_big")

    def test_too_many_entries(self):
        files = {f"modules/f{i}.py": b"x" for i in range(5)}
        build_zip(self.path("u.zip"), files)
        with mock.patch.object(selfupdate, "MAX_FILES", -49):
            self.assert_rejected("bad_zip")

    def test_nothing_written_outside_staging(self):
        build_zip(self.path("u.zip"), self.FILES, extra={"../evil.py": b"x"})
        self.assert_rejected("unsafe_path")
        self.assertEqual(sorted(os.listdir(self.tmp)), ["u.zip"] if not os.path.exists(self.path("staging")) else ["staging", "u.zip"])


# --------------------------------------------------------------------------- #
class UrlTests(unittest.TestCase):
    def test_allowed(self):
        selfupdate.check_download_url(f"{REPO_DL}/v1/a.zip", initial=True)
        selfupdate.check_download_url("https://objects.githubusercontent.com/x")
        selfupdate.check_download_url("https://release-assets.githubusercontent.com/x")
        selfupdate.check_download_url("https://github.com/anything")

    def test_blocked(self):
        for url in ("http://github.com/x", "https://evil.example/x", "https://github.com.evil.example/x",
                    "https://api.github.com/x", "ftp://github.com/x", "https://user:pw@github.com/x", "", None,
                    "file:///etc/passwd", "https://githubusercontent.com/x"):
            with self.assertRaises(UpdateError, msg=repr(url)) as cm:
                selfupdate.check_download_url(url)
            self.assertEqual(cm.exception.code, "network")

    def test_initial_url_must_be_this_projects_release_asset(self):
        for url in ("https://github.com/Other/Repo/releases/download/v1/a.zip",
                    "https://github.com/Firefox0304/yt-dlp-Python-UI/archive/main.zip",
                    "https://objects.githubusercontent.com/a.zip"):
            with self.assertRaises(UpdateError):
                selfupdate.check_download_url(url, initial=True)

    def test_redirects_are_checked(self):
        handler = selfupdate._SafeRedirect()
        req = urllib.request.Request(f"{REPO_DL}/v1/a.zip")
        with self.assertRaises(UpdateError):
            handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example/a.zip")
        with self.assertRaises(UpdateError):
            handler.redirect_request(req, None, 302, "Found", {}, "http://objects.githubusercontent.com/a.zip")
        ok = handler.redirect_request(req, None, 302, "Found", {}, "https://objects.githubusercontent.com/a.zip")
        self.assertEqual(ok.full_url, "https://objects.githubusercontent.com/a.zip")


class FakeNet:
    """假的網路：依網址回傳固定內容；沒登記的網址當成連線失敗。"""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, timeout=15, accept="*/*"):
        self.calls.append(url)
        if url not in self.routes:
            raise urllib.error.URLError("no route")
        value = self.routes[url]
        if isinstance(value, Exception):
            raise value
        return io.BytesIO(value)


def release_json(version="7.2.0", tag=None, size=1000, **extra):
    name = f"yt-dlp-Python-UI-v{version}.zip"
    data = {"tag_name": tag or f"v{version}", "draft": False, "prerelease": False, "body": "更新內容",
            "assets": [{"name": name, "browser_download_url": f"{REPO_DL}/v{version}/{name}", "size": size},
                       {"name": name + ".sha256", "browser_download_url": f"{REPO_DL}/v{version}/{name}.sha256", "size": 90}]}
    data.update(extra)
    return data


class FetchReleaseTests(unittest.TestCase):
    def fetch(self, data):
        raw = data if isinstance(data, (bytes, Exception)) else json.dumps(data).encode("utf-8")
        return selfupdate.fetch_release(FakeNet({selfupdate.RELEASE_API_URL: raw}))

    def test_normal(self):
        info = self.fetch(release_json("7.2.0", tag="UI-v7.2.0"))
        self.assertEqual(info["version"], "7.2.0")
        self.assertEqual(info["zip_name"], "yt-dlp-Python-UI-v7.2.0.zip")
        self.assertTrue(info["zip_url"].startswith(REPO_DL))
        self.assertEqual(info["notes"], "更新內容")

    def assert_code(self, data, code):
        with self.assertRaises(UpdateError) as cm:
            self.fetch(data)
        self.assertEqual(cm.exception.code, code)

    def test_failures(self):
        self.assert_code(release_json(draft=True), "no_release")
        self.assert_code(release_json(prerelease=True), "no_release")
        self.assert_code(release_json(tag="nightly"), "no_release")
        self.assert_code(release_json(tag="v7.2.0-beta"), "no_release")
        self.assert_code(release_json(assets=[]), "no_asset")
        self.assert_code(release_json(assets=release_json()["assets"][:1]), "no_asset")  # 缺校驗檔
        self.assert_code(release_json(size=selfupdate.MAX_ZIP_BYTES + 1), "too_big")
        self.assert_code([1, 2, 3], "no_release")
        self.assert_code(b"<html>", "network")
        self.assert_code(urllib.error.HTTPError("u", 404, "nf", {}, None), "no_release")
        self.assert_code(urllib.error.HTTPError("u", 403, "rate limited", {}, None), "network")
        self.assert_code(urllib.error.URLError("offline"), "network")

    def test_asset_url_must_be_the_projects_release_download(self):
        data = release_json()
        data["assets"][0]["browser_download_url"] = "https://evil.example/a.zip"
        self.assert_code(data, "network")
        data = release_json()
        data["assets"][1]["browser_download_url"] = "https://github.com/Other/Repo/releases/download/v1/a.sha256"
        self.assert_code(data, "network")

    def test_huge_api_response_rejected(self):
        self.assert_code(b"{" + b" " * (selfupdate.MAX_API_BYTES + 10), "too_big")

    def test_notes_are_truncated(self):
        self.assertEqual(len(self.fetch(release_json(body="x" * 5000))["notes"]), selfupdate.NOTES_LIMIT)

    def test_asset_names_depend_only_on_the_parsed_version(self):
        data = release_json("7.2.0")
        data["tag_name"] = "UI-v7.2.0"
        self.assertEqual(self.fetch(data)["zip_name"], "yt-dlp-Python-UI-v7.2.0.zip")


class DownloadTests(TempCase):
    def test_download_with_progress(self):
        payload = os.urandom(200000)
        seen = []
        done = selfupdate.download_file("u", self.path("f"), 10**6, lambda d, t: seen.append(d),
                                        FakeNet({"u": payload}))
        self.assertEqual(done, len(payload))
        with open(self.path("f"), "rb") as f:
            self.assertEqual(f.read(), payload)
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(seen[-1], len(payload))

    def test_over_limit_aborts(self):
        with self.assertRaises(UpdateError) as cm:
            selfupdate.download_file("u", self.path("f"), 1000, None, FakeNet({"u": b"x" * 5000}))
        self.assertEqual(cm.exception.code, "too_big")

    def test_declared_length_over_limit_aborts_early(self):
        class Resp(io.BytesIO):
            headers = {"Content-Length": "999999"}
        with self.assertRaises(UpdateError) as cm:
            selfupdate.download_file("u", self.path("f"), 1000, None, lambda url: Resp(b"x"))
        self.assertEqual(cm.exception.code, "too_big")

    def test_network_error(self):
        with self.assertRaises(UpdateError) as cm:
            selfupdate.download_file("u", self.path("f"), 1000, None, FakeNet({}))
        self.assertEqual(cm.exception.code, "network")

    def test_sha_file_formats(self):
        digest = sha(b"x")
        for text in (digest, f"{digest}  a.zip", f"{digest.upper()} *a.zip\n", f"\n{digest}  a.zip\nother"):
            self.assertEqual(selfupdate.parse_sha256_file(text, "a.zip"), digest)
        for text in ("", "nothing", f"{digest}  other.zip", "abc  a.zip"):
            with self.assertRaises(UpdateError) as cm:
                selfupdate.parse_sha256_file(text, "a.zip")
            self.assertEqual(cm.exception.code, "bad_checksum")


class PrepareTests(TempCase):
    FILES = {"modules/a.py": b"print('new')\n", "web/index.html": b"<html>new</html>"}

    def serve(self, files=None, manifest=None, sha_text=None, version="7.2.0", extra=None, **release_extra):
        files = self.FILES if files is None else files
        zip_path = self.path("served.zip")
        build_zip(zip_path, files, manifest=manifest, extra=extra)
        with open(zip_path, "rb") as f:
            data = f.read()
        name = f"yt-dlp-Python-UI-v{version}.zip"
        routes = {selfupdate.RELEASE_API_URL: json.dumps(release_json(version, size=len(data), **release_extra)).encode(),
                  f"{REPO_DL}/v{version}/{name}": data,
                  f"{REPO_DL}/v{version}/{name}.sha256": (sha_text if sha_text is not None else f"{sha(data)}  {name}\n").encode()}
        return FakeNet(routes)

    def prepare(self, net, current="7.1.2", progress=None):
        work = self.path("work")
        return selfupdate.prepare_update(current, on_progress=progress, open_url=net, work_dir=work), work

    def test_full_flow(self):
        stages = []
        result, work = self.prepare(self.serve(), progress=lambda s, p: stages.append(s))
        self.assertEqual(result["version"], "7.2.0")
        self.assertEqual(result["notes"], "更新內容")
        self.assertEqual(sorted(result["manifest"]["files"]), sorted(self.FILES))
        for rel, data in self.FILES.items():
            with open(os.path.join(result["staging"], *rel.split("/")), "rb") as f:
                self.assertEqual(f.read(), data)
        self.assertEqual([s for i, s in enumerate(stages) if i == 0 or s != stages[i - 1]], ["checking", "downloading", "verifying"])
        self.assertTrue(os.path.isdir(work))
        selfupdate.cleanup(work)
        self.assertFalse(os.path.exists(work))

    def assert_fails(self, net, code, current="7.1.2"):
        with self.assertRaises(UpdateError) as cm:
            self.prepare(net, current)
        self.assertEqual(cm.exception.code, code)
        self.assertFalse(os.path.exists(self.path("work")), "失敗時要清掉暫存資料夾")

    def test_not_newer(self):
        self.assert_fails(self.serve(), "not_newer", current="7.2.0")
        self.assert_fails(self.serve(), "not_newer", current="7.3.0")
        self.assert_fails(self.serve(), "not_newer", current="7.2")  # 補零後相同

    def test_checksum_mismatch(self):
        self.assert_fails(self.serve(sha_text=f"{'0' * 64}  yt-dlp-Python-UI-v7.2.0.zip"), "bad_checksum")
        self.assert_fails(self.serve(sha_text="garbage"), "bad_checksum")

    def test_requires_new_exe(self):
        net = self.serve(manifest=manifest_for(self.FILES, requires_new_exe=True))
        with self.assertRaises(UpdateError) as cm:
            self.prepare(net)
        self.assertEqual((cm.exception.code, cm.exception.detail), ("requires_exe", "7.2.0"))
        self.assertFalse(os.path.exists(self.path("work")))

    def test_malicious_zip_rejected(self):
        self.assert_fails(self.serve(extra={"../evil.py": b"x"}), "unsafe_path")
        self.assert_fails(self.serve(extra={"modules/evil.exe": b"x"}), "unsafe_path")

    def test_network_failure_midway(self):
        net = self.serve()
        net.routes.pop(f"{REPO_DL}/v7.2.0/yt-dlp-Python-UI-v7.2.0.zip")
        self.assert_fails(net, "network")

    def test_tampered_zip_after_checksum_was_published(self):
        net = self.serve()
        zip_url = f"{REPO_DL}/v7.2.0/yt-dlp-Python-UI-v7.2.0.zip"
        net.routes[zip_url] = net.routes[zip_url][:-30] + b"x" * 30
        self.assert_fails(net, "bad_checksum")


# --------------------------------------------------------------------------- #
class ApplyTests(TempCase):
    OLD = {"modules/a.py": b"old a", "modules/b.py": b"old b", "web/app.js": b"old js", "modules/gone.py": b"remove me"}
    NEW = {"modules/a.py": b"new a", "modules/b.py": b"new b", "web/app.js": b"new js", "modules/added.py": b"added"}

    def setUp(self):
        super().setUp()
        self.base = self.path("app")
        self.staging = self.path("staging")
        for rel, data in self.OLD.items():
            self.write(self.base, rel, data)
        # 使用者資料：更新完全不能碰
        self.write(self.base, "config/settings.json", b'{"keep": true}')
        self.write(self.base, "Download/video.mp4", b"video")
        self.write(self.base, "logs/latest.log", b"log")
        for rel, data in self.NEW.items():
            self.write(self.staging, rel, data)
        self.manifest = {"version": "7.2.0", "files": {rel: {"sha256": sha(d), "size": len(d)} for rel, d in self.NEW.items()},
                         "remove": ["modules/gone.py"], "requires_new_exe": False}

    @staticmethod
    def write(root, rel, data):
        target = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(data)

    def read(self, rel):
        with open(os.path.join(self.base, *rel.split("/")), "rb") as f:
            return f.read()

    def tree(self):
        found = {}
        for folder, _dirs, names in os.walk(self.base):
            if "backup" in folder.split(os.sep):
                continue
            for name in names:
                with open(os.path.join(folder, name), "rb") as f:
                    found[os.path.relpath(os.path.join(folder, name), self.base).replace(os.sep, "/")] = f.read()
        return found

    def apply(self, **kw):
        return selfupdate.apply_update(self.base, self.staging, self.manifest, "7.1.2", **kw)

    def test_success(self):
        result = self.apply()
        for rel, data in self.NEW.items():
            self.assertEqual(self.read(rel), data)
        self.assertFalse(os.path.exists(os.path.join(self.base, "modules", "gone.py")))
        self.assertEqual((result["replaced"], result["removed"]), (4, 1))
        self.assertEqual(self.read("config/settings.json"), b'{"keep": true}')
        self.assertEqual(self.read("Download/video.mp4"), b"video")
        self.assertEqual(self.read("logs/latest.log"), b"log")
        self.assertFalse([n for n in self.tree() if n.endswith(".new")], "不能留下暫存檔")

    def test_backup_holds_the_old_files(self):
        result = self.apply()
        self.assertRegex(os.path.basename(result["backup"]), r"^v7\.1\.2-\d{8}-\d{6}$")
        for rel in ("modules/a.py", "modules/b.py", "web/app.js", "modules/gone.py"):
            with open(os.path.join(result["backup"], *rel.split("/")), "rb") as f:
                self.assertEqual(f.read(), self.OLD[rel])
        self.assertFalse(os.path.exists(os.path.join(result["backup"], "modules", "added.py")))  # 原本不存在的不備份

    def test_identical_files_are_skipped_not_rewritten_or_backed_up(self):
        self.write(self.base, "fonts/big.ttf", b"font bytes")
        self.write(self.staging, "fonts/big.ttf", b"font bytes")
        self.manifest["files"]["fonts/big.ttf"] = {"sha256": sha(b"font bytes"), "size": 10}
        font = os.path.join(self.base, "fonts", "big.ttf")
        os.utime(font, (1_000_000_000, 1_000_000_000))
        result = self.apply()
        self.assertEqual((result["replaced"], result["removed"], result["unchanged"]), (4, 1, 1))
        self.assertEqual(os.stat(font).st_mtime, 1_000_000_000)  # 沒有被重寫
        self.assertFalse(os.path.exists(os.path.join(result["backup"], "fonts")))  # 沒有備份

    def test_identical_files_stay_untouched_when_update_fails(self):
        self.write(self.base, "fonts/big.ttf", b"font bytes")
        self.write(self.staging, "fonts/big.ttf", b"font bytes")
        self.manifest["files"]["fonts/big.ttf"] = {"sha256": sha(b"font bytes"), "size": 10}
        before = self.tree()
        with self.assertRaises(UpdateError):
            self.apply(replace=lambda src, dst: (_ for _ in ()).throw(OSError("x")))
        self.assertEqual(self.tree(), before)

    def test_nothing_to_do_creates_no_backup(self):
        for rel, data in self.NEW.items():
            self.write(self.base, rel, data)  # 本機已經是新版
        os.remove(os.path.join(self.base, "modules", "gone.py"))
        result = self.apply()
        self.assertEqual(result, {"backup": "", "replaced": 0, "removed": 0, "unchanged": 4})
        self.assertFalse(os.path.exists(os.path.join(self.base, "backup")))

    def test_failure_midway_restores_everything(self):
        before = self.tree()
        calls = []

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) == 3:
                raise OSError("disk full")
            os.replace(src, dst)

        with self.assertRaises(UpdateError) as cm:
            self.apply(replace=flaky)
        self.assertEqual(cm.exception.code, "apply_failed")
        self.assertIn("disk full", cm.exception.detail)
        self.assertEqual(self.tree(), before)  # 舊檔回來、新增的檔案被移除、removed 的檔案還在、沒有 .new 殘留

    def test_failure_during_removal_restores_everything(self):
        before = self.tree()
        real_remove = os.remove

        def deny(path):
            if path.endswith("gone.py"):
                raise PermissionError("locked")
            real_remove(path)

        with mock.patch("modules.selfupdate.os.remove", deny), self.assertRaises(UpdateError) as cm:
            self.apply()
        self.assertEqual(cm.exception.code, "apply_failed")
        self.assertEqual(self.tree(), before)

    def test_rollback_failure_reports_it(self):
        def boom(src, dst):
            raise OSError("nope")

        with mock.patch.object(selfupdate, "_rollback", side_effect=OSError("restore broke")), \
                self.assertRaises(UpdateError) as cm:
            self.apply(replace=boom)
        self.assertEqual(cm.exception.code, "rollback_failed")
        self.assertIn("restore broke", cm.exception.detail)

    def test_unsafe_manifest_touches_nothing(self):
        before = self.tree()
        for bad in ("../evil.py", "modules/evil.exe", "/abs.py", "config/settings.json"):
            self.manifest["files"][bad] = {"sha256": sha(b"x"), "size": 1}
            with self.assertRaises(UpdateError) as cm:
                self.apply()
            self.assertEqual(cm.exception.code, "unsafe_path", bad)
            del self.manifest["files"][bad]
        self.assertEqual(self.tree(), before)
        self.assertFalse(os.path.exists(os.path.join(self.base, "backup")), "驗證沒過就不該建立備份")
        self.assertFalse(os.path.exists(self.path("evil.py")))

    def test_symlinked_folder_cannot_escape(self):
        outside = self.path("outside")
        os.makedirs(outside)
        web_dir = os.path.join(self.base, "web")
        shutil.rmtree(web_dir)
        try:
            os.symlink(outside, web_dir, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("這個環境不能建立符號連結")
        with self.assertRaises(UpdateError) as cm:
            self.apply()
        self.assertEqual(cm.exception.code, "unsafe_path")
        self.assertEqual(os.listdir(outside), [])

    def test_new_and_removed_files_and_partial_dirs(self):
        self.manifest["files"] = {"locales/ja/new.json": {"sha256": sha(b"{}"), "size": 2}}
        self.manifest["remove"] = []
        self.write(self.staging, "locales/ja/new.json", b"{}")
        self.apply()
        self.assertEqual(self.read("locales/ja/new.json"), b"{}")
        self.assertEqual(self.read("modules/a.py"), b"old a")  # 沒列在清單裡的不動

    def test_prune_keeps_two_newest(self):
        names = ["v7.0.0-20260101-000000", "v7.0.1-20260102-000000", "v7.1.0-20260103-000000", "v7.1.1-20260104-000000"]
        for name in names:
            os.makedirs(os.path.join(self.base, "backup", name))
        os.makedirs(os.path.join(self.base, "backup", "not-a-backup"))
        selfupdate.prune_backups(self.base)
        left = sorted(os.listdir(os.path.join(self.base, "backup")))
        self.assertEqual(left, ["not-a-backup", names[2], names[3]])

    def test_apply_prunes_old_backups(self):
        for name in ("v7.0.0-20200101-000000", "v7.0.1-20200102-000000", "v7.1.0-20200103-000000"):
            os.makedirs(os.path.join(self.base, "backup", name))
        result = self.apply()
        left = [os.path.join(self.base, "backup", n) for n in os.listdir(os.path.join(self.base, "backup"))]
        self.assertEqual(len(left), 2)
        self.assertIn(result["backup"], left)

    def test_restore_backup(self):
        result = self.apply()
        self.assertEqual(self.read("modules/a.py"), b"new a")
        count = selfupdate.restore_backup(self.base)
        self.assertEqual(count, 4)
        for rel in ("modules/a.py", "modules/b.py", "web/app.js", "modules/gone.py"):
            self.assertEqual(self.read(rel), self.OLD[rel])
        self.assertTrue(result["backup"])
        self.assertEqual(selfupdate.restore_backup(self.path("nowhere")), 0)

    def test_paths_with_unicode_or_spaces_in_base_dir(self):
        base = self.path("新資料夾 with space")
        self.write(base, "modules/a.py", b"old")
        self.write(self.staging, "modules/a.py", b"new")
        manifest = {"version": "7.2.0", "files": {"modules/a.py": {"sha256": sha(b"new"), "size": 3}}, "remove": [],
                    "requires_new_exe": False}
        selfupdate.apply_update(base, self.staging, manifest, "7.1.2")
        with open(os.path.join(base, "modules", "a.py"), "rb") as f:
            self.assertEqual(f.read(), b"new")


# --------------------------------------------------------------------------- #
class ReleaseToolTests(TempCase):
    INCLUDED = ["modules/version.py", "modules/a.py", "web/index.html", "locales/en/strings.json", "fonts/a.ttf", "fonts/OFL.txt",
                "tests/test_a.py", "tools/t.py", "README.md", "CHANGELOG.md", "requirements.txt", ".gitignore"]
    # 這些都不能被打包：使用者資料、入口、建置產物、版本控制
    EXCLUDED = ["modules/__pycache__/a.cpython-312.pyc", "config/settings.json", "Download/v.mp4", "example/e.txt",
                "logs/l.log", ".git/config", "backup/v7.0.0-20200101-000000/modules/a.py", "dist/old.zip", "build/x/y.py",
                "yt-dlp-Python-UI.exe", "yt-dlp-Python-UI.pyw", "yt-dlp.exe", "ffmpeg.exe", "Python Download/x.txt",
                ".vscode/settings.json", "tests/__pycache__/t.pyc", "tools/leftover.pyc",
                "manifest.json", "yt-dlp-Python-UI-v9.9.8.zip", "yt-dlp-Python-UI-v9.9.8.zip.sha256", "old-backup.zip"]

    def make_project(self, version="9.9.9", changelog=None, extra=()):
        proj = self.path("proj")
        files = {rel: f"content of {rel}\n" for rel in self.INCLUDED + self.EXCLUDED + list(extra)}
        files["modules/version.py"] = f'APP_NAME = "x"\nAPP_VERSION = "{version}"\n'
        files["CHANGELOG.md"] = changelog if changelog is not None else f"# v{version}\n\n- change\n"
        for rel, text in files.items():
            target = os.path.join(proj, *rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(text)
        return proj

    def build(self, proj=None, **kw):
        return make_release.build_release(proj or self.make_project(), self.path("dist"), **kw)

    def test_packages_everything_except_user_data_and_entry(self):
        zip_path, sha_path, manifest = self.build()
        self.assertEqual(os.path.basename(zip_path), "yt-dlp-Python-UI-v9.9.9.zip")
        with zipfile.ZipFile(zip_path) as z:
            names = set(z.namelist())
            stored = json.loads(z.read("manifest.json"))
        self.assertEqual(names, set(self.INCLUDED) | {"manifest.json"})
        self.assertEqual(stored, manifest)
        self.assertEqual(manifest["version"], "9.9.9")
        self.assertFalse(manifest["requires_new_exe"])
        with open(sha_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), f"{selfupdate.sha256_file(zip_path)}  yt-dlp-Python-UI-v9.9.9.zip\n")

    def test_fonts_are_included(self):
        _zip, _sha, manifest = self.build()
        self.assertIn("fonts/a.ttf", manifest["files"])
        self.assertIn("fonts/OFL.txt", manifest["files"])

    def test_reproducible(self):
        proj = self.make_project()
        first = make_release.build_release(proj, self.path("d1"))[0]
        second = make_release.build_release(proj, self.path("d2"))[0]
        self.assertEqual(selfupdate.sha256_file(first), selfupdate.sha256_file(second))

    def test_output_is_accepted_by_the_client(self):
        zip_path, _sha, _m = self.build(remove=["modules/old.py"], requires_new_exe=True)
        manifest = selfupdate.extract_verified(zip_path, self.path("staging"), "9.9.9")
        self.assertEqual(manifest["remove"], ["modules/old.py"])
        self.assertTrue(manifest["requires_new_exe"])

    def test_unpackable_files_fail_loudly_instead_of_being_dropped(self):
        proj = self.make_project(extra=["modules/notes.bak", "icon.ico", "modules/evil.exe", "fonts/readme.docx"])
        with self.assertRaises(make_release.ReleaseError) as cm:
            self.build(proj)
        message = str(cm.exception)
        for rel in ("modules/notes.bak", "icon.ico", "modules/evil.exe", "fonts/readme.docx"):
            self.assertIn(rel, message)
        self.assertIn("--exclude", message)
        self.assertNotIn("modules/a.py", message)

    def test_exclude_option_resolves_it(self):
        proj = self.make_project(extra=["modules/notes.bak", "icon.ico", "docs/guide.md"])
        _zip, _sha, manifest = self.build(proj, exclude=["icon.ico", "modules/notes.bak", "docs/"])
        self.assertNotIn("icon.ico", manifest["files"])
        self.assertNotIn("docs/guide.md", manifest["files"])
        self.assertIn("modules/a.py", manifest["files"])

    def test_exclude_can_drop_a_whole_folder_such_as_tests(self):
        _zip, _sha, manifest = self.build(exclude=["tests", "fonts/"])
        self.assertFalse([f for f in manifest["files"] if f.startswith(("tests/", "fonts/"))])
        self.assertIn("tools/t.py", manifest["files"])

    def test_leftover_manifest_from_an_extracted_update_is_ignored(self):
        # 把更新包解壓到專案資料夾會在根目錄留下 manifest.json：不能擋住打包，也不能混進新的更新包
        _zip, _sha, manifest = self.build()
        self.assertNotIn("manifest.json", manifest["files"])
        with zipfile.ZipFile(_zip) as z:
            self.assertEqual(z.namelist().count("manifest.json"), 1)  # 只有更新包自己的那一份
            self.assertEqual(json.loads(z.read("manifest.json"))["version"], "9.9.9")

    def test_exclusions_only_apply_at_the_project_root(self):
        # 只有根目錄的 config/、logs/、.exe 才會被排除；別的資料夾裡同名的東西不能被默默吃掉
        proj = self.make_project(extra=["modules/logs/x.py", "modules/tool.exe", "modules/manifest.json", "web/old.zip"])
        with self.assertRaises(make_release.ReleaseError) as cm:
            self.build(proj)
        self.assertIn("modules/tool.exe", str(cm.exception))
        self.assertIn("web/old.zip", str(cm.exception))
        self.assertNotIn("modules/manifest.json", str(cm.exception))  # 非根目錄的 manifest.json 是一般的 .json 檔，可以打包
        _zip, _sha, manifest = self.build(proj, exclude=["modules/tool.exe", "web/old.zip"])
        self.assertIn("modules/logs/x.py", manifest["files"])
        self.assertIn("modules/manifest.json", manifest["files"])

    def test_changelog_must_match_version(self):
        with self.assertRaises(make_release.ReleaseError):
            self.build(self.make_project(changelog="# v9.9.8\n"))

    def test_bad_remove_arguments(self):
        proj = self.make_project()
        with self.assertRaises(UpdateError):
            self.build(proj, remove=["../x.py"])
        with self.assertRaises(make_release.ReleaseError):
            self.build(proj, remove=["modules/a.py"])

    def test_missing_version(self):
        proj = self.make_project()
        with open(os.path.join(proj, "modules", "version.py"), "w", encoding="utf-8") as f:
            f.write("nothing\n")
        with self.assertRaises(make_release.ReleaseError):
            self.build(proj)

    def test_real_project_builds_and_is_accepted(self):
        from modules.version import APP_VERSION
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(make_release.main(["--out", self.path("real")]), 0)
        zip_path = self.path("real", f"yt-dlp-Python-UI-v{APP_VERSION}.zip")
        manifest = selfupdate.extract_verified(zip_path, self.path("staging"), APP_VERSION)
        files = set(manifest["files"])
        for rel in ("modules/selfupdate.py", "tools/make_release.py", "tests/test_selfupdate.py", "web/app.js", "README.md",
                    "CHANGELOG.md", "requirements.txt", ".gitignore", "fonts/NotoSansTC-Regular.ttf", "fonts/NotoSansTC-Bold.ttf"):
            self.assertIn(rel, files)
        self.assertFalse([f for f in files if f.startswith(("config/", "Download/", "example/", "logs/", ".git/", "backup/", "dist/"))])
        self.assertFalse([f for f in files if f.endswith((".exe", ".pyw", ".pyc"))])
        self.assertFalse([f for f in files if "__pycache__" in f])
        self.assertLess(os.path.getsize(zip_path), selfupdate.MAX_ZIP_BYTES)


class EndToEndTests(TempCase):
    """開發者端打包 → 使用者端下載、驗證、套用到另一份安裝：整條路走一遍。"""
    INCLUDED, EXCLUDED = ReleaseToolTests.INCLUDED, ReleaseToolTests.EXCLUDED  # 借用 ReleaseToolTests.make_project

    def test_whole_update_of_an_installed_copy(self):
        dev = ReleaseToolTests.make_project(self, version="9.9.9")
        installed = self.path("installed")
        for rel, text in {"modules/version.py": 'APP_NAME = "x"\nAPP_VERSION = "9.9.8"\n', "modules/a.py": "print('old')\n",
                          "modules/removed.py": "old", "web/index.html": "<html>old", "CHANGELOG.md": "# v9.9.8\n",
                          "config/settings.json": '{"mine": 1}', "Download/a.mp4": "video", "yt-dlp-Python-UI.exe": "MZ-old"}.items():
            target = os.path.join(installed, *rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(text)
        zip_path, sha_path, _manifest = make_release.build_release(dev, self.path("dist"), remove=["modules/removed.py"])
        with open(zip_path, "rb") as f:
            zip_bytes = f.read()
        with open(sha_path, "rb") as f:
            sha_bytes = f.read()
        name = os.path.basename(zip_path)
        net = FakeNet({selfupdate.RELEASE_API_URL: json.dumps(release_json("9.9.9", size=len(zip_bytes))).encode(),
                       f"{REPO_DL}/v9.9.9/{name}": zip_bytes, f"{REPO_DL}/v9.9.9/{name}.sha256": sha_bytes})
        pending = selfupdate.prepare_update("9.9.8", open_url=net)
        try:
            result = selfupdate.apply_update(installed, pending["staging"], pending["manifest"], "9.9.8")
        finally:
            selfupdate.cleanup(pending["work_dir"])

        for rel in ("modules/version.py", "modules/a.py", "web/index.html", "CHANGELOG.md", "README.md", "locales/en/strings.json"):
            with open(os.path.join(dev, *rel.split("/")), "rb") as a, open(os.path.join(installed, *rel.split("/")), "rb") as b:
                self.assertEqual(a.read(), b.read(), rel)
        self.assertFalse(os.path.exists(os.path.join(installed, "modules", "removed.py")))
        with open(os.path.join(installed, "config", "settings.json"), encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"mine": 1}')
        with open(os.path.join(installed, "yt-dlp-Python-UI.exe"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "MZ-old")  # exe 不在更新範圍
        self.assertTrue(os.path.exists(os.path.join(installed, "Download", "a.mp4")))
        self.assertEqual(selfupdate.restore_backup(installed), 5)  # 備份可以完整還原（被動到的 5 個舊檔）
        with open(os.path.join(installed, "modules", "removed.py"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "old")
        self.assertTrue(result["backup"])


# --------------------------------------------------------------------------- #
class ApiSelfUpdateTests(TempCase):
    def setUp(self):
        super().setUp()
        from modules import webui
        i18n.init(ROOT)
        i18n.set_language("zh-TW")
        self.webui = webui
        self.api = webui.Api(self.tmp, {}, self.path("settings.json"))
        self.events = []
        patcher = mock.patch.object(webui.Api, "_emit", lambda api, **e: self.events.append(e))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.staging_dir = self.path("work")
        os.makedirs(self.staging_dir)
        self.pending = {"version": "7.2.0", "notes": "n", "staging": self.path("work", "staging"), "work_dir": self.staging_dir,
                        "manifest": {"files": {}, "remove": [], "version": "7.2.0", "requires_new_exe": False}}

    def types(self):
        return [e["type"] for e in self.events]

    def test_prepare_success_stores_pending_and_asks_to_confirm(self):
        with mock.patch.object(selfupdate, "prepare_update", return_value=self.pending):
            self.api._updating = True
            self.api._self_update_prepare()
        self.assertEqual(self.types(), ["selfupdate_ready"])
        ready = self.events[0]
        self.assertEqual((ready["latest"], ready["notes"]), ("7.2.0", "n"))
        self.assertIs(self.api._pending_update, self.pending)
        self.assertFalse(self.api._updating)

    def test_prepare_progress_is_forwarded(self):
        def fake(current, on_progress=None):
            on_progress("downloading", 50)
            return self.pending
        with mock.patch.object(selfupdate, "prepare_update", side_effect=fake):
            self.api._self_update_prepare()
        self.assertEqual(self.events[0], {"type": "selfupdate_progress", "stage": "downloading", "pct": 50})

    def test_prepare_error_shows_message_and_state(self):
        with mock.patch.object(selfupdate, "prepare_update", side_effect=UpdateError("bad_checksum", "zip")):
            self.api._updating = True
            self.api._self_update_prepare()
        event = self.events[-1]
        self.assertEqual(event["type"], "selfupdate_fail")
        self.assertIn("校驗不符", event["message"])
        self.assertIn("沒有被改動", event["message"])
        self.assertFalse(self.api._updating)
        self.assertIsNone(self.api._pending_update)

    def test_requires_exe_goes_to_manual(self):
        with mock.patch.object(selfupdate, "prepare_update", side_effect=UpdateError("requires_exe", "7.2.0")):
            self.api._self_update_prepare()
        self.assertEqual(self.events[-1], {"type": "selfupdate_manual", "latest": "7.2.0"})

    def test_unexpected_exception_is_reported(self):
        with mock.patch.object(selfupdate, "prepare_update", side_effect=RuntimeError("boom")):
            self.api._self_update_prepare()
        self.assertIn("boom", self.events[-1]["message"])

    def test_start_refused_while_downloading_or_installing_or_updating(self):
        self.api._controller = object()
        self.assertFalse(self.api.start_self_update()["ok"])
        self.api._controller = None
        self.api._installing = True
        self.assertFalse(self.api.start_self_update()["ok"])
        self.api._installing = False
        self.api._updating = True
        self.assertFalse(self.api.start_self_update()["ok"])
        self.assertFalse(self.api.confirm_self_update()["ok"])

    def test_start_launches_worker_and_discards_old_pending(self):
        self.api._pending_update = self.pending
        with mock.patch("modules.webui.threading.Thread") as thread:
            res = self.api.start_self_update()
        self.assertTrue(res["ok"])
        thread.return_value.start.assert_called_once()
        self.assertTrue(self.api._updating)
        self.assertIsNone(self.api._pending_update)
        self.assertFalse(os.path.exists(self.staging_dir))

    def test_confirm_needs_pending(self):
        res = self.api.confirm_self_update()
        self.assertFalse(res["ok"])
        self.assertIn("沒有已準備好的更新", res["error"])

    def test_confirm_refused_while_download_running(self):
        self.api._pending_update = self.pending
        self.api._controller = object()
        self.assertFalse(self.api.confirm_self_update()["ok"])
        self.assertIs(self.api._pending_update, self.pending)

    def test_apply_success_restarts_and_closes(self):
        self.api._pending_update = self.pending
        result = {"backup": "b", "replaced": 1, "removed": 0}
        with mock.patch.object(selfupdate, "apply_update", return_value=result) as apply, \
                mock.patch("modules.webui.check.restart") as restart, \
                mock.patch.object(self.webui.Api, "win_close") as close, \
                mock.patch("modules.webui.time.sleep"):
            self.api._updating = True
            self.api._self_update_apply(self.pending)
        apply.assert_called_once_with(self.tmp, self.pending["staging"], self.pending["manifest"], self.webui.APP_VERSION)
        self.assertEqual(self.types(), ["selfupdate_progress", "selfupdate_done"])
        restart.assert_called_once_with(self.tmp)
        close.assert_called_once_with(animate=False)
        self.assertIsNone(self.api._pending_update)
        self.assertFalse(os.path.exists(self.staging_dir), "套用後要清掉暫存")

    def test_apply_failure_reports_restored_state(self):
        self.api._pending_update = self.pending
        with mock.patch.object(selfupdate, "apply_update", side_effect=UpdateError("apply_failed", "disk full")), \
                mock.patch("modules.webui.check.restart") as restart:
            self.api._updating = True
            self.api._self_update_apply(self.pending)
        event = self.events[-1]
        self.assertEqual(event["type"], "selfupdate_fail")
        self.assertIn("disk full", event["message"])
        self.assertIn("已還原", event["message"])
        restart.assert_not_called()
        self.assertFalse(self.api._updating)
        self.assertIsNone(self.api._pending_update)

    def test_rollback_failure_points_to_backup_folder(self):
        with mock.patch.object(selfupdate, "apply_update", side_effect=UpdateError("rollback_failed", "x")):
            self.api._self_update_apply(self.pending)
        self.assertIn("backup", self.events[-1]["message"])

    def test_restart_failure_says_update_is_applied(self):
        with mock.patch.object(selfupdate, "apply_update", return_value={}), \
                mock.patch("modules.webui.check.restart", side_effect=OSError("no exe")), \
                mock.patch("modules.webui.time.sleep"), mock.patch.object(self.webui.Api, "win_close") as close:
            self.api._self_update_apply(self.pending)
        self.assertIn("手動重新開啟", self.events[-1]["message"])
        close.assert_not_called()

    def test_cancel_cleans_up(self):
        self.api._pending_update = self.pending
        self.assertTrue(self.api.cancel_self_update())
        self.assertIsNone(self.api._pending_update)
        self.assertFalse(os.path.exists(self.staging_dir))
        self.assertTrue(self.api.cancel_self_update())  # 沒有東西時也不會出錯

    def test_closing_cleans_up_unless_applying(self):
        with mock.patch("modules.webui.downloader.terminate_all_downloads"):
            self.api._pending_update = self.pending
            self.api._updating = True
            self.api.on_closing()
            self.assertIs(self.api._pending_update, self.pending)
            self.api._updating = False
            self.api.on_closing()
            self.assertIsNone(self.api._pending_update)

    def test_web_can_only_reach_the_three_public_methods(self):
        public = {n for n in dir(self.webui.Api) if not n.startswith("_") and "self_update" in n}
        self.assertEqual(public, {"start_self_update", "confirm_self_update", "cancel_self_update"})


# --------------------------------------------------------------------------- #
class LocaleAndFrontEndTests(unittest.TestCase):
    LANGS = ("zh-TW", "zh-CN", "en", "ja")

    def bundle(self, lang):
        with open(os.path.join(ROOT, "locales", lang, "strings.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_every_error_code_and_state_has_text_in_every_language(self):
        wanted = [f"selfupdate.err.{c}" for c in selfupdate.ERROR_CODES]
        wanted += [f"selfupdate.state.{s}" for s in ("untouched", "restored", "broken", "updated")]
        wanted += [f"selfupdate.stage.{s}" for s in ("checking", "downloading", "verifying", "applying")]
        for lang in self.LANGS:
            bundle = self.bundle(lang)
            for key in wanted + ["selfupdate.button", "selfupdate.title", "selfupdate.ready.title", "selfupdate.ready.body",
                                 "selfupdate.apply", "selfupdate.done", "selfupdate.fail.title", "selfupdate.manual.title",
                                 "selfupdate.manual.body"]:
                self.assertTrue(bundle.get(key), f"{lang} 缺少 {key}")

    def test_placeholders_match_across_languages(self):
        import re
        for key in [k for k in self.bundle("zh-TW") if k.startswith("selfupdate.")]:
            holders = {lang: set(re.findall(r"\{(\w+)\}", self.bundle(lang)[key])) for lang in self.LANGS}
            self.assertEqual(len({frozenset(h) for h in holders.values()}), 1, f"{key}: {holders}")

    def test_front_end_is_wired(self):
        with open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8") as f:
            js = f.read()
        for needle in ('e.type==="selfupdate_progress"', 'e.type==="selfupdate_ready"', 'e.type==="selfupdate_done"',
                       'e.type==="selfupdate_fail"', 'e.type==="selfupdate_manual"', "api().start_self_update()",
                       "api().confirm_self_update()", "api().cancel_self_update()", "function startSelfUpdate",
                       'id:"selfupd"', 'r.id==="selfupd"'):
            self.assertIn(needle, js)
        self.assertEqual(js.count('r.id==="selfupd"'), 3)  # 只有 UI 新版彈窗、檢查更新彈窗、安裝彈窗三處會處理


if __name__ == "__main__":
    unittest.main()
