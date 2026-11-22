"""輸入網址的整理：只讀 http(s):// 開頭、沒有開頭時偵測合法網址。"""
import os
import tempfile
import unittest

from modules import downloader
from modules.i18n import t
from modules.urls import extract_url


class WithSchemeTests(unittest.TestCase):
    def test_plain_urls_are_unchanged(self):
        for url in ("https://youtu.be/abc", "http://example.com/a?b=1&c=2#x", "https://localhost:8080/x",
                    "https://zh.wikipedia.org/wiki/中文", "https://en.wikipedia.org/wiki/Foo_(bar)"):
            self.assertEqual(extract_url(url), url)

    def test_everything_before_the_scheme_is_ignored(self):
        self.assertEqual(extract_url("快看 https://youtu.be/abc 很好看"), "https://youtu.be/abc")
        self.assertEqual(extract_url("標題：https://youtu.be/abc"), "https://youtu.be/abc")
        self.assertEqual(extract_url("看這個https://youtu.be/abc"), "https://youtu.be/abc")
        self.assertEqual(extract_url("「https://youtu.be/abc」"), "https://youtu.be/abc")

    def test_reading_stops_at_whitespace_including_fullwidth_space(self):
        self.assertEqual(extract_url("https://a.com/x 後面不讀"), "https://a.com/x")
        self.assertEqual(extract_url("https://a.com/x\u3000後面不讀"), "https://a.com/x")
        self.assertEqual(extract_url("https://a.com/x\tmore"), "https://a.com/x")

    def test_scheme_is_case_insensitive_and_normalised_to_lowercase(self):
        self.assertEqual(extract_url("HTTPS://Example.com/Path"), "https://Example.com/Path")

    def test_sentence_punctuation_is_not_part_of_the_url(self):
        self.assertEqual(extract_url("https://a.com/x，謝謝"), "https://a.com/x")
        self.assertEqual(extract_url("https://a.com/x。"), "https://a.com/x")
        self.assertEqual(extract_url("https://a.com/x."), "https://a.com/x")
        self.assertEqual(extract_url("(https://a.com/x)"), "https://a.com/x")

    def test_first_url_wins_when_there_are_several(self):
        self.assertEqual(extract_url("a https://one.com/1 https://two.com/2"), "https://one.com/1")
        self.assertEqual(extract_url("youtube.com/x https://vimeo.com/y"), "https://vimeo.com/y")  # 有 https:// 的優先

    def test_scheme_without_host_is_not_a_url(self):
        for text in ("https://", "https:// ", "http:///path"):
            self.assertIsNone(extract_url(text), text)


class WithoutSchemeTests(unittest.TestCase):
    def test_valid_domains_get_https(self):
        cases = {
            "youtube.com/watch?v=x": "https://youtube.com/watch?v=x",
            "www.facebook.com/video/1": "https://www.facebook.com/video/1",
            "youtu.be/abc": "https://youtu.be/abc",
            "b23.tv/BV1xx": "https://b23.tv/BV1xx",
            "fb.watch/abc123/": "https://fb.watch/abc123/",
            "x.com:8080/path#frag": "https://x.com:8080/path#frag",
            "youtube.com": "https://youtube.com",
            "my-site.co.uk/a": "https://my-site.co.uk/a",
        }
        for text, want in cases.items():
            self.assertEqual(extract_url(text), want, text)

    def test_text_around_the_domain_is_ignored(self):
        self.assertEqual(extract_url("快看 youtube.com/watch?v=x"), "https://youtube.com/watch?v=x")
        self.assertEqual(extract_url("看youtube.com/watch?v=x"), "https://youtube.com/watch?v=x")
        self.assertEqual(extract_url("(youtube.com/x)"), "https://youtube.com/x")
        self.assertEqual(extract_url("youtube.com。"), "https://youtube.com")
        self.assertEqual(extract_url("x.com/a！好看"), "https://x.com/a")

    def test_not_urls(self):
        for text in ("", None, "hello world", "video.mp4", "192.168.1.1", "v1.2", "a.b", "3.5.2", "名稱.中文",
                     "user@gmail.com", "name.surname@gmail.com"):
            self.assertIsNone(extract_url(text), repr(text))

    def test_other_schemes_are_rejected(self):
        for text in ("ftp://x.com/a", "rtmp://a.b/c", "ytsearch:cats.com", "javascript:alert(1)", "file:///c:/x.mp4"):
            self.assertIsNone(extract_url(text), text)


class BatchTests(unittest.TestCase):
    def read(self, text):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "list.txt")
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write(text)
            return downloader.read_batch(path)

    def test_lines_are_cleaned_deduplicated_and_unrecognised_lines_counted(self):
        urls, ignored = self.read(
            "# 註解\n"
            "\n"
            "https://youtu.be/a\n"
            "標題 https://youtu.be/b\n"
            "youtube.com/watch?v=c\n"
            "https://youtube.com/watch?v=c\n"       # 和上一行整理後相同 → 去重
            "這一行沒有網址\n"
            "ytsearch:cats\n"
            "# https://youtu.be/ignored-comment\n")
        self.assertEqual(urls, ["https://youtu.be/a", "https://youtu.be/b", "https://youtube.com/watch?v=c"])
        self.assertEqual(ignored, 2)

    def test_empty_list(self):
        self.assertEqual(self.read("# only comments\n\n"), ([], 0))

    def test_read_batch_urls_is_the_urls_only_version(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "l.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("a https://x.com/1\njunk\n")
            self.assertEqual(downloader.read_batch_urls(path), ["https://x.com/1"])


class ExpandTargetTests(unittest.TestCase):
    def test_single_target(self):
        self.assertEqual(downloader.expand_target("  快看 youtube.com/watch?v=x  "), ["https://youtube.com/watch?v=x"])
        self.assertEqual(downloader.expand_target("not a url"), [])
        self.assertEqual(downloader.expand_target(""), [])

    def test_batch_target(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "l.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("x.com/a\n")
            self.assertEqual(downloader.expand_target("file:" + path), ["https://x.com/a"])


class ApiFlowTests(unittest.TestCase):
    """不開視窗，直接呼叫 Api 的下載前檢查。"""

    @classmethod
    def setUpClass(cls):
        from modules import i18n
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        i18n.init(root)
        i18n.set_language("zh-TW")

    def setUp(self):
        from modules import webui
        self.tmp = tempfile.TemporaryDirectory()
        self.api = webui.Api(self.tmp.name, {}, os.path.join(self.tmp.name, "c.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_prepare_returns_the_cleaned_url(self):
        r = self.api.prepare_download({"url": "快看 youtube.com/watch?v=abc", "outdir": self.tmp.name})
        self.assertTrue(r["ok"])
        self.assertEqual(r["url"], "https://youtube.com/watch?v=abc")

    def test_prepare_detects_playlists_after_cleaning(self):
        r = self.api.prepare_download({"url": "youtube.com/playlist?list=PL1", "outdir": self.tmp.name})
        self.assertEqual((r["ask"], r["kind"], r["url"]), ("single", "playlist", "https://youtube.com/playlist?list=PL1"))

    def test_prepare_rejects_garbage_without_calling_ytdlp(self):
        r = self.api.prepare_download({"url": "hello world", "outdir": self.tmp.name})
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], t("err.invalid_url"))

    def test_prepare_empty_still_asks_for_a_url(self):
        r = self.api.prepare_download({"url": "   ", "outdir": self.tmp.name})
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], t("err.no_url"))

    def test_fetch_info_rejects_garbage_and_batch_targets(self):
        self.assertEqual(self.api.fetch_info("hello world")["error"], t("err.invalid_url"))
        self.assertEqual(self.api.fetch_info("file:C:\\x.txt")["error"], t("err.batch_info"))


if __name__ == "__main__":
    unittest.main()
