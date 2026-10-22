# modules/engine.py
"""決定要用哪一個 yt-dlp 來執行下載，讓下載、更新、診斷三處行為一致。

優先順序：
1. 程式資料夾內的 yt-dlp.exe
2. 目前 Python 環境的 yt_dlp 模組（以 ``python -m yt_dlp`` 子行程執行）
3. PATH 裡的 yt-dlp

模組也走子行程而不是在程式內呼叫，所以所有路徑的參數、進度解析、取消
（終止行程）行為完全相同，不會再有「備援路徑功能殘缺」的問題。
"""
import importlib.metadata
import importlib.util
import os
import re
import shutil
import subprocess
from collections import namedtuple

from modules import utils

Engine = namedtuple("Engine", "kind command label")

JS_RUNTIME_NAMES = ("deno", "node", "bun", "qjs")


def resolve_engine(base_dir):
    local = os.path.join(base_dir, "yt-dlp.exe")
    if os.path.isfile(local):
        return Engine("local_exe", [local], f"本機 yt-dlp.exe（{local}）")

    python = utils.get_console_python()
    if python and importlib.util.find_spec("yt_dlp") is not None:
        return Engine("module", [python, "-m", "yt_dlp"], f"Python 模組（{python} -m yt_dlp）")

    path_exe = shutil.which("yt-dlp")
    if path_exe:
        return Engine("path_exe", [path_exe], f"PATH 中的 yt-dlp（{path_exe}）")
    return None


def get_version(eng, timeout=30):
    """回傳目前引擎的 yt-dlp 版本字串，取不到時回傳 None。"""
    if eng is None:
        return None
    if eng.kind == "module":
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
