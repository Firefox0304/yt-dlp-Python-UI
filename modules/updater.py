# modules/updater.py
"""版本檢查：yt-dlp、Python、FFmpeg、Deno。

安裝與更新由 modules/check.py 的彈窗流程負責；這裡只負責「查」。Python 只顯示版本與建議。

不依賴 pip 的實驗性指令，也不需要 requests。
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request

from modules import downloader
from modules import engine as engine_mod
from modules import utils
from modules.i18n import t

PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"


def fetch_latest_version(timeout=15):
    req = urllib.request.Request(PYPI_URL, headers={"User-Agent": "yt-dlp-python-UI"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    return str(data["info"]["version"])


EJS_PACKAGE = "yt-dlp-ejs"
EJS_PYPI_URL = "https://pypi.org/pypi/yt-dlp-ejs/json"


def fetch_latest_ejs(timeout=15):
    req = urllib.request.Request(EJS_PYPI_URL, headers={"User-Agent": "yt-dlp-python-UI"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    return str(data["info"]["version"])


def ejs_applies(eng):
    """yt-dlp-ejs 是 pip 套件，只有 yt-dlp 走 Python 模組時才需要；獨立的 yt-dlp.exe 已內含，不檢查。"""
    return eng is not None and eng.kind == "module"


def get_ejs_version(python, run=subprocess.run):
    """用該 Python 讀 yt-dlp-ejs 的已安裝版本；沒裝或讀不到回傳 None。"""
    code = "import importlib.metadata as m;print(m.version('yt-dlp-ejs'))"
    try:
        proc = run([python, "-c", code], capture_output=True, text=True, timeout=20,
                   env=utils.child_env(), **utils.hidden_subprocess_kwargs())
    except (OSError, subprocess.SubprocessError):
        return None
    out = (proc.stdout or "").strip()
    return out if proc.returncode == 0 and out else None


def check_ejs(eng, check_upgrade=True, fetch=None, get_version=get_ejs_version):
    """回傳 {"status": "missing"} 或 {"status": "ok"|"update_available", "current", "latest"?}。
    查不到最新版（離線）時只回報目前版本。"""
    current = get_version(eng.command[0])
    if not current:
        return {"status": "missing"}
    result = {"status": "ok", "current": current}
    if check_upgrade:
        try:
            latest = (fetch or fetch_latest_ejs)()
        except Exception:  # noqa: BLE001
            return result
        result["latest"] = latest
        if is_newer(latest, current):
            result["status"] = "update_available"
    return result


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
        raise RuntimeError(t("upd.msg.no_engine"))
    current = engine_mod.get_version(eng)
    if not current:
        raise RuntimeError(t("upd.msg.no_version"))
    log(t("upd.log.current", current=current))

    try:
        latest = fetch_latest_version()
    except Exception as exc:
        raise RuntimeError(t("upd.msg.query_fail", error=exc)) from exc

    if not is_newer(latest, current):
        return {"status": "up_to_date", "current": current, "latest": latest}

    log(t("upd.log.found", latest=latest))
    proc = subprocess.run(
        build_update_command(eng), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
        env=utils.child_env(), **utils.hidden_subprocess_kwargs(),
    )
    if proc.returncode:
        detail = (proc.stderr.strip() or proc.stdout.strip() or t("upd.msg.update_fail"))
        raise RuntimeError(detail[-600:])
    return {"status": "updated", "current": current, "latest": latest}


def ytdlp_status(eng):
    """只查 yt-dlp 有沒有新版，不安裝。回傳 status = up_to_date | update_available；失敗時丟出 RuntimeError。"""
    if eng is None:
        raise RuntimeError(t("upd.msg.no_engine"))
    current = engine_mod.get_version(eng)
    if not current:
        raise RuntimeError(t("upd.msg.no_version"))
    try:
        latest = fetch_latest_version()
    except Exception as exc:
        raise RuntimeError(t("upd.msg.query_fail", error=exc)) from exc
    status = "update_available" if is_newer(latest, current) else "up_to_date"
    return {"status": status, "current": current, "latest": latest}


def check_startup(eng):
    """啟動時使用的檢查入口：只查 yt-dlp 有沒有新版（不安裝），不查 Python、FFmpeg、Deno。"""
    return ytdlp_status(eng)


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
        raise RuntimeError(t("upd.msg.py_query_fail", details=t("upd.sep").join(errors or [t("upd.msg.no_data")])))
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
        return (t("update.copy_upgrade"), f"winget upgrade --id Python.Python.{series} --exact")
    if py.get("new_series") and py.get("overall"):
        newer = ".".join(str(py["overall"]).split(".")[:2])
        if _SERIES_RE.match(newer):
            return (t("update.copy_install"), f"winget install --id Python.Python.{newer} --exact")
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


# --------------------------------------------------------------------------- #
# FFmpeg、Deno 的 winget 更新檢查
# --------------------------------------------------------------------------- #

WINGET_IDS = {"ffmpeg": "Gyan.FFmpeg", "deno": "DenoLand.Deno"}
DENO_DOWNLOADS_URL = "https://deno.com/"
_DENO_VERSION_RE = re.compile(r"^deno (\S+)", re.M)


def winget_upgrade_list_command(pkg_id, winget="winget"):
    """列出「有新版可升級」的套件（只列出，不會安裝）。"""
    return [winget, "list", "--upgrade-available", "--id", pkg_id, "-e", "--accept-source-agreements"]


def output_lists_package(output, pkg_id):
    """winget 的輸出會隨系統語言改變，所以不解析文字，只看套件 ID 有沒有出現在結果表格裡。"""
    return pkg_id.lower() in str(output or "").lower()


def winget_has_upgrade(pkg_id, run=subprocess.run, winget=None, timeout=60):
    """winget 認得這個套件而且有新版時回傳 True；winget 不可用、不是 winget 裝的、或查詢失敗都回傳 False。"""
    winget = winget or shutil.which("winget")
    if not winget:
        return False
    try:
        proc = run(winget_upgrade_list_command(pkg_id, winget), capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=timeout, **utils.hidden_subprocess_kwargs())
    except (OSError, subprocess.SubprocessError):
        return False
    return output_lists_package(proc.stdout, pkg_id)


def parse_deno_version(text):
    match = _DENO_VERSION_RE.search(text or "")
    return match.group(1) if match else None


def find_deno(base_dir):
    found = shutil.which("deno")
    if found:
        return found
    local = os.path.join(base_dir, "deno.exe")
    return local if os.path.isfile(local) else None


def check_deno(base_dir, check_upgrade=False, winget_ok=False):
    """找出 Deno 並讀版本。回傳 {"status": "missing"} 或
    {"status": "ok", "version", "path", "source": "program" | "path", "update": bool}。
    放在程式資料夾的 deno.exe 不是 winget 裝的，不檢查升級。"""
    path = find_deno(base_dir)
    if not path:
        return {"status": "missing"}
    in_program_dir = os.path.normcase(os.path.dirname(path)) == os.path.normcase(os.path.abspath(base_dir))
    version = None
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=15, env=utils.child_env(), **utils.hidden_subprocess_kwargs())
        version = parse_deno_version(proc.stdout) if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        pass
    update = bool(check_upgrade and winget_ok and not in_program_dir and winget_has_upgrade(WINGET_IDS["deno"]))
    return {"status": "ok", "version": version, "path": path,
            "source": "program" if in_program_dir else "path", "update": update}


def check_all(eng, log=lambda text: None, base_dir=None, auto_update=True):
    """一次檢查 yt-dlp、Python、FFmpeg、Deno；各自回報成功或錯誤，互不影響。

    auto_update=True（舊行為）：yt-dlp 有新版就直接更新。
    auto_update=False（v7.0 的「檢查更新」按鈕）：只查不裝，yt-dlp、FFmpeg、Deno 有新版時
    在結果裡標示（status = update_available／update = True），由 modules/check.py 的彈窗讓使用者確認。
    FFmpeg 與 Deno 放在程式資料夾時不是 winget 裝的，只顯示版本。Python 的檢查與顯示維持原樣。
    base_dir 為 None 時略過 FFmpeg 與 Deno。
    """
    results = {}
    try:
        results["ytdlp"] = check_and_update(eng, log) if auto_update else ytdlp_status(eng)
    except Exception as exc:  # noqa: BLE001
        results["ytdlp"] = {"status": "error", "message": str(exc)}
    if ejs_applies(eng):
        try:
            results["ejs"] = check_ejs(eng, check_upgrade=not auto_update)
        except Exception as exc:  # noqa: BLE001
            results["ejs"] = {"status": "error", "message": str(exc)}
    log(t("upd.log.checking_python"))
    try:
        results["python"] = check_python()
        command = winget_python_command(results["python"])
        if command and utils.winget_available():  # 只有 Windows 且 winget 真的能用，才提供複製命令
            results["python"]["winget_command"] = command
    except Exception as exc:  # noqa: BLE001
        results["python"] = {"status": "error", "message": str(exc)}
    if base_dir is None:
        return results
    winget_ok = (not auto_update) and utils.winget_available()
    log(t("upd.log.checking_ffmpeg"))
    try:
        results["ffmpeg"] = check_ffmpeg(base_dir)
        ff = results["ffmpeg"]
        ff["update"] = bool(winget_ok and ff.get("status") == "ok" and ff.get("source") == "path"
                            and winget_has_upgrade(WINGET_IDS["ffmpeg"]))
    except Exception as exc:  # noqa: BLE001
        results["ffmpeg"] = {"status": "error", "message": str(exc)}
    log(t("upd.log.checking_deno"))
    try:
        results["deno"] = check_deno(base_dir, check_upgrade=not auto_update, winget_ok=winget_ok)
    except Exception as exc:  # noqa: BLE001
        results["deno"] = {"status": "error", "message": str(exc)}
    return results
