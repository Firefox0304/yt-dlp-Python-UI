# modules/utils.py
"""共用小工具（只依賴標準函式庫，啟動器在安裝依賴前也能安全匯入）。"""
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlparse

IMG_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif")

_BILIBILI_HOSTS = ("bilibili.com", "bilibili.tv", "b23.tv", "biliintl.com")


def hidden_subprocess_kwargs():
    """Windows 下讓子行程不跳出主控台視窗；其他平台回傳空 dict。"""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": startupinfo, "creationflags": subprocess.CREATE_NO_WINDOW}


def child_env():
    """子行程環境：強制 UTF-8，避免繁體中文標題在管線中變成亂碼。"""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def get_console_python():
    """回傳可用來執行 `-m pip` / `-m yt_dlp` 的 python 路徑。

    GUI 由 pythonw.exe 啟動時，pythonw 沒有 stdout，子行程輸出會遺失，
    所以改用同資料夾的 python.exe（搭配 CREATE_NO_WINDOW 不會有黑視窗）。
    """
    exe = sys.executable or ""
    if getattr(sys, "frozen", False):
        return shutil.which("python") or shutil.which("python3")
    if os.path.basename(exe).lower() == "pythonw.exe":
        console = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.isfile(console):
            return console
    return exe or shutil.which("python") or shutil.which("python3")


def is_bilibili_url(url):
    """判斷網址是否屬於 Bilibili（只有這些網站才需要附上 bilibili 的 Origin/Referer）。"""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in _BILIBILI_HOSTS)


def open_path(path):
    """用系統預設程式開啟檔案或資料夾。失敗時回傳 False。"""
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


def atomic_write_json(path, data, **dump_kwargs):
    """先寫入暫存檔再 os.replace()，程式中途被關閉也不會留下半個 JSON。"""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, **dump_kwargs)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def find_app_icon(base_dir):
    cand = os.path.join(base_dir, "icon", "app_icon.ico")
    return cand if os.path.exists(cand) else None


def find_title_banner(base_dir):
    """尋找標題橫幅圖片（icon/banner.png|jpg|jpeg|gif）。"""
    for ext in ("png", "jpg", "jpeg", "gif"):
        p = os.path.join(base_dir, "icon", f"banner.{ext}")
        if os.path.exists(p):
            return p
    return None


# --------------------------------------------------------------------------- #
# 系統平台描述（系統診斷用）
# --------------------------------------------------------------------------- #

_WINDOWS_NT_NAMES = {
    (6, 3): "Windows 8.1", (6, 2): "Windows 8", (6, 1): "Windows 7",
    (6, 0): "Windows Vista", (5, 2): "Windows XP", (5, 1): "Windows XP",
}


def _windows_name(release, version):
    """由內部版本號判斷 Windows 名稱。

    Windows 11 的內部版本仍是 10.0（組建編號 22000 以上才是 11），而且較舊的 Python
    會把它回報成 "10"，所以不能只看 platform.release()。
    """
    release = release or ""
    if "server" in release.lower():  # 例如 "2022Server"
        year = re.match(r"\d+", release)
        return f"Windows Server {year.group(0)}" if year else "Windows Server"
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version or "")
    if match:
        major, minor, build = (int(g) for g in match.groups())
        if major == 10:
            return "Windows 11" if build >= 22000 else "Windows 10"
        if (major, minor) in _WINDOWS_NT_NAMES:
            return _WINDOWS_NT_NAMES[(major, minor)]
    return f"Windows {release}".strip()


def _read_os_release():
    """讀取 Linux 的 os-release；讀不到就回傳空 dict。"""
    try:
        return dict(platform.freedesktop_os_release())  # Python 3.10+
    except (OSError, AttributeError):
        pass
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            data = {}
            with open(path, encoding="utf-8") as f:
                for line in f:
                    key, sep, value = line.strip().partition("=")
                    if sep:
                        data[key] = value.strip().strip('"')
            return data
        except OSError:
            continue
    return {}


def describe_platform(system=None, release=None, version=None, os_release=None, mac_version=None):
    """回傳易讀的系統平台描述，例如：

    Windows 11（版本號 10.0.22631）／Ubuntu Linux 24.04／macOS 14.5

    參數只是為了測試時可以注入；平常不需要傳。
    """
    system = system if system is not None else platform.system()
    release = release if release is not None else platform.release()
    version = version if version is not None else platform.version()

    if system == "Windows":
        name = _windows_name(release, version)
        return f"{name}（版本號 {version}）" if version else name

    if system == "Darwin":
        mac = mac_version if mac_version is not None else platform.mac_ver()[0]
        return f"macOS {mac}".strip()

    if system == "Linux":
        info = os_release if os_release is not None else _read_os_release()
        name = info.get("NAME")
        if not name:
            return f"Linux {release}".strip()
        if "linux" not in name.lower():
            name += " Linux"
        ver = info.get("VERSION_ID") or ""
        return f"{name} {ver}".strip()

    return f"{system or '未知系統'} {release}".strip()


def winget_available(timeout=10):
    """Windows 且 winget 能實際執行時回傳 True（只有找到檔案但執行失敗也算不可用）。"""
    if os.name != "nt":
        return False
    exe = shutil.which("winget")
    if not exe:
        return False
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True,
                              timeout=timeout, **hidden_subprocess_kwargs())
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0
