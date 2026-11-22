"""下載命令組合與輸出路徑解析（不會真的執行 yt-dlp）。"""
import tempfile
import unittest

from modules import downloader
from modules.downloader import DownloadItem, DownloadOptions, build_command, extract_output_path, normalize_fragments
from modules.engine import Engine

URL = "https://example.com/watch?v=abc"


def make_command(**option_overrides):
    eng = Engine("module", ["python", "-m", "yt_dlp"], "test")
    with tempfile.TemporaryDirectory() as base:
        return build_command(base, eng, DownloadItem(URL), base, DownloadOptions(**option_overrides))


class FragmentOptionTests(unittest.TestCase):
    def test_normalize_accepts_only_known_choices(self):
        for value in (4, 8, 16, "8", " 16 "):
            self.assertEqual(normalize_fragments(value), int(str(value).strip()))
        for value in (0, 5, 32, -1, "abc", None, "", 4.5):
            self.assertEqual(normalize_fragments(value), downloader.DEFAULT_FRAGMENTS, repr(value))

    def test_n_flag_only_when_boost_enabled(self):
        self.assertNotIn("-N", make_command(concurrent_download=False, concurrent_fragments=16))

    def test_n_flag_uses_selected_count(self):
        cmd = make_command(concurrent_download=True, concurrent_fragments=8)
        self.assertEqual(cmd[cmd.index("-N") + 1], "8")

    def test_n_flag_falls_back_for_bad_count(self):
        cmd = make_command(concurrent_download=True, concurrent_fragments=7)
        self.assertEqual(cmd[cmd.index("-N") + 1], str(downloader.DEFAULT_FRAGMENTS))


class CommandShapeTests(unittest.TestCase):
    def test_url_is_last_and_separated_by_double_dash(self):
        cmd = make_command()
        self.assertEqual(cmd[-2:], ["--", URL])

    def test_progress_template_is_passed_through(self):
        cmd = make_command()
        self.assertEqual(cmd[cmd.index("--progress-template") + 1], downloader.PROGRESS_TEMPLATE)

    def test_audio_format_extracts_audio(self):
        cmd = make_command(fmt="mp3")
        self.assertIn("-x", cmd)
        self.assertEqual(cmd[cmd.index("--audio-format") + 1], "mp3")

    def test_video_format_sets_merge_container(self):
        cmd = make_command(fmt="mkv")
        self.assertEqual(cmd[cmd.index("--merge-output-format") + 1], "mkv")


class OutputPathTests(unittest.TestCase):
    def test_destination_and_merger_lines(self):
        self.assertEqual(extract_output_path("[download] Destination: C:\\Downloads\\a.mp4"), "C:\\Downloads\\a.mp4")
        self.assertEqual(extract_output_path('[Merger] Merging formats into "C:\\Downloads\\a.mkv"'), "C:\\Downloads\\a.mkv")
        self.assertEqual(extract_output_path("[ExtractAudio] Destination: a.mp3"), "a.mp3")
        self.assertEqual(extract_output_path("[download] a.mp4 has already been downloaded"), "a.mp4")

    def test_side_files_are_ignored(self):
        for name in ("a.vtt", "a.srt", "a.jpg", "a.webp", "a.info.json", "a.description"):
            self.assertIsNone(extract_output_path(f"[download] Destination: {name}"), name)

    def test_unrelated_lines(self):
        self.assertIsNone(extract_output_path("[youtube] Extracting URL"))


if __name__ == "__main__":
    unittest.main()
