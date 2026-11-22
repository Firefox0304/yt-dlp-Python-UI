"""進度範本、進度行解析與 FRAG（分片）偵測。"""
import unittest

from modules import progress
from modules.progress import PROGRESS_TEMPLATE, parse_progress_line


class TemplateTests(unittest.TestCase):
    def test_template_has_the_four_fields_in_order(self):
        # 解析靠欄位順序：百分比 | 速度 | ETA | FRAG；改範本時這個測試會提醒你同步改解析
        fields = [part.strip() for part in PROGRESS_TEMPLATE.split("|")]
        self.assertEqual(len(fields), 4)
        self.assertTrue(fields[0].startswith("Downloading:") and "_percent_str" in fields[0])
        self.assertIn("_speed_str", fields[1])
        self.assertTrue(fields[2].startswith("ETA") and "_eta_str" in fields[2])
        self.assertTrue(fields[3].startswith("FRAG") and "fragment_count" in fields[3])

    def test_downloader_uses_the_same_template(self):
        from modules import downloader
        self.assertIs(downloader.PROGRESS_TEMPLATE, progress.PROGRESS_TEMPLATE)


class ParseTests(unittest.TestCase):
    def test_normal_line(self):
        info = parse_progress_line("Downloading:12.3% | 1.50MiB/s | ETA 00:10 | FRAG NA")
        self.assertEqual((info.percent, info.speed, info.eta, info.fragmented), (12.3, "1.50MiB/s", "00:10", False))

    def test_padded_percent_from_ytdlp(self):
        info = parse_progress_line("Downloading:  45.0% | 2.00MiB/s | ETA 00:05 | FRAG NA")
        self.assertEqual(info.percent, 45.0)

    def test_comma_decimal(self):
        self.assertEqual(parse_progress_line("Downloading:12,5% | 1MiB/s | ETA 00:01 | FRAG NA").percent, 12.5)

    def test_percent_is_clamped(self):
        self.assertEqual(parse_progress_line("Downloading:250% | 1MiB/s | ETA 00:01 | FRAG NA").percent, 100.0)
        self.assertEqual(parse_progress_line("Downloading:100% | 1MiB/s | ETA 00:00 | FRAG NA").percent, 100.0)

    def test_na_percent_is_still_a_progress_line(self):
        info = parse_progress_line("Downloading:N/A% | N/A | ETA Unknown | FRAG NA")
        self.assertIsNotNone(info)
        self.assertIsNone(info.percent)
        self.assertEqual((info.speed, info.eta), ("N/A", "Unknown"))

    def test_default_ytdlp_line_has_only_percent(self):
        info = parse_progress_line("[download]  45.0% of 10.00MiB at 1.00MiB/s ETA 00:10")
        self.assertEqual(info.percent, 45.0)
        self.assertFalse(info.fragmented)
        self.assertEqual((info.speed, info.eta), ("", ""))

    def test_default_final_line(self):  # yt-dlp 預設格式的收尾行也是進度行
        self.assertEqual(parse_progress_line("[download] 100% of 10.00MiB in 00:01").percent, 100.0)

    def test_item_prefix_is_ignored(self):
        info = parse_progress_line("[2/5] Downloading:50.0% | 3MiB/s | ETA 00:02 | FRAG 8")
        self.assertEqual((info.percent, info.speed, info.eta, info.fragmented), (50.0, "3MiB/s", "00:02", True))

    def test_non_progress_lines_return_none(self):
        for line in ("", None, "random text", "[download] Destination: x.mp4", "[Merger] Merging formats into \"a.mp4\"",
                     "Downloading item 2 of 5", "[download] Downloading item 2 of 5"):
            self.assertIsNone(parse_progress_line(line), line)


class FragmentDetectionTests(unittest.TestCase):
    def test_numeric_fragment_count_means_fragmented(self):
        for count in ("1", "8", "16", "300"):
            self.assertTrue(parse_progress_line(f"Downloading:1.0% | 1MiB/s | ETA 00:01 | FRAG {count}").fragmented, count)

    def test_missing_fragment_values_mean_not_fragmented(self):
        for count in ("NA", "N/A", "None", ""):
            self.assertFalse(parse_progress_line(f"Downloading:1.0% | 1MiB/s | ETA 00:01 | FRAG {count}").fragmented, count)

    def test_line_without_frag_field_is_not_fragmented(self):
        self.assertFalse(parse_progress_line("Downloading:1.0% | 1MiB/s | ETA 00:01").fragmented)


if __name__ == "__main__":
    unittest.main()
