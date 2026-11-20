"""yt-dlp-python-UI v6.0 啟動器（網頁介面版，僅支援 Windows）。

介面是 web/ 內的 HTML；後端仍是 modules/ 內的 Python。這個檔案只負責：
找到程式資料夾、缺少 pywebview 時自動安裝，然後啟動 modules.webui。
"""
import os
import subprocess
import sys
import traceback

BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
    else os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def show_error(text):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "yt-dlp-python-UI", 0x10)
    except Exception:  # noqa: BLE001
        if sys.stderr:
            sys.stderr.write(text + "\n")


def ensure_webview():
    try:
        import webview  # noqa: F401
        return True
    except ImportError:
        pass
    python = sys.executable
    if python.lower().endswith("pythonw.exe"):  # 安裝過程不需要視窗，但 pip 要用一般的 python.exe
        console = python[:-len("pythonw.exe")] + "python.exe"
        python = console if os.path.isfile(console) else python
    try:
        subprocess.run([python, "-m", "pip", "install", "pywebview"], timeout=600,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True)
        import importlib
        importlib.invalidate_caches()
        import webview  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    if not ensure_webview():
        show_error("找不到 pywebview，自動安裝也失敗。\n請在命令提示字元執行：\n  pip install pywebview")
        sys.exit(1)
    try:
        from modules import webui
        webui.main(BASE_DIR)
    except Exception:  # noqa: BLE001
        show_error("程式啟動失敗：\n\n" + traceback.format_exc()[-1500:])
        sys.exit(1)
