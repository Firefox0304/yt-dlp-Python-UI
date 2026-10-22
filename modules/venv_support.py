# modules/venv_support.py
"""專用虛擬環境（.venv）：系統 Python 不允許直接 pip install 時的解法（只依賴標準函式庫）。

Debian／Ubuntu（以及 Homebrew 等）的系統 Python 依 PEP 668 被標記為「外部管理」，直接 pip install 會被拒絕
（error: externally-managed-environment）。正確做法不是硬塞（--break-system-packages 可能弄壞系統），
而是在程式資料夾建立專用虛擬環境，依賴只裝在那裡；之後每次啟動，程式會自動改用它重新執行。
"""
import os
import shutil
import subprocess
import sys
import sysconfig

from modules import utils

VENV_NAME = ".venv"
ENV_FLAG = "YTDLP_UI_IN_VENV"  # 重新啟動時設定，避免任何原因造成的無限重啟迴圈


def venv_dir(base_dir):
    return os.path.join(base_dir, VENV_NAME)


def venv_python(base_dir, gui=False):
    """專用虛擬環境的 Python 路徑；不存在（或連結已失效）時回傳 None。

    gui=True 在 Windows 回傳 pythonw.exe（不跳出主控台）；其他平台沒有差別。
    """
    if os.name == "nt":
        names = ("pythonw.exe", "python.exe") if gui else ("python.exe",)
        candidates = [os.path.join(venv_dir(base_dir), "Scripts", n) for n in names]
    else:
        candidates = [os.path.join(venv_dir(base_dir), "bin", "python")]
    for path in candidates:
        if os.path.isfile(path):  # 失效的符號連結 isfile 為 False
            return path
    return None


def running_in_venv(base_dir):
    """目前是不是正在用這個程式資料夾的 .venv 執行。"""
    if sys.prefix == sys.base_prefix:
        return False
    return os.path.realpath(sys.prefix) == os.path.realpath(venv_dir(base_dir))


def is_externally_managed():
    """系統 Python 是否被標記為「外部管理」（PEP 668），也就是不能直接 pip install。"""
    if sys.prefix != sys.base_prefix:  # 已在虛擬環境中，可以放心 pip install
        return False
    marker = os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED")
    return os.path.isfile(marker)


def is_externally_managed_error(output):
    """pip 的輸出是不是「外部管理環境」的拒絕訊息（用來補強事前偵測）。"""
    return "externally-managed-environment" in (output or "").lower()


def _run(cmd, timeout=900):
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, **utils.hidden_subprocess_kwargs(),
    )


def _tail(proc, limit=800):
    return ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()[-limit:]


def create_and_install(base_dir, packages, python=None):
    """建立 .venv 並安裝 packages。回傳 (是否成功, 失敗時給使用者看的訊息)。

    虛擬環境是隔離的（看不到系統已安裝的套件），所以 packages 必須是完整的必要套件清單。
    """
    python = python or utils.get_console_python()
    if not python:
        return False, "找不到可用的 Python。"
    vdir = venv_dir(base_dir)

    if venv_python(base_dir) is None:  # 不存在，或上次建立到一半留下的殘缺資料夾
        shutil.rmtree(vdir, ignore_errors=True)
        try:
            proc = _run([python, "-m", "venv", vdir])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"無法建立虛擬環境：{exc}"
        if proc.returncode or venv_python(base_dir) is None:
            shutil.rmtree(vdir, ignore_errors=True)  # 清掉殘缺的資料夾，下次才能重新來過
            detail = _tail(proc)
            if "ensurepip" in detail.lower() or "python3-venv" in detail.lower():
                return False, ("缺少 python3-venv 套件，無法建立虛擬環境。\n"
                               "Debian／Ubuntu 請先執行：sudo apt install python3-venv python3-full\n\n" + detail)
            return False, f"無法建立虛擬環境：\n{detail}"

    try:
        proc = _run([venv_python(base_dir), "-m", "pip", "install", "--disable-pip-version-check", *packages])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"無法在虛擬環境中安裝套件：{exc}"
    if proc.returncode:
        return False, f"在虛擬環境中安裝套件失敗：\n{_tail(proc)}"
    return True, ""


def relaunch(python, script=None, args=None):
    """改用 python 重新執行同一支啟動檔。POSIX 直接取代目前行程；Windows 啟動新行程後結束。"""
    script = os.path.abspath(script or sys.argv[0])
    argv = [python, script] + list(sys.argv[1:] if args is None else args)
    env = os.environ.copy()
    env[ENV_FLAG] = "1"
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(argv, env=env, close_fds=True, creationflags=flags)
        sys.exit(0)
    os.execve(python, argv, env)


def maybe_reexec(base_dir):
    """程式資料夾有可用的 .venv、但目前不是用它執行時，改用它重新啟動。

    只會做一次（用環境變數防止迴圈）；.venv 壞掉或啟動失敗時直接略過，照原本的環境繼續。
    """
    if os.environ.get(ENV_FLAG) or getattr(sys, "frozen", False):
        return
    python = venv_python(base_dir, gui=True)
    if python is None or running_in_venv(base_dir):
        return
    try:
        relaunch(python)
    except OSError:
        pass
