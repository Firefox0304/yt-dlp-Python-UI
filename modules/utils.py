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

from modules.i18n import t

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


# 「自動開啟」絕對不該碰的檔案類型：可執行檔、腳本、捷徑、安裝檔、含巨集的 Office 檔、網頁等。
# yt-dlp 遇到直連檔案時會保留原副檔名，所以下載結果不一定是影音檔，不能直接丟給 os.startfile。
UNSAFE_OPEN_EXTENSIONS = frozenset(
    ".exe .com .scr .pif .bat .cmd .msi .msp .msc .cpl .dll .sys .drv .ocx .vxd "
    ".lnk .url .scf .library-ms .searchconnector-ms .appref-ms .application .gadget .manifest "
    ".appx .appxbundle .msix .msixbundle .jar .jnlp .hta .inf .ins .isp .job .reg .rgs .sct .shb .shs "
    ".ps1 .ps1xml .ps2 .ps2xml .psc1 .psc2 .psd1 .psm1 .vb .vbe .vbs .wsc .wsf .wsh .ws .js .jse .mjs "
    ".py .pyw .pyz .pyc .sh .bash .pl .rb .chm .xll "
    ".docm .dotm .xlsm .xltm .xlam .pptm .potm .ppam .sldm "
    ".html .htm .xhtml .mht .mhtml .svg".split())


def is_safe_to_open(path):
    """檔案可不可以交給系統預設程式開啟（只看檔名，不碰檔案內容）。

    擋掉 UNSAFE_OPEN_EXTENSIONS；Windows 會忽略檔名尾端的點與空白（``a.exe.`` 其實是 ``a.exe``），
    而 ``a.txt:b.exe`` 是 NTFS 替代資料流，兩種都一律視為不安全。沒有副檔名的檔案不算危險。
    """
    name = os.path.basename(str(path or "").strip().replace("\\", "/").rstrip("/"))
    if not name or ":" in name:
        return False
    name = name.rstrip(". ")
    return os.path.splitext(name)[1].lower() not in UNSAFE_OPEN_EXTENSIONS


def is_safe_web_url(url):
    """只接受 http／https 且有主機名稱的網址；含空白或控制字元的一律拒絕。"""
    text = str(url or "").strip()
    if not text or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in text):
        return False
    try:
        parts = urlparse(text)
        return parts.scheme in ("http", "https") and bool(parts.hostname)
    except ValueError:
        return False


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


# 找「最新的下載檔」時要略過的副檔名：下載中的暫存檔、字幕、縮圖
NON_MEDIA_SUFFIXES = (".part", ".ytdl", ".vtt", ".srt", ".jpg", ".png", ".webp")


def latest_media_file(folder):
    """資料夾（不含子資料夾）中最新修改的媒體檔路徑；沒有或讀不到回傳 None。"""
    try:
        paths = [os.path.join(folder, name) for name in os.listdir(folder)]
        paths = [p for p in paths if os.path.isfile(p) and not p.lower().endswith(NON_MEDIA_SUFFIXES)]
        return max(paths, key=os.path.getmtime) if paths else None
    except OSError:
        return None


def reveal_in_file_manager(path):
    """在檔案總管中「選取」這個檔案（只是選取，不會執行它）。失敗時回傳 False。"""
    path = os.path.normpath(str(path))
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", path])  # explorer 不論成功與否結束代碼都是 1，所以不檢查
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            return open_path(os.path.dirname(path))
        return True
    except Exception:  # noqa: BLE001
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
        return t("util.platform_version", name=name, version=version) if version else name

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

    return f"{system or t('util.unknown_system')} {release}".strip()


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
