# modules/bootstrap.py
"""啟動流程：單一實例檢查 → 啟動畫面 → 檢查／安裝依賴 → 載入模組 → 建立主視窗。

這個模組只依賴標準函式庫，所以啟動畫面可以在 customtkinter 等套件載入之前就顯示出來。
"""
import importlib
import importlib.util
import os
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from tkinter import messagebox

from modules import fonts, instance, splash, utils, venv_support
from modules.version import APP_NAME, APP_VERSION

# pip 套件名稱 -> 匯入名稱
REQUIRED_PACKAGES = {
    "customtkinter": "customtkinter",
    "yt-dlp": "yt_dlp",
    "pillow": "PIL",
}

# install_missing 的結果
OK, ABORT, RELAUNCH = "ok", "abort", "relaunch"


# --------------------------------------------------------------------------- #
# 依賴檢查與安裝
# --------------------------------------------------------------------------- #

def required_packages(base_dir=None):
    """必要的 pip 套件。程式資料夾內已有 yt-dlp.exe 時，不要求 yt-dlp 套件（engine 會直接用 exe）。"""
    has_local_ytdlp = bool(base_dir) and os.path.isfile(os.path.join(base_dir, "yt-dlp.exe"))
    return [pkg for pkg in REQUIRED_PACKAGES if not (pkg == "yt-dlp" and has_local_ytdlp)]


def find_missing_packages(base_dir=None):
    """回傳缺少的 pip 套件。"""
    missing = []
    for package in required_packages(base_dir):
        module = REQUIRED_PACKAGES[package]
        if module in sys.modules:
            continue
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(package)
    return missing


def _pip_install(packages):
    """回傳 (成功與否, 錯誤輸出)。"""
    python = utils.get_console_python()
    if not python:
        return False, "找不到可用的 Python"
    try:
        proc = subprocess.run(
            [python, "-m", "pip", "install", "--disable-pip-version-check", *packages],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            **utils.hidden_subprocess_kwargs(),
        )
    except OSError as exc:
        return False, str(exc)
    if proc.returncode:
        return False, (proc.stderr or proc.stdout or "").strip()[-800:]
    return True, ""


def _install_in_venv(loading, base_dir):
    """建立專用虛擬環境（.venv）並安裝所有必要套件，成功後要求用它重新啟動。"""
    loading.show()
    loading.set_progress(8, "正在建立專用虛擬環境並安裝必要組件（需要網路連線）")
    ok, detail = run_in_background(
        loading, venv_support.create_and_install, base_dir, required_packages(base_dir))
    if not ok:
        loading.hide()
        messagebox.showerror(
            "安裝失敗",
            f"{detail}\n\n也可以手動執行：\npython3 -m venv .venv\n.venv/bin/pip install -r requirements.txt",
            parent=loading.root,
        )
        return ABORT, None
    return RELAUNCH, venv_support.venv_python(base_dir, gui=True)


def install_missing(loading, missing, base_dir=None):
    """詢問並安裝缺少的套件。回傳 (OK | ABORT | RELAUNCH, 要用來重新啟動的 python)。

    系統 Python 被標記為「外部管理」（PEP 668，例如 Debian／Ubuntu）時不能直接 pip install，
    改為在程式資料夾建立專用虛擬環境，裝好後用它重新啟動。
    """
    externally_managed = venv_support.is_externally_managed()
    loading.hide()  # 置頂的啟動畫面會把對話框蓋住
    if externally_managed:
        question = (
            f"偵測到缺少以下必要組件：\n{', '.join(missing)}\n\n"
            "這個系統的 Python 由作業系統管理，不允許直接安裝套件。\n"
            f"是否改為在程式資料夾建立專用的虛擬環境（{venv_support.VENV_NAME}）並安裝？\n"
            "（不會動到系統的 Python，之後會自動使用它啟動）")
    else:
        question = (
            f"偵測到您的電腦缺少以下必要組件：\n{', '.join(missing)}\n\n"
            "是否立即自動下載並安裝？\n(這將確保程式能正常啟動)")
    if not messagebox.askyesno("環境檢查", question, parent=loading.root):
        messagebox.showwarning("警告", "缺少必要組件，程式無法啟動。", parent=loading.root)
        return ABORT, None
    if externally_managed:
        return _install_in_venv(loading, base_dir)

    loading.show()
    loading.set_progress(8, "正在安裝必要組件（需要網路連線）")
    ok, detail = run_in_background(loading, _pip_install, missing)
    if not ok:
        loading.hide()
        if venv_support.is_externally_managed_error(detail):  # 事前偵測沒抓到時的補強
            if messagebox.askyesno(
                    "環境檢查",
                    "系統拒絕直接安裝套件（這個 Python 由作業系統管理）。\n"
                    f"是否改為在程式資料夾建立專用的虛擬環境（{venv_support.VENV_NAME}）並安裝？",
                    parent=loading.root):
                return _install_in_venv(loading, base_dir)
            messagebox.showwarning("警告", "缺少必要組件，程式無法啟動。", parent=loading.root)
            return ABORT, None
        messagebox.showerror(
            "安裝失敗",
            f"自動安裝失敗：\n{detail}\n\n請手動執行：pip install -r requirements.txt",
            parent=loading.root,
        )
        return ABORT, None

    importlib.invalidate_caches()
    if find_missing_packages(base_dir):
        loading.hide()
        messagebox.showerror("安裝失敗", "套件已安裝，但仍無法匯入。請關閉程式後重新開啟。", parent=loading.root)
        return ABORT, None
    loading.show()
    return OK, None


# --------------------------------------------------------------------------- #
# 背景執行 + 啟動畫面動畫
# --------------------------------------------------------------------------- #

def run_in_background(loading, func, *args):
    """在背景執行緒執行 func，同時由主執行緒持續更新啟動畫面。

    這樣載入大型套件（例如 customtkinter）時，畫面不會卡住。
    func 內若拋出例外，會在主執行緒重新拋出。
    """
    box = {}
    done = threading.Event()

    def target():
        try:
            box["result"] = func(*args)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=target, daemon=True).start()
    while not done.is_set():
        loading.refresh()
        done.wait(0.016)
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _load_modules(base_dir, loading):
    """（背景執行緒）匯入主程式需要的所有套件與模組，並完成日誌與設定的初始化。"""
    loading.set_progress(20, "正在載入介面元件")
    import customtkinter  # noqa: F401
    loading.set_progress(40, "正在載入影像元件")
    import PIL.Image  # noqa: F401
    import PIL.ImageTk  # noqa: F401
    loading.set_progress(55, "正在載入程式模組")
    from modules import config_manager, logger, ui  # noqa: F401
    loading.set_progress(70, "正在讀取設定")
    os.makedirs(os.path.join(base_dir, "Download"), exist_ok=True)
    logger.setup_logging(os.path.join(base_dir, "logs"))
    cfg = config_manager.load_or_init(os.path.join(base_dir, "config", "settings.json"))
    loading.set_progress(78, "正在建立主視窗")
    return cfg


# --------------------------------------------------------------------------- #
# 與主視窗的銜接
# --------------------------------------------------------------------------- #

class StartupHooks:
    """交給 ui.App 的啟動回呼：回報建立進度、主視窗完成時收起啟動畫面。"""

    def __init__(self, loading, single):
        self.loading = loading
        self.single = single

    def attach(self, root):
        self.loading.adopt(root)

    def progress(self, value, text):
        self.loading.set_progress(value, text)
        self.loading.refresh()

    def ready(self, app):
        self.loading.close()
        if self.single is not None:
            app.poll_hooks.append(lambda: self._raise_if_requested(app))

    def _raise_if_requested(self, app):
        """有人再次點開程式時，把已開啟的視窗拉到最前面。"""
        if not self.single.consume_activation():
            return
        root = app.root
        try:
            if root.state() == "iconic":
                root.deiconify()
            root.lift()
            root.attributes("-topmost", True)
            root.after(300, lambda: root.attributes("-topmost", False))
            root.focus_force()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def _enable_dpi_awareness():
    """在建立任何視窗之前啟用高 DPI，啟動畫面與主視窗才會一致且清晰。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def _create_splash():
    try:
        return splash.Splash(APP_NAME, APP_VERSION)
    except Exception:  # noqa: BLE001 — 啟動畫面失敗不應該讓程式打不開
        return splash.NullSplash()


def show_startup_error(base_dir, exc):
    # pythonw 沒有主控台：完整錯誤寫進檔案，方便回報問題。
    try:
        os.makedirs(os.path.join(base_dir, "logs"), exist_ok=True)
        with open(os.path.join(base_dir, "logs", "startup_error.log"), "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
    except OSError:
        pass
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("啟動錯誤", f"程式啟動失敗：{exc}\n\n詳細資訊已寫入 logs/startup_error.log", parent=root)
        root.destroy()
    except Exception:  # noqa: BLE001
        pass


def _start(base_dir, loading, single):
    loading.set_progress(3, "正在檢查執行環境")
    loading.refresh()
    missing = find_missing_packages(base_dir)
    if missing:
        status, python = install_missing(loading, missing, base_dir)
        if status == ABORT:
            return None
        if status == RELAUNCH:
            return python  # 交給 main：先關閉啟動畫面、釋放鎖，再用虛擬環境的 Python 重新啟動

    cfg = run_in_background(loading, _load_modules, base_dir, loading)

    from modules import ui
    ui.launch_ui(cfg, base_dir, startup=StartupHooks(loading, single))
    return None


def main(base_dir):
    venv_support.maybe_reexec(base_dir)  # 程式資料夾有專用 .venv 時，改用它執行（在建立任何視窗之前）
    _enable_dpi_awareness()
    fonts.register(base_dir, fonts.read_mode(base_dir))  # 在建立任何視窗之前註冊字型，啟動畫面也能使用
    single = instance.SingleInstance(base_dir)
    if not single.acquire():
        return  # 已有一份在啟動或執行中，已通知它把視窗拉到前面

    loading = _create_splash()
    relaunch_python = None
    try:
        relaunch_python = _start(base_dir, loading, single)
    except Exception as exc:  # noqa: BLE001
        loading.close()
        show_startup_error(base_dir, exc)
    finally:
        loading.close()
        single.release()
    if relaunch_python:
        venv_support.relaunch(relaunch_python)
