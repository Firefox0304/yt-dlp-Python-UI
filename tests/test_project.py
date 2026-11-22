"""專案層級的一致性：版本號單一來源、語言檔完整。"""
import os
import re
import unittest

from modules.version import APP_VERSION
from tools import check_locales

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class VersionTests(unittest.TestCase):
    def test_changelog_top_entry_matches_app_version(self):
        match = re.search(r"^# v(\S+)", read("CHANGELOG.md"), re.M)
        self.assertIsNotNone(match, "CHANGELOG.md 最上方要有「# v版本號」標題")
        self.assertEqual(match.group(1), APP_VERSION,
                         "CHANGELOG 最新版本和 modules/version.py 的 APP_VERSION 不一致")

    def test_version_is_not_hard_coded_in_the_page(self):
        # 標題列的版本由後端的 APP_VERSION 在啟動時填入，網頁裡不能再寫死
        for name in ("index.html", "app.js"):
            self.assertNotIn(f"v{APP_VERSION}", read("web", name), name)
        self.assertNotRegex(read("web", "index.html"), r'id="ver">\s*v\d')

    def test_readme_title_has_no_version(self):
        self.assertNotRegex(read("README.md").split("\n", 1)[0], r"v\d")


class PageLayoutTests(unittest.TestCase):
    def test_quick_actions_are_a_two_column_grid_in_this_order(self):
        html = read("web", "index.html")
        block = html.split('class="qa"', 1)[1].split("</div>", 1)[0]
        ids = re.findall(r'<button[^>]*\bid="(\w+)"', block)
        # 兩欄排列：載入範例|說明 / 下載歷史|開啟檔案位置 / 檢查更新|系統診斷（也是 Tab 鍵順序）
        self.assertEqual(ids, ["example", "help", "hist", "locate", "upd", "diag"])
        self.assertNotIn("wide2", block)
        self.assertNotIn("wide2", read("web", "style.css"))


class SidebarWidthTests(unittest.TestCase):
    """側欄寬度自適應（英文、日文的快速操作標籤較長）：這幾條規則缺一不可。"""

    def rule(self, selector):
        css = read("web", "style.css")
        match = re.search(re.escape(selector) + r"\{([^}]*)\}", css)
        self.assertIsNotNone(match, f"找不到 CSS 規則 {selector}")
        return match.group(1)

    def test_sidebar_track_grows_with_its_content_up_to_440px(self):
        self.assertIn("grid-template-columns:minmax(0,1fr) fit-content(440px)", self.rule(".grid"))

    def test_sidebar_never_gets_narrower_than_the_original_width(self):
        self.assertIn("min-width:clamp(300px,24vw,440px)", self.rule("aside.side"))

    def test_only_quick_actions_decide_the_sidebar_width(self):
        self.assertIn("contain:inline-size", self.rule(".side>:not(.qa)"))

    def test_quick_action_buttons_stay_on_one_line(self):
        self.assertIn("repeat(2,minmax(0,1fr))", self.rule(".qa"))
        rule = self.rule(".qa .btn")
        for part in ("white-space:nowrap", "height:40px", "text-overflow:ellipsis"):
            self.assertIn(part, rule)
        self.assertNotIn("min-height", rule)

    def test_single_column_layout_releases_the_sidebar_minimum(self):
        self.assertRegex(read("web", "style.css"), r"@media \(max-width:860px\)\{[^@]*aside\.side\{min-width:0\}")


class SmallWindowTests(unittest.TestCase):
    def test_small_window_zoom_is_in_the_compact_media_query(self):
        css = read("web", "style.css")
        block = css.split("@media (max-height:780px){", 1)[1]
        self.assertIn("zoom:.85", block.split("\n}", 1)[0])


class LocaleTests(unittest.TestCase):
    def test_locales_are_consistent(self):
        errors, _warnings = check_locales.run()
        self.assertEqual(errors, [], "\n" + "\n".join(errors))


if __name__ == "__main__":
    unittest.main()
