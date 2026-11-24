# modules/check.py
"""v7.0 啟動檢查與自動安裝。

流程：視窗顯示後，在背景執行緒偵測缺少的核心程式（Python、yt-dlp、FFmpeg、Deno）與 yt-dlp 新版；
有項目時通知網頁跳出彈窗，使用者按「確認」後才安裝（winget／pip），完成後自動重新啟動。

這個檔案負責：偵測缺少、安裝執行緒與輸出階段解析、重新讀取 PATH、重新啟動。
網頁溝通（confirm_install 等）在 modules/webui.py，版本查詢在 modules/updater.py。
"""
import os
import re
import shutil
import subprocess
import sys
from collections import deque

from modules import channel, downloader, logger, updater, utils
from modules import engine as engine_mod
from modules.i18n import t

LOG = logger.get()

# 安裝順序：yt-dlp 的 pip 必須等 Python 就緒，所以 Python 一定在最前面
ITEM_ORDER = ("python", "ytdlp", "ejs", "ffmpeg", "deno")
ACTIONS = ("install", "upgrade")
WINGET_ITEMS = frozenset({"python", "ffmpeg", "deno"})
WINGET_FLAGS = ("--accept-source-agreements", "--accept-package-agreements")
# winget 的結束代碼：0 成功、已經安裝了、沒有可套用的更新，都算成功
WINGET_OK_CODES = frozenset({0, 0x8A150061, 0x8A15002B})
DEFAULT_PYTHON_SERIES = "3.13"
DOWNLOAD_PAGES = {
    "python": updater.PYTHON_DOWNLOADS_URL,
    "ffmpeg": downloader.FFMPEG_DOWNLOAD_URL,
    "deno": updater.DENO_DOWNLOADS_URL,
}
LAUNCHER_NAME = "yt-dlp-Python-UI.pyw"
_SERIES_RE = re.compile(r"^\d+\.\d+$")


# --------------------------------------------------------------------------- #
# PATH：剛裝完的程式不在舊程序的環境裡，要從登錄檔重新讀取
# --------------------------------------------------------------------------- #

def merge_path(registry_paths, current_path):
    """合併 PATH：登錄檔（系統在前、使用者在後）優先，目前環境裡多出來的項目（例如虛擬環境）接在最後。"""
    parts, seen = [], set()
    for chunk in (*registry_paths, current_path):
        for part in str(chunk or "").split(os.pathsep):
            part = part.strip()
            key = part.lower().rstrip("\\/")
            if not part or key in seen:
                continue
            seen.add(key)
            parts.append(part)
    return os.pathsep.join(parts)


def read_registry_paths():
    """讀取 Windows 登錄檔裡最新的系統與使用者 PATH（已展開 %變數%）；非 Windows 或讀不到回傳空清單。"""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    found = []
    for root, sub in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, "Environment")):
        try:
            with winreg.OpenKey(root, sub) as key:
                value, _kind = winreg.QueryValueEx(key, "Path")
        except OSError:
            continue
        found.append(os.path.expandvars(str(value)))
    return found


def refresh_path(read=read_registry_paths, environ=None):
    """把最新的 PATH 寫回目前程序的環境（之後啟動的子行程與新程序都會繼承），並回傳新的 PATH。"""
    environ = os.environ if environ is None else environ
    merged = merge_path(read(), environ.get("PATH", ""))
    if merged:
        environ["PATH"] = merged
    return environ.get("PATH", "")


# --------------------------------------------------------------------------- #
# 缺少偵測與項目規劃
# --------------------------------------------------------------------------- #

def detect_missing(base_dir):
    """回傳缺少的項目 id 集合（Python、yt-dlp、FFmpeg、Deno）。"""
    missing = set()
    if not utils.get_console_python():
        missing.add("python")
    eng = engine_mod.resolve_engine(base_dir)
    if eng is None:
        missing.update(("ytdlp", "ejs"))  # yt-dlp 會以 pip 模組安裝，yt-dlp-ejs 一併需要
    elif updater.ejs_applies(eng) and updater.check_ejs(eng, check_upgrade=False)["status"] == "missing":
        missing.add("ejs")
    if not downloader.find_ffmpeg(base_dir):
        missing.add("ffmpeg")
    if not updater.find_deno(base_dir):
        missing.add("deno")
    return missing


def plan_items(missing, updates):
    """依安裝順序排出要處理的項目 [{"id", "action"}]。缺少（install）優先於更新（upgrade）；Python 不提示更新。"""
    items = []
    for ident in ITEM_ORDER:
        if ident in missing:
            items.append({"id": ident, "action": "install"})
        elif ident in updates and ident != "python":
            items.append({"id": ident, "action": "upgrade"})
    return items


def sanitize_items(items):
    """網頁傳來的項目只認白名單（id、action 都固定），重複與不合理的組合會被丟掉。"""
    missing, updates = set(), set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        ident, action = item.get("id"), item.get("action")
        if ident in ITEM_ORDER and action == "install":
            missing.add(ident)
        elif ident in ITEM_ORDER and action == "upgrade":
            updates.add(ident)
    return plan_items(missing, updates)


def needs_winget(items):
    return any(item["id"] in WINGET_ITEMS for item in items)


def collect_startup_items(base_dir):
    """啟動時的檢查：缺少的項目＋yt-dlp 有新版（有缺少 yt-dlp 時，安裝下來本來就是最新版）。"""
    refresh_path()
    missing = detect_missing(base_dir)
    updates = set()
    eng = engine_mod.resolve_engine(base_dir)
    if "ytdlp" not in missing:
        try:
            if updater.check_startup(eng)["status"] == "update_available":
                updates.add("ytdlp")
        except Exception as exc:  # noqa: BLE001 — 離線等情況不打擾使用者
            LOG.warning("Startup yt-dlp update check failed: %s", exc)
    if "ejs" not in missing and updater.ejs_applies(eng):
        try:
            if updater.check_ejs(eng)["status"] == "update_available":
                updates.add("ejs")
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Startup yt-dlp-ejs update check failed: %s", exc)
    return plan_items(missing, updates)


def items_from_results(results):
    """「檢查更新」按鈕的結果：只收「有新版」的項目（缺少偵測在按鈕不另外處理）。"""
    updates = set()
    if (results.get("ytdlp") or {}).get("status") == "update_available":
        updates.add("ytdlp")
    if (results.get("ejs") or {}).get("status") == "update_available":
        updates.add("ejs")
    if (results.get("ffmpeg") or {}).get("update"):
        updates.add("ffmpeg")
    if (results.get("deno") or {}).get("update"):
        updates.add("deno")
    return plan_items(set(), updates)


def collect_startup_ui(check=None):
    """啟動時查 UI（本程式）有沒有新版：有才回傳 {"current", "latest"}，已是最新或查不到回傳 None。"""
    result = (check or updater.check_ui)()
    if result.get("status") == "update_available":
        return {"current": result["current"], "latest": result["latest"]}
    return None


def run_startup_check(base_dir, deliver, on_beta=None):
    """背景執行緒入口：有要處理的項目或 UI 新版時呼叫 deliver(items, ui)，都沒有就什麼都不做（不跳任何彈窗）。
    on_beta(是否為 Beta) 在 UI 版本查完後一定會呼叫一次（不影響是否跳彈窗）。

    兩項檢查互不影響：缺少／yt-dlp 檢查失敗時仍會通知 UI 新版，反過來也一樣。
    """
    try:
        items = collect_startup_items(base_dir)
    except Exception:  # noqa: BLE001
        LOG.exception("Startup check failed")
        items = []
    try:
        ui = collect_startup_ui()
    except Exception as exc:  # noqa: BLE001 — 離線等情況不打擾使用者
        LOG.warning("Startup UI update check failed: %s", exc)
        ui = None
    LOG.info("Startup check items: %s; UI update: %s", items, ui)
    if on_beta is not None:  # UI 版本查完（成功或失敗都呼叫）：讓畫面依 Beta 狀態更新
        try:
            on_beta(channel.is_beta())
        except Exception:  # noqa: BLE001
            LOG.exception("on_beta callback failed")
    if items or ui:
        deliver(items, ui)


# --------------------------------------------------------------------------- #
# 指令組裝
# --------------------------------------------------------------------------- #

def python_package_id(series):
    series = str(series or "")
    return f"Python.Python.{series if _SERIES_RE.match(series) else DEFAULT_PYTHON_SERIES}"


def pick_python_series(fetch=None):
    """安裝用的 Python 版本系列：用查到的最新系列，查不到就用預設。"""
    try:
        overall = (fetch or updater.fetch_python_info)().get("overall")
        if overall:
            series = ".".join(str(overall).split(".")[:2])
            if _SERIES_RE.match(series):
                return series
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Python version lookup failed: %s", exc)
    return DEFAULT_PYTHON_SERIES


def winget_command(winget, action, pkg_id):
    verb = "upgrade" if action == "upgrade" else "install"
    return [winget, verb, "-e", "--id", pkg_id, *WINGET_FLAGS]


def pip_command(python, action, package="yt-dlp"):
    cmd = [python, "-m", "pip", "install"]
    if action == "upgrade":
        cmd.append("-U")
    return cmd + [package, "--disable-pip-version-check"]


# --------------------------------------------------------------------------- #
# 輸出階段解析（pip 與 winget 沒有精準百分比，依輸出階段推進）
# --------------------------------------------------------------------------- #

_PIP_STAGES = (
    (re.compile(r"^Successfully installed"), 1.0),
    (re.compile(r"^Requirement already satisfied"), 0.9),
    (re.compile(r"^Installing collected packages"), 0.8),
    (re.compile(r"^\s*Downloading "), 0.4),
    (re.compile(r"^Collecting "), 0.15),
)
_WINGET_STAGES = (
    (re.compile(r"Successfully installed|已成功安裝|成功安裝", re.I), 1.0),
    (re.compile(r"Starting package install|開始安裝套件", re.I), 0.75),
    (re.compile(r"Successfully verified installer hash|已成功驗證安裝程式雜湊", re.I), 0.65),
    (re.compile(r"Downloading|正在下載", re.I), 0.3),
    (re.compile(r"^Found |已找到", re.I), 0.1),
)
_PERCENT_RE = re.compile(r"(\d{1,3})\s*%")


def stage_progress(kind, line, current=0.0):
    """依一行輸出回傳新的階段進度（0～1，只會前進不會倒退）。kind：pip、winget；其他種類沒有階段，維持原值。"""
    found = current
    if kind == "pip":
        for pattern, value in _PIP_STAGES:
            if pattern.search(line):
                found = value
                break
    elif kind == "winget":
        for pattern, value in _WINGET_STAGES:
            if pattern.search(line):
                found = value
                break
        percent = _PERCENT_RE.search(line)
        if percent and found < 0.65:  # 下載中的百分比：對應到 0.3～0.65
            found = max(found, 0.3 + 0.35 * min(100, int(percent.group(1))) / 100)
    return max(current, min(1.0, found))


def overall_progress(index, total, fraction):
    """整體進度（0～100）：index 是目前項目的序號（從 0 起算），fraction 是該項目的階段進度。"""
    if total <= 0:
        return 0.0
    return round(100.0 * (index + max(0.0, min(1.0, fraction))) / total, 1)


def command_succeeded(kind, code):
    if kind == "winget":
        return (code & 0xFFFFFFFF) in WINGET_OK_CODES
    return code == 0


def _tail_text(lines, limit=600):
    useful = [line for line in lines if re.search(r"\w", line)]
    return "\n".join(useful[-6:])[-limit:]


# --------------------------------------------------------------------------- #
# 安裝執行
# --------------------------------------------------------------------------- #

class InstallError(Exception):
    """安裝失敗。item：失敗的項目 id；pages：可提供下載頁的項目 id（winget 不可用時用）。"""

    def __init__(self, item, message, pages=()):
        super().__init__(message)
        self.item = item
        self.pages = tuple(pages)


def run_command(cmd, kind, on_progress, popen=subprocess.Popen):
    """執行指令並即時解析輸出；回傳 (結束代碼, 最後幾行輸出)。"""
    proc = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                 text=True, encoding="utf-8", errors="replace", env=utils.child_env(),
                 **utils.hidden_subprocess_kwargs())
    fraction, tail = 0.0, deque(maxlen=12)
    for raw in proc.stdout:
        line = raw.strip()
        if not line:
            continue
        tail.append(line)
        new = stage_progress(kind, line, fraction)
        if new != fraction:
            fraction = new
            on_progress(fraction)
    return proc.wait(), list(tail)


def _build_step(base_dir, item, winget):
    """回傳 (指令, 種類)；種類決定輸出階段怎麼解析與結束代碼怎麼判斷。"""
    ident, action = item["id"], item["action"]
    if ident == "python":
        return winget_command(winget, "install", python_package_id(pick_python_series())), "winget"
    if ident in updater.WINGET_IDS:
        return winget_command(winget, action, updater.WINGET_IDS[ident]), "winget"
    # yt-dlp、yt-dlp-ejs：安裝到 PATH 的 Python（Python 剛裝好時 PATH 已重新讀取）
    if ident == "ejs":
        eng = engine_mod.resolve_engine(base_dir)
        python = eng.command[0] if updater.ejs_applies(eng) else utils.get_console_python()
        if not python:
            raise InstallError("ejs", t("check.err.no_python"))
        return pip_command(python, action, updater.EJS_PACKAGE), "pip"
    if action == "upgrade":
        eng = engine_mod.resolve_engine(base_dir)
        if eng is not None and eng.kind != "module":
            return updater.build_update_command(eng), "other"  # 本機 yt-dlp.exe／PATH 上的執行檔：自我更新
        python = eng.command[0] if eng is not None else utils.get_console_python()
    else:
        python = utils.get_console_python()
    if not python:
        raise InstallError("ytdlp", t("check.err.no_python"))
    return pip_command(python, action), "pip"


def run_install(base_dir, items, emit, runner=run_command):
    """依序安裝（Python → yt-dlp → FFmpeg → Deno），每一步回報項目與進度。

    成功回傳 None；任何一項失敗丟出 InstallError（不會繼續後面的項目）。
    emit 以關鍵字參數呼叫，例如 emit(type="install_progress", item="python", ...)。
    """
    items = sanitize_items(items)
    if not items:
        raise InstallError(None, t("check.err.no_items"))
    winget = None
    if needs_winget(items):
        winget = shutil.which("winget") if utils.winget_available() else None
        if not winget:
            pages = [item["id"] for item in items if item["id"] in WINGET_ITEMS]
            raise InstallError(pages[0], t("check.err.no_winget"), pages)
    total = len(items)
    for index, item in enumerate(items):
        ident, action = item["id"], item["action"]

        def progress(fraction, _index=index, _ident=ident, _action=action):
            emit(type="install_progress", item=_ident, action=_action, index=_index + 1, total=total,
                 pct=overall_progress(_index, total, fraction))

        progress(0.0)
        cmd, kind = _build_step(base_dir, item, winget)
        LOG.info("Installing %s (%s): %s", ident, action, cmd)
        try:
            code, tail = runner(cmd, kind, progress)
        except OSError as exc:
            raise InstallError(ident, str(exc)) from exc
        if not command_succeeded(kind, code):
            LOG.warning("Install of %s failed with code %s: %s", ident, code, tail)
            detail = t("check.err.cmd_fail", code=code)
            extra = _tail_text(tail)
            raise InstallError(ident, detail + ("\n" + extra if extra else ""))
        refresh_path()  # 剛裝好的程式要讓後面的步驟（例如用新 Python 跑 pip）找得到


# --------------------------------------------------------------------------- #
# 重新啟動
# --------------------------------------------------------------------------- #

def build_restart_command(base_dir, frozen=None, executable=None, argv0=None, isfile=os.path.isfile):
    """exe 版重啟自己；.pyw 版用 pythonw.exe 重新執行啟動器。"""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    exe = executable or sys.executable
    if frozen:
        return [exe]
    if os.path.basename(exe).lower() == "python.exe":
        windowless = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if isfile(windowless):
            exe = windowless
    script = os.path.abspath(argv0 or (sys.argv[0] if sys.argv else ""))
    if not isfile(script):
        script = os.path.join(base_dir, LAUNCHER_NAME)
    return [exe, script]


def restart(base_dir, popen=subprocess.Popen):
    """用 Popen 啟動新程序（帶著剛重新讀取的 PATH）。呼叫端接著關閉目前視窗。"""
    refresh_path()
    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"  # 單一 exe 重啟自己時，不要沿用舊程序的解壓縮資料夾
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    popen(build_restart_command(base_dir), cwd=base_dir, env=env, creationflags=flags,
          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
