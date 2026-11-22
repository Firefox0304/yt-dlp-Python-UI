"""語言檔：載入器（每個語言一個資料夾、多檔合併）與 tools/check_locales.py 的檢查。"""
import json
import os
import shutil
import tempfile
import unittest

from modules import i18n
from tools import check_locales

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        locales = os.path.join(self.tmp.name, "locales")
        write_json(os.path.join(locales, "zh-TW", "strings.json"), {"a": "甲", "only.tw": "只有繁中", "hello": "你好 {name}"})
        write_json(os.path.join(locales, "zh-TW", "errors.json"), {"err.x": "錯誤"})
        write_json(os.path.join(locales, "zh-TW", "logs.json"), {"log.y": "日誌"})
        write_json(os.path.join(locales, "en", "strings.json"), {"a": "A", "hello": "Hello {name}"})
        write_json(os.path.join(locales, "en", "errors.json"), {"err.x": "Error"})
        i18n.init(self.tmp.name)

    def tearDown(self):
        i18n.init(ROOT)
        i18n.set_language("zh-TW")
        self.tmp.cleanup()

    def test_all_json_files_in_the_language_folder_are_merged(self):
        i18n.set_language("zh-TW")
        self.assertEqual((i18n.t("a"), i18n.t("err.x"), i18n.t("log.y")), ("甲", "錯誤", "日誌"))

    def test_missing_text_falls_back_to_english_then_base_language(self):
        i18n.set_language("en")
        self.assertEqual(i18n.t("err.x"), "Error")
        self.assertEqual(i18n.t("log.y"), "日誌")      # en 沒有 → 基準語言
        self.assertEqual(i18n.t("only.tw"), "只有繁中")
        self.assertEqual(i18n.t("no.such.key"), "no.such.key")

    def test_placeholders(self):
        i18n.set_language("en")
        self.assertEqual(i18n.t("hello", name="Bob"), "Hello Bob")
        self.assertEqual(i18n.t("hello"), "Hello {name}")  # 沒給變數就原樣

    def test_bundle_merges_everything(self):
        bundle = i18n.bundle("en")
        self.assertEqual((bundle["a"], bundle["err.x"], bundle["log.y"]), ("A", "Error", "日誌"))

    def test_missing_folder_and_broken_file_do_not_crash(self):
        i18n.set_language("ja")  # 沒有 ja 資料夾：記警告，改用英文
        with self.assertLogs(i18n.LOG, level="WARNING"):
            self.assertEqual(i18n.t("a"), "A")
        with open(os.path.join(self.tmp.name, "locales", "en", "broken.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        i18n._cache.clear()
        i18n.set_language("en")  # 壞掉的檔案只記警告，同資料夾其他檔案照常載入
        with self.assertLogs(i18n.LOG, level="WARNING"):
            self.assertEqual(i18n.t("a"), "A")

    def test_real_project_languages_all_load(self):
        i18n.init(ROOT)
        for code in i18n.CODES:
            self.assertGreater(len(i18n.bundle(code)), 150, code)
            self.assertIn("err.no_url", i18n.bundle(code), code)
            self.assertIn("log.ready", i18n.bundle(code), code)


class CheckerTests(unittest.TestCase):
    """複製一份專案，故意弄壞語言檔，確認檢查腳本抓得到。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for name in ("locales", "modules", "web"):
            shutil.copytree(os.path.join(ROOT, name), os.path.join(self.tmp.name, name),
                            ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(os.path.join(ROOT, "yt-dlp-Python-UI.pyw"), self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, *parts):
        return os.path.join(self.tmp.name, "locales", *parts)

    def load(self, *parts):
        with open(self.path(*parts), encoding="utf-8") as f:
            return json.load(f)

    def check(self):
        errors, _ = check_locales.run(self.tmp.name)
        return "\n".join(errors)

    def test_clean_copy_passes(self):
        self.assertEqual(self.check(), "")

    def test_missing_and_extra_keys(self):
        data = self.load("en", "strings.json")
        data.pop("quick.locate")
        data["bogus.key"] = "x"
        write_json(self.path("en", "strings.json"), data)
        text = self.check()
        self.assertIn("缺少", text)
        self.assertIn("quick.locate", text)
        self.assertIn("bogus.key", text)

    def test_placeholder_mismatch(self):
        data = self.load("en", "logs.json")
        data["log.batch_ignored"] = "Skipped lines."
        write_json(self.path("en", "logs.json"), data)
        self.assertIn("占位符", self.check())

    def test_error_key_in_the_wrong_file(self):
        data = self.load("zh-TW", "strings.json")
        data["err.sneaky"] = "放錯"
        write_json(self.path("zh-TW", "strings.json"), data)
        self.assertIn("應該放在 errors.json", self.check())

    def test_ordinary_key_in_errors_file(self):
        data = self.load("zh-TW", "errors.json")
        data["btn.sneaky"] = "放錯"
        write_json(self.path("zh-TW", "errors.json"), data)
        self.assertIn("不該放在這個檔案", self.check())

    def test_same_key_in_two_files(self):
        data = self.load("ja", "strings.json")
        data["err.no_url"] = "重複"
        write_json(self.path("ja", "strings.json"), data)
        self.assertIn("同時出現在", self.check())

    def test_language_with_different_file_set(self):
        os.remove(self.path("ja", "logs.json"))
        self.assertIn("檔案組成", self.check())

    def test_missing_language_folder(self):
        shutil.rmtree(self.path("en"))
        self.assertIn("語言資料夾", self.check())

    def test_invalid_json(self):
        with open(self.path("zh-CN", "errors.json"), "w", encoding="utf-8") as f:
            f.write("{oops")
        self.assertIn("JSON", self.check())

    def test_key_used_in_code_but_not_defined(self):
        with open(os.path.join(self.tmp.name, "web", "app.js"), "a", encoding="utf-8") as f:
            f.write('\nt("typo.not.defined");\n')
        self.assertIn("typo.not.defined", self.check())


if __name__ == "__main__":
    unittest.main()
