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

from modules import channel, check, i18n, update_summary, updater  # noqa: E402


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
        self.assertEqual(updater.RELEASE_URL, "https://github.com/Firefox0304/yt-dlp-Python-UI/releases/latest")


class CheckUiTests(unittest.TestCase):
    def setUp(self):
        channel.set_beta(False)
        self.addCleanup(channel.set_beta, False)

    def test_update_available(self):
        self.assertEqual(updater.check_ui("7.0.2", fetch=lambda: "7.0.3"),
                         {"status": "update_available", "current": "7.0.2", "latest": "7.0.3"})

    def test_same_version_is_ok(self):
        self.assertEqual(updater.check_ui("7.0.2", fetch=lambda: "7.0.2")["status"], "ok")

    def test_local_newer_than_github_is_beta(self):
        self.assertEqual(updater.check_ui("7.1.0", fetch=lambda: "7.0.9"),
                         {"status": "beta", "current": "7.1.0", "latest": "7.0.9"})

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
        self.assertEqual(lines[1], "yt-dlp-ejs：0.8.0（已為最新版本）")
        self.assertEqual(lines[2], "UI：v7.0.2 → v7.0.3")
        self.assertTrue(lines[3].startswith("Python："))
        self.assertFalse(problem)

    def test_up_to_date_shows_only_current(self):
        lines, _ = self.lines({"status": "ok", "current": "7.0.2", "latest": "7.0.2"})
        self.assertEqual(lines[2], "UI：v7.0.2（已為最新版本）")

    def test_up_to_date_template_for_every_package(self):
        lines, _ = self.lines({"status": "ok", "current": "7.1.1", "latest": "7.1.1"})
        self.assertEqual(lines, ["yt-dlp：2026.11.23（已為最新版本）", "yt-dlp-ejs：0.8.0（已為最新版本）",
                                 "UI：v7.1.1（已為最新版本）", "Python：3.15.2（已為最新版本）"])

    def test_up_to_date_template_in_other_languages(self):
        for lang, tail in (("zh-CN", "（已是最新版本）"), ("en", " (up to date)"), ("ja", "（最新バージョンです）")):
            i18n.set_language(lang)
            lines, _ = self.lines({"status": "ok", "current": "7.1.1", "latest": "7.1.1"})
            for line in lines:
                self.assertTrue(line.endswith(tail), (lang, line))
            self.assertIn("2026.11.23", lines[0])

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
        opened.assert_called_once_with(updater.RELEASE_URL)


class VersionCompareTests(unittest.TestCase):
    def test_different_lengths_are_padded_with_zero(self):
        self.assertFalse(updater.is_newer("7.1", "7.1.0"))
        self.assertFalse(updater.is_newer("7.1.0", "7.1"))
        self.assertTrue(updater.is_newer("7.1.0.1", "7.1"))
        self.assertTrue(updater.is_newer("7.2", "7.1.9"))

    def test_ytdlp_style_versions_unchanged(self):
        self.assertTrue(updater.is_newer("2026.11.23", "2026.10.26"))
        self.assertFalse(updater.is_newer("2026.10.26", "2026.11.23"))
        self.assertTrue(updater.is_newer("2026.11.23.1", "2026.11.23"))
        self.assertFalse(updater.is_newer("2026.11.23", "2026.11.23"))

    def test_short_tag_does_not_cause_false_beta(self):
        channel.set_beta(False)
        self.addCleanup(channel.set_beta, False)
        self.assertEqual(updater.check_ui("7.1.0", fetch=lambda: "7.1")["status"], "ok")
        self.assertFalse(channel.is_beta())


class BetaStateTests(unittest.TestCase):
    def setUp(self):
        channel.set_beta(False)
        self.addCleanup(channel.set_beta, False)

    def test_beta_set_and_cleared_by_check_ui(self):
        updater.check_ui("7.1.2", fetch=lambda: "7.1.1")
        self.assertTrue(channel.is_beta())
        updater.check_ui("7.1.2", fetch=lambda: "7.1.2")      # 發佈了對應的 tag
        self.assertFalse(channel.is_beta())
        updater.check_ui("7.1.2", fetch=lambda: "7.1.1")
        updater.check_ui("7.1.2", fetch=lambda: "7.1.3")      # 有更新的版本
        self.assertFalse(channel.is_beta())

    def test_unknown_keeps_previous_state(self):
        def boom():
            raise OSError("offline")
        updater.check_ui("7.1.2", fetch=lambda: "7.1.1")
        updater.check_ui("7.1.2", fetch=boom)
        self.assertTrue(channel.is_beta())                    # 無法確認就不更動
        channel.set_beta(False)
        updater.check_ui("7.1.2", fetch=boom)
        self.assertFalse(channel.is_beta())                   # 從沒確認過就不標

    def test_labels(self):
        from modules.version import APP_NAME, APP_VERSION
        self.assertEqual(channel.version_label(), f"v{APP_VERSION}")
        self.assertEqual(channel.title(), f"{APP_NAME} v{APP_VERSION}")
        channel.set_beta(True)
        self.assertEqual(channel.version_label(), f"v{APP_VERSION} Beta")
        self.assertEqual(channel.title(), f"{APP_NAME} v{APP_VERSION} Beta")

    def test_summary_line_in_every_language(self):
        res = {"ytdlp": {"status": "up_to_date", "current": "2026.11.23"},
               "ui": {"status": "beta", "current": "7.1.2", "latest": "7.1.1"},
               "python": {"status": "ok", "current": "3.15.2", "patch_update": False, "new_series": False, "eol": False}}
        expected = {"zh-TW": "UI：v7.1.2 Beta（GitHub 最新發佈為 v7.1.1）",
                    "zh-CN": "UI：v7.1.2 Beta（GitHub 最新发布为 v7.1.1）",
                    "en": "UI: v7.1.2 Beta (latest release on GitHub is v7.1.1)",
                    "ja": "UI：v7.1.2 Beta（GitHub の最新リリースは v7.1.1 です）"}
        i18n.init(ROOT)
        for lang, line in expected.items():
            i18n.set_language(lang)
            text, problem, _ = update_summary.summarize_update(res)
            self.assertEqual(text.split("\n\n")[1], line, lang)
            self.assertFalse(problem)
        i18n.set_language("zh-TW")

    def test_diagnostics_header_has_beta(self):
        import zipfile
        from modules import diagnostics
        with tempfile.TemporaryDirectory() as base:
            for beta, header in ((False, channel.title()), (True, channel.title() + " Beta")):
                channel.set_beta(beta)
                dest = os.path.join(base, f"diag{beta}.zip")
                diagnostics.build_package(dest, base, "report", {}, home=base)
                with zipfile.ZipFile(dest) as z:
                    first = z.read("diagnostics.txt").decode("utf-8").splitlines()[0]
                self.assertEqual(first, channel.title())
                self.assertEqual(first.endswith(" Beta"), beta)


class StartupBetaTests(unittest.TestCase):
    def setUp(self):
        channel.set_beta(False)
        self.addCleanup(channel.set_beta, False)

    def run_check(self, ui_effect, on_beta, items=()):
        delivered = []
        with mock.patch("modules.check.collect_startup_items", return_value=list(items)), \
                mock.patch("modules.check.collect_startup_ui", side_effect=ui_effect):
            check.run_startup_check("base", lambda i, u: delivered.append((i, u)), on_beta)
        return delivered

    def test_on_beta_called_even_when_no_popup(self):
        seen = []

        def ui_check():
            channel.set_beta(True)  # 模擬 check_ui 查到本地比 GitHub 新
            return None

        delivered = self.run_check(ui_check, seen.append)
        self.assertEqual(seen, [True])
        self.assertEqual(delivered, [])

    def test_on_beta_called_when_ui_check_fails(self):
        seen = []
        self.run_check(OSError("offline"), seen.append)
        self.assertEqual(seen, [False])

    def test_on_beta_failure_does_not_block_popup(self):
        def bad(_flag):
            raise RuntimeError("boom")
        items = [{"id": "ytdlp", "action": "upgrade"}]
        self.assertEqual(self.run_check(lambda: None, bad, items), [(items, None)])


class ApiBetaTests(unittest.TestCase):
    def test_announce_beta_updates_page_and_window_title(self):
        from modules import webui
        channel.set_beta(True)
        self.addCleanup(channel.set_beta, False)

        class FakeWindow:
            titles = []

            def set_title(self, text):
                self.titles.append(text)

        with tempfile.TemporaryDirectory() as base:
            api = webui.Api(base, {}, os.path.join(base, "settings.json"))
            api._window = FakeWindow()
            events = []
            with mock.patch.object(webui.Api, "_emit", lambda self, **e: events.append(e)):
                api._announce_beta()
        self.assertEqual(events, [{"type": "ui_beta", "beta": True}])
        self.assertEqual(FakeWindow.titles, [channel.title()])
        self.assertTrue(channel.title().endswith(" Beta"))

    def test_set_title_failure_is_ignored(self):
        from modules import webui

        class BadWindow:
            def set_title(self, text):
                raise RuntimeError("no window")

        with tempfile.TemporaryDirectory() as base:
            api = webui.Api(base, {}, os.path.join(base, "settings.json"))
            api._window = BadWindow()
            with mock.patch.object(webui.Api, "_emit", lambda self, **e: None):
                api._announce_beta(False)  # 不丟例外就算通過

    def test_get_state_has_beta_flag(self):
        from modules import webui
        i18n.init(ROOT)
        i18n.set_language("zh-TW")
        self.addCleanup(channel.set_beta, False)
        with tempfile.TemporaryDirectory() as base:
            api = webui.Api(base, {}, os.path.join(base, "settings.json"))
            channel.set_beta(False)
            self.assertFalse(api.get_state()["beta"])
            channel.set_beta(True)
            self.assertTrue(api.get_state()["beta"])


class FrontEndWiringTests(unittest.TestCase):
    """前端沒有自動化測試，至少確認膠囊、事件、樣式三處接起來（改名時會在這裡提醒）。"""

    def read(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_capsule_is_wired(self):
        html, js, css = self.read("web", "index.html"), self.read("web", "app.js"), self.read("web", "style.css")
        self.assertIn('id="ver"></span><span class="beta" id="beta" hidden>Beta</span>', html)
        self.assertIn('e.type==="ui_beta"', js)
        self.assertIn('$("beta").hidden=!s.beta', js)
        self.assertIn(".beta[hidden]{display:none}", css)
        self.assertEqual(css.count("--beta-soft:"), 3)  # 色彩定義：淺色 1 + 深色 2（跟隨系統與手動）
        self.assertIn("background:var(--beta-soft)", css)  # .beta 有用到


if __name__ == "__main__":
    unittest.main()
