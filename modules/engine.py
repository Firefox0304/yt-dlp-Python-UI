# modules/engine.py
"""決定要用哪一個 yt-dlp 來執行下載，讓下載、更新、診斷三處行為一致。

優先順序：
1. Python 環境的 yt_dlp 模組（以 ``python -m yt_dlp`` 子行程執行）
2. yt-dlp.exe：先程式資料夾內的，再 PATH 裡的
3. 都找不到就回傳 None，由 modules/check.py 的啟動檢查提示以 pip 安裝

模組也走子行程而不是在程式內呼叫，所以所有路徑的參數、進度解析、取消
（終止行程）行為完全相同，不會再有「備援路徑功能殘缺」的問題。
"""
import importlib.metadata
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from collections import namedtuple

from modules import utils

Engine = namedtuple("Engine", "kind command label")

JS_RUNTIME_NAMES = ("deno", "node", "bun", "qjs")


_FIND_YTDLP_CODE = "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('yt_dlp') else 1)"
_module_found = set()  # 只記「確定有裝」的 Python；沒裝的不記，這樣剛裝好時下一次判斷就會看到


def _python_has_ytdlp(python, run=subprocess.run):
    """問那個真正的 Python 有沒有 yt_dlp 模組（exe 版自己的環境看不到系統 Python 的套件）。"""
    if python in _module_found:
        return True
    try:
        proc = run([python, "-c", _FIND_YTDLP_CODE], capture_output=True, timeout=20,
                   env=utils.child_env(), **utils.hidden_subprocess_kwargs())
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    _module_found.add(python)
    return True


def _module_available(python, run=subprocess.run):
    if getattr(sys, "frozen", False):
        # exe 版：importlib 只看得到 exe 內部，會誤判成沒裝，導致把 pip 裝的 yt-dlp.exe 當獨立檔、用 -U 更新而失敗
        return _python_has_ytdlp(python, run)
    return importlib.util.find_spec("yt_dlp") is not None


def resolve_engine(base_dir, run=subprocess.run):
    python = utils.get_console_python()
    if python and _module_available(python, run):
        return Engine("module", [python, "-m", "yt_dlp"], f"Python module ({python} -m yt_dlp)")

    local = os.path.join(base_dir, "yt-dlp.exe")
    if os.path.isfile(local):
        return Engine("local_exe", [local], f"local yt-dlp.exe ({local})")

    path_exe = shutil.which("yt-dlp")
    if path_exe:
        return Engine("path_exe", [path_exe], f"yt-dlp on PATH ({path_exe})")
    return None


def describe(eng):
    """給使用者看的來源說明（依介面語言）；eng.label 是固定的英文，只給日誌用。"""
    from modules.i18n import t
    if eng.kind == "local_exe":
        return t("engine.local", path=eng.command[0])
    if eng.kind == "module":
        return t("engine.module", python=eng.command[0])
    return t("engine.path", path=eng.command[0])


def get_version(eng, timeout=30):
    """回傳目前引擎的 yt-dlp 版本字串，取不到時回傳 None。"""
    if eng is None:
        return None
    if eng.kind == "module" and not getattr(sys, "frozen", False):
        # exe 版不能讀行程內的中繼資料：那是 exe 自己的環境（可能含打包時的舊版本），不是系統 Python 的 yt-dlp，
        # 會讓版本永遠停在舊的而反覆提示更新；exe 版改問系統 Python（下面的 python -m yt_dlp --version）
        try:
            return importlib.metadata.version("yt-dlp")
        except importlib.metadata.PackageNotFoundError:
            pass
    try:
        proc = subprocess.run(
            list(eng.command) + ["--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            env=utils.child_env(), **utils.hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode:
        return None
    lines = proc.stdout.strip().splitlines()
    return lines[-1].strip() if lines else None


def find_js_runtimes():
    """偵測系統上有哪些 yt-dlp 可用的 JavaScript 執行環境。"""
    return [name for name in JS_RUNTIME_NAMES if shutil.which(name)]


_FFMPEG_VERSION_RE = re.compile(r"^ffmpeg version (\S+)", re.M)


def parse_ffmpeg_version(text):
    """從 ``ffmpeg -version`` 的輸出取出版本字串（例如 7.1.1-essentials_build-www.gyan.dev）。"""
    match = _FFMPEG_VERSION_RE.search(text or "")
    return match.group(1) if match else None


def get_ffmpeg_version(ffmpeg_path, timeout=15):
    """執行 ffmpeg -version 取得版本字串；無法執行或解析時回傳 None。"""
    try:
        proc = subprocess.run(
            [ffmpeg_path, "-version"], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, env=utils.child_env(), **utils.hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_ffmpeg_version(proc.stdout) if proc.returncode == 0 else None
