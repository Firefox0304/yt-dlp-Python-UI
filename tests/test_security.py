"""自動開啟檔案／網址的安全檢查，以及診斷包的隱私遮蔽。"""
import json
import os
import tempfile
import unittest
import zipfile

from modules import diagnostics, utils


class SafeToOpenTests(unittest.TestCase):
    def test_media_files_are_allowed(self):
        for path in ("C:\\Downloads\\movie.mp4", "a.mkv", "song.m4a", "Download/標題.webm", "x.opus", "noext"):
            self.assertTrue(utils.is_safe_to_open(path), path)

    def test_executables_and_scripts_are_blocked(self):
        for path in ("x.exe", "X.EXE", "a.bat", "a.cmd", "setup.msi", "a.lnk", "a.url", "a.ps1", "a.vbs", "a.js",
                     "a.hta", "a.jar", "a.py", "a.docm", "page.html", "p.svg", "C:\\x\\run.cmd", "a.scr", "a.com"):
            self.assertFalse(utils.is_safe_to_open(path), path)

    def test_windows_filename_tricks_are_blocked(self):
        for path in ("movie.mp4.exe", "a.exe.", "a.exe ", "a.exe. .", "a.txt:evil.exe", "report.pdf:stream"):
            self.assertFalse(utils.is_safe_to_open(path), repr(path))

    def test_empty_values_are_blocked(self):
        for path in ("", None, "   "):
            self.assertFalse(utils.is_safe_to_open(path), repr(path))


class SafeWebUrlTests(unittest.TestCase):
    def test_http_and_https_are_allowed(self):
        for url in ("https://example.com/a?b=1&c=2", "HTTP://x.org", "https://youtu.be/abc", " https://x.org/ "):
            self.assertTrue(utils.is_safe_web_url(url), url)

    def test_other_schemes_and_malformed_urls_are_blocked(self):
        for url in ("javascript:alert(1)", "file:///c:/x.exe", "ftp://x.org", "https://", "http:///path",
                    "https://exa mple.com", "https://x.com/\nmore", "https://x.com/\x00", "", None, "example.com"):
            self.assertFalse(utils.is_safe_web_url(url), repr(url))


class RedactTests(unittest.TestCase):
    def test_home_directory_is_masked(self):
        text = diagnostics.redact("open C:\\Users\\子睿\\Desktop\\x.mp4", home="C:\\Users\\子睿")
        self.assertNotIn("子睿", text)
        self.assertIn("%USERPROFILE%", text)

    def test_other_user_directories_are_masked(self):
        text = diagnostics.redact("D:/Users/bob/a and /home/alice/b and E:\\Users\\carol\\c", home="")
        for name in ("bob", "alice", "carol"):
            self.assertNotIn(name, text)
        self.assertEqual(text.count("<user>"), 3)

    def test_unrelated_text_is_untouched(self):
        self.assertEqual(diagnostics.redact("yt-dlp 2026.11.01", home=""), "yt-dlp 2026.11.01")


class BuildPackageTests(unittest.TestCase):
    def test_package_contains_redacted_files_only(self):
        with tempfile.TemporaryDirectory() as base:
            os.makedirs(os.path.join(base, "logs"))
            with open(os.path.join(base, "logs", "latest.log"), "w", encoding="utf-8") as f:
                f.write("Running command: C:\\Users\\secretname\\yt-dlp.exe --cookies C:\\Users\\secretname\\c.txt\n")
            dest = os.path.join(base, "out.zip")
            names = diagnostics.build_package(dest, base, "平台：Windows\n路徑 C:\\Users\\secretname\\x",
                                              {"download_path": "C:\\Users\\secretname\\Videos", "language": "en"},
                                              home="C:\\Users\\secretname")
            self.assertEqual(sorted(names), ["diagnostics.txt", "latest.log", "settings.json"])
            with zipfile.ZipFile(dest) as zf:
                self.assertEqual(sorted(zf.namelist()), sorted(names))
                for name in names:
                    self.assertNotIn("secretname", zf.read(name).decode("utf-8"), name)
                self.assertEqual(json.loads(zf.read("settings.json"))["language"], "en")
                self.assertIn("平台：Windows", zf.read("diagnostics.txt").decode("utf-8"))

    def test_missing_log_does_not_break_export(self):
        with tempfile.TemporaryDirectory() as base:
            dest = os.path.join(base, "out.zip")
            diagnostics.build_package(dest, base, "report", {})
            with zipfile.ZipFile(dest) as zf:
                self.assertIn("latest.log", zf.namelist())


if __name__ == "__main__":
    unittest.main()
