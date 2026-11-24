"""引擎判斷：exe 版要問真正的 Python 有沒有 yt_dlp 模組，才不會把 pip 裝的 yt-dlp.exe 當成獨立檔去 -U。"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from modules import engine, updater

PYTHON = r"C:\Python\python.exe"
PIP_EXE = r"C:\Python\Scripts\yt-dlp.EXE"


class FakeRun:
    """記錄呼叫，依序回傳指定的結束代碼。"""

    def __init__(self, *codes):
        self.codes = list(codes)
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return SimpleNamespace(returncode=self.codes.pop(0) if self.codes else 0)


class FrozenEngineTests(unittest.TestCase):
    def setUp(self):
        engine._module_found.clear()
        self.base = tempfile.TemporaryDirectory()
        self.addCleanup(self.base.cleanup)
        self.addCleanup(engine._module_found.clear)

    def resolve(self, run, python=PYTHON, which=PIP_EXE):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch("modules.engine.utils.get_console_python", return_value=python), \
                mock.patch("modules.engine.shutil.which", return_value=which):
            return engine.resolve_engine(self.base.name, run=run)

    def test_frozen_with_module_in_real_python_uses_module(self):
        run = FakeRun(0)
        eng = self.resolve(run)
        self.assertEqual(eng.kind, "module")
        self.assertEqual(eng.command, [PYTHON, "-m", "yt_dlp"])
        self.assertEqual(run.calls[0][0], PYTHON)
        self.assertTrue(updater.ejs_applies(eng))

    def test_update_command_is_pip_not_self_update(self):
        eng = self.resolve(FakeRun(0))
        cmd = updater.build_update_command(eng)
        self.assertEqual(cmd[:5], [PYTHON, "-m", "pip", "install", "--upgrade"])
        self.assertNotIn("-U", cmd)

    def test_frozen_without_module_falls_back_to_path_exe(self):
        eng = self.resolve(FakeRun(1))
        self.assertEqual(eng.kind, "path_exe")
        self.assertEqual(eng.command, [PIP_EXE])
        self.assertEqual(updater.build_update_command(eng), [PIP_EXE, "-U"])

    def test_frozen_without_real_python_falls_back_to_path_exe(self):
        run = FakeRun()
        eng = self.resolve(run, python=None)
        self.assertEqual(eng.kind, "path_exe")
        self.assertEqual(run.calls, [])

    def test_not_found_is_not_cached_but_found_is(self):
        run = FakeRun(1, 0)
        self.assertEqual(self.resolve(run).kind, "path_exe")   # 沒裝：不記
        self.assertEqual(self.resolve(run).kind, "module")     # 剛裝好：下一次就看得到
        self.assertEqual(self.resolve(run).kind, "module")     # 有裝：不必再開子行程
        self.assertEqual(len(run.calls), 2)

    def test_subprocess_error_means_not_installed(self):
        def boom(*args, **kwargs):
            raise OSError("no python")
        self.assertEqual(self.resolve(boom).kind, "path_exe")

    def make_local_exe(self):
        open(os.path.join(self.base.name, "yt-dlp.exe"), "wb").close()

    def test_module_beats_local_exe(self):
        self.make_local_exe()
        self.assertEqual(self.resolve(FakeRun(0)).kind, "module")

    def test_local_exe_used_when_no_module(self):
        self.make_local_exe()
        eng = self.resolve(FakeRun(1))
        self.assertEqual(eng.kind, "local_exe")
        self.assertEqual(eng.command, [os.path.join(self.base.name, "yt-dlp.exe")])

    def test_local_exe_beats_path_exe_and_works_without_real_python(self):
        self.make_local_exe()
        self.assertEqual(self.resolve(FakeRun(), python=None).kind, "local_exe")

    def test_nothing_found_returns_none(self):
        self.assertIsNone(self.resolve(FakeRun(1), which=None))


class SourceEngineTests(unittest.TestCase):
    def test_not_frozen_keeps_in_process_lookup(self):
        run = FakeRun()
        with mock.patch.object(sys, "frozen", False, create=True), \
                mock.patch("modules.engine.utils.get_console_python", return_value=PYTHON), \
                mock.patch("modules.engine.importlib.util.find_spec", return_value=object()):
            eng = engine.resolve_engine(tempfile.gettempdir(), run=run)
        self.assertEqual(eng.kind, "module")
        self.assertEqual(run.calls, [])  # .pyw 直接執行時不多開子行程


class GetVersionTests(unittest.TestCase):
    ENGINE = engine.Engine("module", [PYTHON, "-m", "yt_dlp"], "test")

    def test_frozen_asks_real_python_not_in_process_metadata(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout="2026.11.23\n")

        def stale_metadata(name):
            raise AssertionError("exe 版不應讀行程內的中繼資料（可能是打包時的舊版本）")

        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch("modules.engine.importlib.metadata.version", side_effect=stale_metadata), \
                mock.patch("modules.engine.subprocess.run", side_effect=fake_run):
            version = engine.get_version(self.ENGINE)
        self.assertEqual(version, "2026.11.23")
        self.assertEqual(calls[0], [PYTHON, "-m", "yt_dlp", "--version"])

    def test_not_frozen_keeps_fast_metadata_lookup(self):
        def no_subprocess(*args, **kwargs):
            raise AssertionError(".pyw 直接執行時不需要開子行程")

        with mock.patch.object(sys, "frozen", False, create=True), \
                mock.patch("modules.engine.importlib.metadata.version", return_value="2026.11.23"), \
                mock.patch("modules.engine.subprocess.run", side_effect=no_subprocess):
            self.assertEqual(engine.get_version(self.ENGINE), "2026.11.23")


if __name__ == "__main__":
    unittest.main()
