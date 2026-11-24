"""UI（本程式）版本檢查：tag 解析、比較、檢查更新的顯示、啟動檢查通知（不碰網路）。"""
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules import check, i18n, update_summary, updater  # noqa: E402


class ParseTagTests(unittest.TestCase):
    def test_accepted_formats(self):
        for name, expected in (("UI-v7.1.0", "7.1.0"), ("v7.1.0", "7.1.0"), ("7.1.0", "7.1.0"),
                               ("ui_7.1", "7.1"), ("V7.1.0.1", "7.1.0.1"), (" UI-v7.1.0 ", "7.1.0")):
            self.assertEqual(updater.parse_ui_tag(name), expected, name)

    def test_rejected_formats(self):
        for name in ("v7.1.0-beta", "nightly", "", None, "release-7.1", "UI-v7", "v7.1.0\n; rm", "../7.1.0"):
            self.assertIsNone(updater.parse_ui_tag(name), repr(name))


class LatestVersionTests(unittest.TestCase):
    def test_picks_numerically_largest_regardless_of_order(self):
        tags = [{"name": "v7.9.0"}, {"name": "UI-v7.10.0"}, {"name": "v7.2.1"}, {"name": "preview"}]
        self.assertEqual(updater.latest_ui_version(tags), "7.10.0")

    def test_accepts_plain_strings_and_ignores_junk(self):
        self.assertEqual(updater.latest_ui_version(["v1.0", {"x": 1}, None, "v1.2"]), "1.2")

    def test_none_when_no_valid_tag(self):
        self.assertIsNone(updater.latest_ui_version([{"name": "beta"}]))
        self.assertIsNone(updater.latest_ui_version([]))
        self.assertIsNone(updater.latest_ui_version(None))

    def test_fetch_uses_fixed_github_url(self):
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["url"], seen["agent"] = req.full_url, req.get_header("User-agent")
            return io.BytesIO(json.dumps([{"name": "v7.0.2"}, {"name": "UI-v7.0.3"}]).encode())

        with mock.patch("modules.updater.urllib.request.urlopen", side_effect=fake_urlopen):
            self.assertEqual(updater.fetch_latest_ui(), "7.0.3")
        self.assertEqual(seen["url"], updater.UI_TAGS_URL)
        self.assertTrue(seen["agent"])
        self.assertTrue(updater.UI_TAGS_URL.startswith("https://api.github.com/repos/Firefox0304/yt-dlp-Python-UI/"))
        self.assertEqual(updater.PROJECT_URL, "https://github.com/Firefox0304/yt-dlp-Python-UI")


class CheckUiTests(unittest.TestCase):
    def test_update_available(self):
        self.assertEqual(updater.check_ui("7.0.2", fetch=lambda: "7.0.3"),
                         {"status": "update_available", "current": "7.0.2", "latest": "7.0.3"})

    def test_same_or_older_remote_is_ok(self):
        self.assertEqual(updater.check_ui("7.0.2", fetch=lambda: "7.0.2")["status"], "ok")
        self.assertEqual(updater.check_ui("7.1.0", fetch=lambda: "7.0.9")["status"], "ok")

    def test_ten_is_newer_than_nine(self):
        self.assertEqual(updater.check_ui("7.9.0", fetch=lambda: "7.10.0")["status"], "update_available")

    def test_failures_are_unknown_not_errors(self):
        def boom():
            raise OSError("offline")
        self.assertEqual(updater.check_ui("7.0.2", fetch=boom), {"status": "unknown", "current": "7.0.2"})
        self.assertEqual(updater.check_ui("7.0.2", fetch=lambda: None)["status"], "unknown")

    def test_defaults_to_app_version(self):
        from modules.version import APP_VERSION
        self.assertEqual(updater.check_ui(fetch=lambda: APP_VERSION)["current"], APP_VERSION)


class SummaryTests(unittest.TestCase):
    def setUp(self):
        i18n.init(ROOT)
        i18n.set_language("zh-TW")

    def results(self, ui):
        res = {"ytdlp": {"status": "up_to_date", "current": "2026.11.23"},
               "ejs": {"status": "ok", "current": "0.8.0"},
               "python": {"status": "ok", "current": "3.15.2", "patch_update": False, "new_series": False, "eol": False}}
        if ui is not None:
            res["ui"] = ui
        return res

    def lines(self, ui):
        text, problem, _ = update_summary.summarize_update(self.results(ui))
        return text.split("\n\n"), problem

    def test_new_version_shows_arrow_between_ejs_and_python(self):
        lines, problem = self.lines({"status": "update_available", "current": "7.0.2", "latest": "7.0.3"})
        self.assertEqual(lines[1], "yt-dlp-ejs：0.8.0")
        self.assertEqual(lines[2], "UI：v7.0.2 → v7.0.3")
        self.assertTrue(lines[3].startswith("Python："))
        self.assertFalse(problem)

    def test_up_to_date_shows_only_current(self):
        lines, _ = self.lines({"status": "ok", "current": "7.0.2", "latest": "7.0.2"})
        self.assertEqual(lines[2], "UI：v7.0.2")

    def test_unknown_is_not_a_problem(self):
        lines, problem = self.lines({"status": "unknown", "current": "7.0.2"})
        self.assertEqual(lines[2], "UI：v7.0.2（無法查詢最新版本）")
        self.assertFalse(problem)

    def test_no_ui_result_adds_no_line(self):
        lines, _ = self.lines(None)
        self.assertEqual(len(lines), 3)

    def test_other_languages_have_the_texts(self):
        for lang in ("zh-CN", "en", "ja"):
            i18n.set_language(lang)
            lines, _ = self.lines({"status": "update_available", "current": "7.0.2", "latest": "7.0.3"})
            self.assertIn("v7.0.2 → v7.0.3", lines[2], lang)
            self.assertNotIn("update.ui", lines[2], lang)


class StartupTests(unittest.TestCase):
    ITEMS = [{"id": "ytdlp", "action": "upgrade"}]
    UI = {"current": "7.0.2", "latest": "7.0.3"}

    def run_check(self, items=None, ui=None, items_error=None, ui_error=None):
        delivered = []
        with mock.patch("modules.check.collect_startup_items",
                        side_effect=items_error, return_value=items or []), \
                mock.patch("modules.check.collect_startup_ui", side_effect=ui_error, return_value=ui):
            check.run_startup_check("base", lambda items, ui: delivered.append((items, ui)))
        return delivered

    def test_items_and_ui_delivered_together(self):
        self.assertEqual(self.run_check(self.ITEMS, self.UI), [(self.ITEMS, self.UI)])

    def test_only_ui_still_notifies(self):
        self.assertEqual(self.run_check([], self.UI), [([], self.UI)])

    def test_only_items_has_no_ui(self):
        self.assertEqual(self.run_check(self.ITEMS, None), [(self.ITEMS, None)])

    def test_nothing_to_report_stays_silent(self):
        self.assertEqual(self.run_check([], None), [])

    def test_checks_do_not_affect_each_other(self):
        self.assertEqual(self.run_check(self.ITEMS, None, ui_error=OSError("offline")), [(self.ITEMS, None)])
        self.assertEqual(self.run_check([], self.UI, items_error=RuntimeError("boom")), [([], self.UI)])

    def test_collect_startup_ui(self):
        up = lambda: {"status": "update_available", "current": "7.0.2", "latest": "7.0.3", "extra": 1}  # noqa: E731
        self.assertEqual(check.collect_startup_ui(up), self.UI)
        self.assertIsNone(check.collect_startup_ui(lambda: {"status": "ok", "current": "7.0.2"}))
        self.assertIsNone(check.collect_startup_ui(lambda: {"status": "unknown", "current": "7.0.2"}))


class ApiPromptTests(unittest.TestCase):
    def test_prompt_carries_ui_and_can_be_claimed_later(self):
        from modules import webui
        with tempfile.TemporaryDirectory() as base:
            api = webui.Api(base, {}, os.path.join(base, "settings.json"))
            events = []
            with mock.patch.object(webui.Api, "_emit", lambda self, **e: events.append(e)):
                api._deliver_prompt([], {"current": "7.0.2", "latest": "7.0.3"})
            self.assertEqual(events, [{"type": "install_prompt", "items": [], "ui": {"current": "7.0.2", "latest": "7.0.3"}}])
            self.assertEqual(api.get_install_prompt()["ui"], {"current": "7.0.2", "latest": "7.0.3"})

    def test_project_page_url_is_fixed(self):
        from modules import webui
        with tempfile.TemporaryDirectory() as base, mock.patch("modules.webui.webbrowser.open") as opened:
            webui.Api(base, {}, os.path.join(base, "s.json")).open_project_page()
        opened.assert_called_once_with(updater.PROJECT_URL)


if __name__ == "__main__":
    unittest.main()
