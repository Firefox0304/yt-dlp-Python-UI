# tests/test_check.py — 啟動檢查與自動安裝的純邏輯測試（不碰 winget、登錄檔與網路）
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules import check, updater, utils  # noqa: E402


class FakeRun:
    def __init__(self, outputs):
        self.outputs, self.calls = outputs, []

    def __call__(self, cmd, **_kw):
        self.calls.append(cmd)
        out = self.outputs.get(cmd[0], ("", 1))
        return subprocess.CompletedProcess(cmd, out[1], stdout=out[0], stderr="")


class PythonDetection(unittest.TestCase):
    def test_store_stub(self):
        self.assertTrue(utils.is_store_stub(r"C:\\Users\\a\\AppData\\Local\\Microsoft\\WindowsApps\\python.exe"))
        self.assertFalse(utils.is_store_stub(r"C:\\Python313\\python.exe"))

    def test_skips_stub_and_unrunnable(self):
        stub = os.path.join(r"C:\\Users\\a\\AppData\\Local\\Microsoft\\WindowsApps", "python3")
        bad = os.path.join("/bad", "python3")
        good = os.path.join("/good", "python3")
        run = FakeRun({bad: ("", 9009), good: ("Python 3.13.1", 0)})
        path = os.pathsep.join([os.path.dirname(stub), "/bad", "/good"])
        found = utils.find_real_python(path, run=run, isfile=lambda p: True, names=("python3",))
        self.assertEqual(found, good)
        self.assertNotIn(stub, [c[0] for c in run.calls])

    def test_none_found(self):
        self.assertIsNone(utils.find_real_python("", run=FakeRun({}), isfile=lambda p: True, names=("python3",)))


class PathMerge(unittest.TestCase):
    def test_registry_first_and_dedupe(self):
        sep = os.pathsep
        merged = check.merge_path([sep.join(["/A", "/B"]), "/C"], sep.join(["/a", "/venv"]))
        self.assertEqual(merged.split(sep), ["/A", "/B", "/C", "/venv"])

    def test_refresh_path_writes_environ(self):
        env = {"PATH": "x"}
        check.refresh_path(read=lambda: ["y"], environ=env)
        self.assertEqual(env["PATH"].split(os.pathsep), ["y", "x"])


class Planning(unittest.TestCase):
    def test_order_and_actions(self):
        items = check.plan_items({"deno", "python"}, {"ytdlp", "ffmpeg", "python"})
        self.assertEqual([(i["id"], i["action"]) for i in items],
                         [("python", "install"), ("ytdlp", "upgrade"), ("ffmpeg", "upgrade"), ("deno", "install")])

    def test_python_never_upgrade(self):
        self.assertEqual(check.plan_items(set(), {"python"}), [])

    def test_sanitize(self):
        raw = [{"id": "deno", "action": "install"}, {"id": "evil", "action": "install"}, {"id": "ytdlp", "action": "rm"},
               "x", {"id": "python", "action": "install"}]
        self.assertEqual([i["id"] for i in check.sanitize_items(raw)], ["python", "deno"])

    def test_items_from_results(self):
        res = {"ytdlp": {"status": "update_available"}, "ffmpeg": {"update": True}, "deno": {"update": False}}
        self.assertEqual([i["id"] for i in check.items_from_results(res)], ["ytdlp", "ffmpeg"])

    def test_needs_winget(self):
        self.assertFalse(check.needs_winget([{"id": "ytdlp", "action": "install"}]))
        self.assertTrue(check.needs_winget([{"id": "deno", "action": "install"}]))


class Commands(unittest.TestCase):
    def test_winget(self):
        cmd = check.winget_command("winget", "install", "Gyan.FFmpeg")
        self.assertEqual(cmd[:5], ["winget", "install", "-e", "--id", "Gyan.FFmpeg"])
        self.assertIn("--accept-source-agreements", cmd)
        self.assertIn("--accept-package-agreements", cmd)
        self.assertEqual(check.winget_command("winget", "upgrade", "x")[1], "upgrade")

    def test_pip(self):
        self.assertEqual(check.pip_command("py", "install")[:5], ["py", "-m", "pip", "install", "yt-dlp"])
        self.assertIn("-U", check.pip_command("py", "upgrade"))

    def test_python_id(self):
        self.assertEqual(check.python_package_id("3.14"), "Python.Python.3.14")
        self.assertEqual(check.python_package_id("x; rm"), "Python.Python." + check.DEFAULT_PYTHON_SERIES)
        self.assertEqual(check.pick_python_series(lambda: {"overall": "3.14.1"}), "3.14")
        self.assertEqual(check.pick_python_series(lambda: 1 / 0), check.DEFAULT_PYTHON_SERIES)


class Progress(unittest.TestCase):
    def test_pip_stages_monotonic(self):
        f = 0.0
        for line, want in (("Collecting yt-dlp", .15), ("Downloading x.whl", .4), ("Collecting y", .4),
                           ("Installing collected packages: yt-dlp", .8), ("Successfully installed yt-dlp-1", 1.0)):
            f = check.stage_progress("pip", line, f)
            self.assertEqual(f, want)

    def test_winget_stages_and_percent(self):
        self.assertEqual(check.stage_progress("winget", "Found FFmpeg [Gyan.FFmpeg]", 0), .1)
        self.assertAlmostEqual(check.stage_progress("winget", "  50 %", .1), .475)
        self.assertEqual(check.stage_progress("winget", "Successfully installed", .5), 1.0)
        self.assertEqual(check.stage_progress("other", "anything", .2), .2)

    def test_overall(self):
        self.assertEqual(check.overall_progress(1, 4, .5), 37.5)

    def test_exit_codes(self):
        self.assertTrue(check.command_succeeded("winget", 0))
        self.assertTrue(check.command_succeeded("winget", -1978335189))  # 0x8A15002B：沒有可用更新
        self.assertFalse(check.command_succeeded("winget", 1))
        self.assertFalse(check.command_succeeded("pip", 1))


class WingetUpgradeCheck(unittest.TestCase):
    def test_output_matching_is_language_independent(self):
        self.assertTrue(updater.output_lists_package("名稱 識別碼\nFFmpeg  Gyan.FFmpeg  7.0", "Gyan.FFmpeg"))
        self.assertFalse(updater.output_lists_package("找不到符合輸入條件的已安裝套件。", "Gyan.FFmpeg"))

    def test_has_upgrade(self):
        run = FakeRun({"w": ("x DenoLand.Deno 2.0", 0)})
        self.assertTrue(updater.winget_has_upgrade("DenoLand.Deno", run=run, winget="w"))
        self.assertEqual(run.calls[0][:3], ["w", "list", "--upgrade-available"])
        self.assertFalse(updater.winget_has_upgrade("Gyan.FFmpeg", run=run, winget="w"))

    def test_deno_version(self):
        self.assertEqual(updater.parse_deno_version("deno 2.1.4 (stable)\nv8 13"), "2.1.4")


class Ejs(unittest.TestCase):
    ENG = type("E", (), {"kind": "module", "command": ["py"]})()
    EXE = type("E", (), {"kind": "local_exe", "command": ["x.exe"]})()

    def test_applies_only_to_module_engine(self):
        self.assertTrue(updater.ejs_applies(self.ENG))
        self.assertFalse(updater.ejs_applies(self.EXE))
        self.assertFalse(updater.ejs_applies(None))

    def test_version_read(self):
        self.assertEqual(updater.get_ejs_version("py", run=FakeRun({"py": ("0.3.1\n", 0)})), "0.3.1")
        self.assertIsNone(updater.get_ejs_version("py", run=FakeRun({"py": ("", 1)})))

    def test_check_states(self):
        miss = updater.check_ejs(self.ENG, get_version=lambda p: None)
        self.assertEqual(miss["status"], "missing")
        new = updater.check_ejs(self.ENG, fetch=lambda: "0.4.0", get_version=lambda p: "0.3.1")
        self.assertEqual(new["status"], "update_available")
        same = updater.check_ejs(self.ENG, fetch=lambda: "0.3.1", get_version=lambda p: "0.3.1")
        self.assertEqual(same["status"], "ok")
        off = updater.check_ejs(self.ENG, fetch=lambda: 1 / 0, get_version=lambda p: "0.3.1")
        self.assertEqual(off["status"], "ok")

    def test_order_pip_and_results(self):
        items = check.plan_items({"deno", "ejs", "ytdlp"}, set())
        self.assertEqual([i["id"] for i in items], ["ytdlp", "ejs", "deno"])
        self.assertFalse(check.needs_winget([{"id": "ejs", "action": "install"}]))
        self.assertEqual(check.pip_command("py", "upgrade", "yt-dlp-ejs")[-2], "yt-dlp-ejs")
        res = {"ejs": {"status": "update_available"}}
        self.assertEqual([i["id"] for i in check.items_from_results(res)], ["ejs"])


class Restart(unittest.TestCase):
    def test_frozen_restarts_itself(self):
        self.assertEqual(check.build_restart_command("d", frozen=True, executable="C:\\a.exe"), ["C:\\a.exe"])

    def test_pyw_uses_pythonw(self):
        cmd = check.build_restart_command("d", frozen=False, executable=os.path.join("P", "python.exe"),
                                          argv0=os.path.join("D", "app.pyw"), isfile=lambda p: True)
        self.assertEqual(os.path.basename(cmd[0]), "pythonw.exe")
        self.assertEqual(os.path.basename(cmd[1]), "app.pyw")


class InstallFlow(unittest.TestCase):
    def test_no_winget_raises_with_pages(self):
        orig = utils.winget_available
        utils.winget_available = lambda: False
        try:
            with self.assertRaises(check.InstallError) as cm:
                check.run_install(ROOT, [{"id": "ffmpeg", "action": "install"}], lambda **k: None)
        finally:
            utils.winget_available = orig
        self.assertEqual(cm.exception.pages, ("ffmpeg",))


if __name__ == "__main__":
    unittest.main()
