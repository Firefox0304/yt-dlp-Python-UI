# modules/updater.py
"""版本檢查：yt-dlp（可自動更新）、Python 與 FFmpeg（只顯示版本，不更新）。

不依賴 pip 的實驗性指令，也不需要 requests。
"""
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request

from modules import downloader
from modules import engine as engine_mod
from modules import utils

PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"


def fetch_latest_version(timeout=15):
    req = urllib.request.Request(PYPI_URL, headers={"User-Agent": "yt-dlp-python-UI"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    return str(data["info"]["version"])


def _version_tuple(version):
    return tuple(int(part) for part in re.findall(r"\d+", version))


def is_newer(latest, current):
    """yt-dlp 版本是純數字的日期格式（2025.10.22[.nightly]），直接比較數字即可。"""
    try:
        return _version_tuple(latest) > _version_tuple(current)
    except ValueError:
        return latest != current


def build_update_command(eng):
    if eng.kind == "module":
        return [eng.command[0], "-m", "pip", "install", "--upgrade", "yt-dlp",
                "--disable-pip-version-check"]
    # 獨立執行檔（本機或 PATH）使用 yt-dlp 內建的自我更新
    return list(eng.command) + ["-U"]


def check_and_update(eng, log=lambda text: None):
    """檢查並更新。回傳 dict：status = up_to_date | updated，失敗時丟出 RuntimeError。"""
    if eng is None:
        raise RuntimeError("找不到 yt-dlp，請先安裝（pip install yt-dlp）或放入 yt-dlp.exe。")
    current = engine_mod.get_version(eng)
    if not current:
        raise RuntimeError("無法取得目前 yt-dlp 版本")
    log(f"目前 yt-dlp 版本：{current}")

    try:
        latest = fetch_latest_version()
    except Exception as exc:
        raise RuntimeError(f"無法查詢最新版本：{exc}") from exc

    if not is_newer(latest, current):
        return {"status": "up_to_date", "current": current, "latest": latest}

    log(f"發現新版本 {latest}，正在更新…")
    proc = subprocess.run(
        build_update_command(eng), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
        env=utils.child_env(), **utils.hidden_subprocess_kwargs(),
    )
    if proc.returncode:
        detail = (proc.stderr.strip() or proc.stdout.strip() or "yt-dlp 更新失敗")
        raise RuntimeError(detail[-600:])
    return {"status": "updated", "current": current, "latest": latest}


# --------------------------------------------------------------------------- #
# Python 版本檢查（只檢查與提示，不會自動升級 Python）
# --------------------------------------------------------------------------- #

ENDOFLIFE_URL = "https://endoflife.date/api/python.json"
PYTHON_DOWNLOADS_URL = "https://www.python.org/downloads/"
_FINAL_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _http_get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "yt-dlp-python-UI"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_python_info(timeout=15):
    """查詢 Python 各版本系列的最新修補版與結束支援日期。

    主要來源 endoflife.date；失敗時退而讀取 python.org 下載頁，只取得「目前最新版」。
    回傳 {"series_latest": {"3.13": "3.13.7"}, "eol": {"3.13": "2029-10-31" | bool}, "overall": "3.14.0" | None}。
    """
    series_latest, eol, errors = {}, {}, []
    try:
        for entry in json.loads(_http_get(ENDOFLIFE_URL, timeout)):
            cycle, latest = str(entry.get("cycle", "")), str(entry.get("latest", ""))
            if re.match(r"^\d+\.\d+$", cycle) and _FINAL_VERSION_RE.match(latest):
                series_latest[cycle] = latest
                eol[cycle] = entry.get("eol", False)
    except Exception as exc:  # noqa: BLE001 — 任何來源失敗都改用後備來源
        errors.append(str(exc))

    overall = None
    if series_latest:
        overall = max(series_latest.values(), key=_version_tuple)
    else:
        try:
            match = re.search(r"Download Python (\d+\.\d+\.\d+)", _http_get(PYTHON_DOWNLOADS_URL, timeout))
            overall = match.group(1) if match else None
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
    if not series_latest and not overall:
        raise RuntimeError("無法查詢 Python 最新版本：" + "；".join(errors or ["沒有取得資料"]))
    return {"series_latest": series_latest, "eol": eol, "overall": overall}


def _is_eol(value, today):
    if value is True:
        return True
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value) <= today
        except ValueError:
            return False
    return False


def evaluate_python(current, info, today=None):
    """比較目前版本與查詢結果（純函式，方便測試）。current 是 (major, minor, micro)。"""
    today = today or datetime.date.today()
    series = f"{current[0]}.{current[1]}"
    series_latest = info["series_latest"].get(series)
    overall = info.get("overall")
    return {
        "status": "ok",
        "current": ".".join(str(n) for n in current[:3]),
        "series": series,
        "series_latest": series_latest,
        "overall": overall,
        "patch_update": bool(series_latest and _version_tuple(series_latest) > tuple(current[:3])),
        "new_series": bool(overall and _version_tuple(overall)[:2] > tuple(current[:2])),
        "eol": _is_eol(info["eol"].get(series), today),
    }


_SERIES_RE = re.compile(r"^\d+\.\d+$")


def winget_python_command(py):
    """依 Python 檢查結果決定要讓使用者複製的 winget 命令；沒有適用的命令時回傳 None。

    回傳 (按鈕文字, 命令)。命令只用到通過格式驗證的版本系列（純數字），不會夾帶其他字元。
    """
    if py.get("status") != "ok":
        return None
    series = str(py.get("series", ""))
    if py.get("patch_update") and _SERIES_RE.match(series):
        return ("複製升級命令", f"winget upgrade --id Python.Python.{series} --exact")
    if py.get("new_series") and py.get("overall"):
        newer = ".".join(str(py["overall"]).split(".")[:2])
        if _SERIES_RE.match(newer):
            return ("複製安裝新版命令", f"winget install --id Python.Python.{newer} --exact")
    return None


def check_python(current=None, fetch=None):
    return evaluate_python(tuple(current or sys.version_info[:3]), (fetch or fetch_python_info)())


def check_ffmpeg(base_dir):
    """找出目前使用的 FFmpeg 並讀取版本（只回報，不更新）。

    回傳 {"status": "ok", "version": 版本或 None, "path": 路徑, "source": "program" | "path"}，
    或 {"status": "missing"}。
    """
    path = downloader.find_ffmpeg(base_dir)
    if not path:
        return {"status": "missing"}
    in_program_dir = os.path.normcase(os.path.dirname(path)) == os.path.normcase(os.path.abspath(base_dir))
    return {"status": "ok", "version": engine_mod.get_ffmpeg_version(path), "path": path,
            "source": "program" if in_program_dir else "path"}


def check_all(eng, log=lambda text: None, base_dir=None):
    """一次檢查 yt-dlp（有新版就更新）、Python 版本與 FFmpeg 版本；各自回報成功或錯誤，互不影響。

    FFmpeg 只顯示版本與位置，不會下載或更新（base_dir 為 None 時略過 FFmpeg）。
    """
    results = {}
    try:
        results["ytdlp"] = check_and_update(eng, log)
    except Exception as exc:  # noqa: BLE001
        results["ytdlp"] = {"status": "error", "message": str(exc)}
    log("正在檢查 Python 版本…")
    try:
        results["python"] = check_python()
        command = winget_python_command(results["python"])
        if command and utils.winget_available():  # 只有 Windows 且 winget 真的能用，才提供複製命令
            results["python"]["winget_command"] = command
    except Exception as exc:  # noqa: BLE001
        results["python"] = {"status": "error", "message": str(exc)}
    if base_dir is not None:
        log("正在檢查 FFmpeg 版本…")
        try:
            results["ffmpeg"] = check_ffmpeg(base_dir)
        except Exception as exc:  # noqa: BLE001
            results["ffmpeg"] = {"status": "error", "message": str(exc)}
    return results
