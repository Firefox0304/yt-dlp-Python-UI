"""「開啟檔案位置」挑選檔案的邏輯（不會真的開啟檔案總管）。"""
import os
import tempfile
import time
import unittest

from modules import history, utils


def touch(path, age_seconds=0):
    with open(path, "w", encoding="utf-8") as f:
        f.write("x")
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    return path


class LatestExistingFileTests(unittest.TestCase):
    def test_picks_newest_successful_item_with_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            old = touch(os.path.join(d, "old.mp4"))
            new = touch(os.path.join(d, "new.mp4"))
            items = [{"success": True, "files": [old]}, {"success": True, "files": [new]}]  # 舊→新
            self.assertEqual(history.latest_existing_file(items), new)

    def test_skips_failed_items_and_missing_files(self):
        with tempfile.TemporaryDirectory() as d:
            kept = touch(os.path.join(d, "kept.mp4"))
            items = [
                {"success": True, "files": [kept]},
                {"success": False, "files": [touch(os.path.join(d, "failed.mp4"))]},
                {"success": True, "files": [os.path.join(d, "deleted.mp4")]},
            ]
            self.assertEqual(history.latest_existing_file(items), kept)

    def test_uses_last_existing_file_of_an_item(self):
        with tempfile.TemporaryDirectory() as d:
            first, last = touch(os.path.join(d, "1.mp4")), touch(os.path.join(d, "2.mp4"))
            self.assertEqual(history.latest_existing_file([{"success": True, "files": [first, last]}]), last)

    def test_nothing_found(self):
        for items in ([], None, [{"success": True}], [{"success": True, "files": [None, 5, ""]}]):
            self.assertIsNone(history.latest_existing_file(items), items)


class LatestMediaFileTests(unittest.TestCase):
    def test_newest_media_file_wins(self):
        with tempfile.TemporaryDirectory() as d:
            touch(os.path.join(d, "a.mp4"), age_seconds=100)
            newest = touch(os.path.join(d, "b.mkv"), age_seconds=10)
            self.assertEqual(utils.latest_media_file(d), newest)

    def test_ignores_partial_downloads_subtitles_thumbnails_and_folders(self):
        with tempfile.TemporaryDirectory() as d:
            media = touch(os.path.join(d, "video.mp4"), age_seconds=500)
            for name in ("video.mp4.part", "video.mp4.ytdl", "v.vtt", "v.SRT", "v.jpg", "v.PNG", "v.webp"):
                touch(os.path.join(d, name))  # 都比 video.mp4 新
            os.makedirs(os.path.join(d, "sub"))
            self.assertEqual(utils.latest_media_file(d), media)

    def test_empty_or_missing_folder(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(utils.latest_media_file(d))
            self.assertIsNone(utils.latest_media_file(os.path.join(d, "nope")))


if __name__ == "__main__":
    unittest.main()
