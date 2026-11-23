"""yt-dlp-python-UI 啟動器（網頁介面版，僅支援 Windows）。

介面是 web/ 內的 HTML；後端仍是 Python 的 modules/。這個檔案只負責：
找到程式資料夾、pywebview 缺失（連視窗都建不出來）時自動 pip 安裝與 MessageBoxW 提示，然後啟動 modules.webui。
其餘缺少的程式（Python、yt-dlp、FFmpeg、Deno）與更新，由視窗顯示後的 modules/check.py 彈窗流程處理。
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


def tr(key, fallback):
    """啟動器的少數訊息也依介面語言顯示：設定檔的 language，沒有就看 Windows 顯示語言；任何一步失敗就用中文備用文字。"""
    try:
        import json
        from modules import i18n
        i18n.init(BASE_DIR)
        setting = "auto"
        try:
            with open(os.path.join(BASE_DIR, "config", "settings.json"), "r", encoding="utf-8") as f:
                setting = json.load(f).get("language", "auto")
        except (OSError, ValueError, AttributeError):
            pass
        i18n.set_language(i18n.resolve(setting))
        text = i18n.t(key)
        return fallback if text == key else text
    except Exception:  # noqa: BLE001
        return fallback


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
        show_error(tr("launcher.no_webview", "找不到 pywebview，自動安裝也失敗。\n請在命令提示字元執行：\n  pip install pywebview"))
        sys.exit(1)
    try:
        from modules import webui
        webui.main(BASE_DIR)
    except Exception:  # noqa: BLE001
        show_error(tr("launcher.start_failed", "程式啟動失敗：") + "\n\n" + traceback.format_exc()[-1500:])
        sys.exit(1)
