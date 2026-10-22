# modules/downloader.py
"""yt-dlp 下載核心：組合命令、執行、解析進度、分類錯誤、支援取消。

所有下載都以子行程執行 yt-dlp（見 engine.py），UI 只透過 callback 取得結果。
"""
import json
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import List, Tuple
from urllib.parse import parse_qs, urlparse

from modules import engine as engine_mod
from modules import logger, utils

AUDIO_FORMATS = ("mp3", "m4a", "wav", "aac", "flac", "opus")
LOSSY_AUDIO = ("mp3", "m4a", "aac", "opus")
VIDEO_CONTAINERS = ("mp4", "mkv", "webm")

# 畫質 → 高度上限。「720P」代表「最高到 720P」，而不是「至少 720P」。
QUALITY_HEIGHTS = {
    "8K": 4320, "4K": 2160, "2K": 1440,
    "1080P": 1080, "720P": 720, "480P": 480, "240P": 240,
}

DEFAULT_SUBTITLE_LANGS = "zh-Hant,zh-TW,en.*"
DEFAULT_FILENAME_TEMPLATE = "%(title)s.%(ext)s"
NO_COOKIE_VALUES = ("", "無", "none", "None", "不使用")

PROGRESS_TEMPLATE = ("Downloading:%(progress._percent_str)s | "
                     "%(progress._speed_str)s | ETA %(progress._eta_str)s")

_PROGRESS_RE = re.compile(r"^(?:Downloading:|\[download\])\s*(\d+(?:[.,]\d+)?)\s*%")

# 從 yt-dlp 輸出判斷最終檔案位置（後出現的覆蓋先出現的，Merger / ExtractAudio 在最後）
_PATH_PATTERNS = [
    re.compile(r'^\[Merger\] Merging formats into "(.+)"$'),
    re.compile(r"^\[ExtractAudio\] Destination: (.+)$"),
    re.compile(r"^\[download\] Destination: (.+)$"),
    re.compile(r"^\[download\] (.+) has already been downloaded"),
]
_NON_MEDIA_EXTS = {".vtt", ".srt", ".ass", ".lrc", ".json", ".jpg", ".jpeg", ".png", ".webp", ".description"}

# yt-dlp 分開下載影音時的中間檔，例如 title.f137.mp4；合併後會被刪除
_INTERMEDIATE_RE = re.compile(r"\.f[\w-]+\.\w+$")

_FFMPEG_MISSING_RE = re.compile(r"(ffmpeg|ffprobe)( and (ffmpeg|ffprobe))? (is )?not (found|installed)")

MESSAGES = {
    "no_engine": "找不到 yt-dlp。請執行 pip install yt-dlp，或把 yt-dlp.exe 放進程式資料夾。",
    "reload": ("YouTube 要求重新載入頁面，可能是 yt-dlp 版本過舊、播放器驗證或登入狀態造成。\n"
               "請先按「檢查更新」；若仍失敗，請重新匯入 YouTube Cookies.txt。"),
    "ffmpeg": "FFmpeg 缺失或無法執行。請將 ffmpeg.exe 放入程式資料夾，或將 FFmpeg 加入系統 PATH 後再試一次。",
    "outdated": "yt-dlp 版本過舊，下載失敗。請先按「檢查更新」。",
    "http412": ("Bilibili 回傳 HTTP 412（反爬驗證）。請先在瀏覽器登入 bilibili.com，"
                "也可能需要重新匯入 Cookies.txt；並按「檢查更新」更新 yt-dlp。"),
    "rate_limit": "對方伺服器回應請求過於頻繁（HTTP 429）。請稍後再試；若有開啟字幕，可減少字幕語言數量。",
    "cookie_dpapi": "Cookie 讀取失敗：Windows DPAPI 無法解密瀏覽器 Cookie。請改用瀏覽器擴充功能匯出 Cookies.txt。",
    "cookie": "Cookie 讀取或解析失敗。請確認瀏覽器已完全關閉，或改匯入 Cookies.txt。",
    "login": "此影片需要登入、年齡驗證或通過機器人檢查。請匯入已登入網站的 Cookies.txt 後再試一次。",
    "js_runtime": ("yt-dlp 找不到可用的 JavaScript 執行環境，部分 YouTube 影片可能因此無法解析。"
                   "請安裝 Deno（或 Node.js），並先按「檢查更新」。"),
    "format": "找不到符合條件的畫質或格式，請改選「預設」畫質再試一次。",
    "unsupported": "yt-dlp 不支援這個網址，請確認網址是否完整。",
    "unavailable": "影片無法使用（可能已刪除、私人影片或地區限制）。",
}


@dataclass
class DownloadOptions:
    fmt: str = "mp4"
    quality: str = "預設"
    cookie_browser: str = "無"
    cookie_file: str = ""
    write_subtitles: bool = False
    embed_subtitles: bool = False
    subtitle_langs: str = DEFAULT_SUBTITLE_LANGS
    write_thumbnail: bool = False
    add_metadata: bool = False
    number_playlist_items: bool = False  # 只在下載整個播放清單時生效
    use_archive: bool = False
    filename_template: str = DEFAULT_FILENAME_TEMPLATE


@dataclass
class DownloadItem:
    """一筆要下載的工作。playlist=True 代表下載整個播放清單，並放進 folder 子資料夾。"""
    url: str
    playlist: bool = False
    folder: str = ""  # 播放清單子資料夾名稱；開始下載該項目時才會建立

    def target_dir(self, outdir):
        return os.path.join(outdir, self.folder) if self.playlist and self.folder else outdir


@dataclass
class DownloadResult:
    success: bool = False
    cancelled: bool = False
    code: str = ""
    message: str = ""
    total: int = 0
    succeeded: int = 0
    failures: List[Tuple[str, str]] = field(default_factory=list)
    files: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def find_ffmpeg(base_dir):
    """找內建或 PATH 中的 ffmpeg，回傳絕對路徑或 None。"""
    candidates = [
        os.path.join(base_dir, "ffmpeg.exe"),
        os.path.join(base_dir, "ffmpeg"),
        shutil.which("ffmpeg"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


FFMPEG_DOWNLOAD_URL = "https://www.ffmpeg.org/download.html"


def ffmpeg_install_command():
    """可以直接貼到終端機的 FFmpeg 安裝命令，回傳 (按鈕文字, 命令)；沒有適用的命令時回傳 None。

    目前只有 Linux 且有 apt（Debian／Ubuntu 系）才提供。其他發行版的套件管理員與套件名稱不同
    （例如 Fedora 的 ffmpeg 需要額外軟體庫），顯示 apt 命令反而會誤導，所以不提供。
    """
    if platform.system() == "Linux" and (shutil.which("apt") or shutil.which("apt-get")):
        return ("複製安裝命令", "sudo apt install ffmpeg")
    return None


def ffmpeg_missing_message(base_dir):
    """找不到 FFmpeg 時顯示的說明，依作業系統附上合適的安裝提示。"""
    system = platform.system()
    name = "ffmpeg.exe" if system == "Windows" else "ffmpeg"
    lines = ["找不到 FFmpeg，下載（合併影音、轉檔）需要它。", ""]
    if ffmpeg_install_command():  # 命令本身由對話框另外顯示，並提供複製按鈕
        lines += ["請在終端機執行下面的命令安裝（需要輸入密碼）；",
                  f"也可以把 ffmpeg 放進程式資料夾：\n{base_dir}"]
    else:
        lines += [f"請把 {name} 放進程式資料夾：\n{base_dir}", "或將 FFmpeg 加入系統 PATH，然後再試一次。"]
        if system == "Windows":
            lines += ["", "FFmpeg 官網主要提供原始碼，Windows 版通常連結到第三方建置（例如 gyan.dev、BtbN），請在下載頁的「Windows」區塊選擇。"]
        elif system == "Darwin":
            lines += ["", "macOS 也可以用 Homebrew 安裝：brew install ffmpeg"]
        elif system == "Linux":
            lines += ["", "也可以用您的 Linux 發行版的套件管理員安裝 ffmpeg。"]
    return "\n".join(lines)


def read_batch_urls(path):
    """讀取批次清單：忽略空行與 # 註解，並移除重複網址（保留原本順序）。"""
    urls, seen = [], set()
    with open(path, encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or line in seen:
                continue
            seen.add(line)
            urls.append(line)
    return urls


_YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")


def classify_playlist_url(url):
    """判斷網址是否含 YouTube 播放清單。

    回傳 "none"（沒有清單）、"mixed"（同時指定了影片與清單，例如 watch?v=…&list=…）、
    "playlist"（純播放清單，沒有指定哪一支影片）。其他網站一律視為 "none"。
    """
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return "none"
    host = (parsed.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in _YOUTUBE_HOSTS):
        return "none"
    if not parse_qs(parsed.query).get("list"):
        return "none"
    path = parsed.path.rstrip("/")
    if path == "/playlist":
        return "playlist"
    has_video = (bool(parse_qs(parsed.query).get("v"))
                 or (host.endswith("youtu.be") and len(path) > 1)
                 or path.startswith(("/shorts/", "/embed/", "/live/")))
    return "mixed" if has_video else "playlist"


DEFAULT_PLAYLIST_FOLDER = "播放清單"
_PLAYLIST_NAME_RE = re.compile(r"^播放清單(\d+)$")
_INVALID_FOLDER_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = ({"CON", "PRN", "AUX", "NUL"}
                     | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)})


def sanitize_folder_name(name, max_len=100):
    """把使用者輸入轉成合法的 Windows 資料夾名稱；清完是空的就回傳空字串。"""
    name = _INVALID_FOLDER_CHARS.sub("_", (name or "").strip())
    name = name[:max_len].rstrip(" .")
    if not name:
        return ""
    if name.split(".")[0].upper() in _WINDOWS_RESERVED:
        name = "_" + name
    return name


def next_playlist_folder(outdir, reserved=()):
    """預設資料夾名稱：「播放清單」+（目前最大編號 + 1）。

    會掃描輸出資料夾中已存在的「播放清單N」資料夾，以及本次已預留（尚未建立）的名稱，
    所以不會與既有資料夾同名，批次下載多個清單時也不會互相撞名。
    """
    highest = 0
    try:
        names = [n for n in os.listdir(outdir) if os.path.isdir(os.path.join(outdir, n))]
    except OSError:
        names = []
    for name in list(names) + list(reserved):
        match = _PLAYLIST_NAME_RE.match(name)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{DEFAULT_PLAYLIST_FOLDER}{highest + 1}"


def expand_target(target):
    """把輸入框內容展開成網址清單：單一網址，或 ``file:<清單路徑>`` 的所有網址。"""
    target = (target or "").strip()
    if target.startswith("file:"):
        return read_batch_urls(target[5:])
    return [target] if target else []


def derive_title(item, files):
    """歷史紀錄用的標題：播放清單顯示摘要，單支影片取輸出檔名（不含副檔名）。"""
    if item.playlist:
        return f"{DEFAULT_PLAYLIST_FOLDER}：{item.folder or DEFAULT_PLAYLIST_FOLDER}（{len(files)} 部）"
    for path in reversed(files):
        stem = os.path.splitext(os.path.basename(str(path).replace("\\", "/")))[0]
        if stem:
            return stem
    return ""


def build_format_selector(quality):
    """畫質 → yt-dlp -f 表達式。

    例如 720P：優先「高度 ≤ 720 的最佳影像 + 最佳音訊」，其次高度 ≤ 720 的合併串流；
    若影片根本沒有這麼低的畫質，退而求其次取最低畫質，而不是反而下載 4K。
    """
    height = QUALITY_HEIGHTS.get(quality)
    if height is None:
        return "bv*+ba/b"
    return f"bv*[height<={height}]+ba/b[height<={height}]/wv*+ba/w"


def extract_output_path(line):
    """從 yt-dlp 的輸出行取出檔案路徑（字幕、縮圖等附屬檔不算）。"""
    for pattern in _PATH_PATTERNS:
        match = pattern.match(line)
        if match:
            path = match.group(1).strip().strip('"')
            if os.path.splitext(path)[1].lower() in _NON_MEDIA_EXTS:
                return None
            return path
    return None


def _browser_cookie_arg(browser):
    if not browser or browser.strip() in NO_COOKIE_VALUES:
        return None
    return browser.strip().lower()


def _network_args(url, opts):
    args = []
    # Bilibili 需要瀏覽器式的標頭；其他網站不要硬帶，以免被誤判。
    if utils.is_bilibili_url(url):
        args += ["--add-headers", "Origin:https://www.bilibili.com",
                 "--add-headers", "Referer:https://www.bilibili.com/"]
    browser = _browser_cookie_arg(opts.cookie_browser)
    if opts.cookie_file and os.path.isfile(opts.cookie_file):
        args += ["--cookies", opts.cookie_file]  # Cookies.txt 優先於瀏覽器 Cookie
    elif browser:
        args += ["--cookies-from-browser", browser]
    return args


def build_command(base_dir, eng, item, outdir, opts):
    """組合單一項目的 yt-dlp 命令。URL 前面一定加 ``--``，避免網址被當成選項。"""
    url = item.url
    cmd = list(eng.command)
    cmd += ["--no-update", "--newline", "--progress-template", PROGRESS_TEMPLATE,
            "--retries", "3", "--fragment-retries", "3", "--extractor-retries", "3",
            "--retry-sleep", "http:exp=1:20"]

    ffmpeg = find_ffmpeg(base_dir)
    if ffmpeg:
        cmd += ["--ffmpeg-location", os.path.dirname(ffmpeg)]

    cmd += _network_args(url, opts)
    cmd += ["--yes-playlist" if item.playlist else "--no-playlist"]

    if opts.use_archive:
        archive = os.path.join(base_dir, "config", "archive.txt")
        os.makedirs(os.path.dirname(archive), exist_ok=True)
        cmd += ["--download-archive", archive]

    fmt = (opts.fmt or "mp4").lower()
    is_audio = fmt in AUDIO_FORMATS
    if is_audio:
        cmd += ["-x", "--audio-format", fmt]
        if fmt in LOSSY_AUDIO:
            cmd += ["--audio-quality", "0"]
    else:
        cmd += ["-f", build_format_selector(opts.quality)]
        if fmt in VIDEO_CONTAINERS:
            cmd += ["--merge-output-format", fmt]

    template = (opts.filename_template or "").strip() or DEFAULT_FILENAME_TEMPLATE
    if item.playlist and opts.number_playlist_items and "playlist_index" not in template:
        template = "%(playlist_index)s - " + template  # 01 - 標題.mp4（位數依清單長度自動補零）
    cmd += ["-o", os.path.join(item.target_dir(outdir), template)]

    if opts.write_subtitles or (opts.embed_subtitles and not is_audio):
        cmd += ["--sub-langs", (opts.subtitle_langs or "").strip() or DEFAULT_SUBTITLE_LANGS]
    if opts.write_subtitles:
        cmd += ["--write-subs"]
    if opts.embed_subtitles and not is_audio:
        cmd += ["--embed-subs"]
    if opts.write_thumbnail:
        cmd += ["--write-thumbnail"]
    if opts.add_metadata:
        cmd += ["--add-metadata"]

    cmd += ["--", url]
    return cmd


def format_command(cmd):
    if os.name == "nt":
        return subprocess.list2cmdline(cmd)
    return " ".join(shlex.quote(part) for part in cmd)


def classify_error(lines, rc):
    """把 yt-dlp 輸出歸類成 (代碼, 給使用者看的訊息)。

    只分析 ERROR 行；若沒有 ERROR 行才看最後 20 行。這樣正常輸出中出現的
    ``[ffmpeg]`` 字樣不會被誤判成「FFmpeg 缺失」。
    """
    lines = [str(line) for line in lines]
    errors = [line for line in lines if line.startswith("ERROR")]
    text = "\n".join(errors) if errors else "\n".join(lines[-20:])
    low = text.lower()
    full_low = "\n".join(lines).lower()

    if "the page needs to be reloaded" in low:
        return "reload", MESSAGES["reload"]
    if _FFMPEG_MISSING_RE.search(low):
        return "ffmpeg", MESSAGES["ffmpeg"]
    if "older than 90 days" in low or ("yt-dlp version" in low and ("old" in low or "outdated" in low)):
        return "outdated", MESSAGES["outdated"]
    if "http error 412" in low or "precondition failed" in low:
        return "http412", MESSAGES["http412"]
    if "http error 429" in low or "too many requests" in low:
        return "rate_limit", MESSAGES["rate_limit"]
    if "failed to decrypt with dpapi" in low:
        return "cookie_dpapi", MESSAGES["cookie_dpapi"]
    if ("cookies-from-browser" in low or "cookie database" in low or "could not copy" in low
            or "cookies.txt" in low or "cookie" in low):
        return "cookie", MESSAGES["cookie"]
    if ("sign in to confirm" in low or "login required" in low or "private video" in low
            or "confirm your age" in low or "not a bot" in low):
        return "login", MESSAGES["login"]
    if "requested format is not available" in low:
        if "javascript runtime" in full_low or "js runtime" in full_low:
            return "js_runtime", MESSAGES["js_runtime"]
        return "format", MESSAGES["format"]
    if "javascript runtime" in full_low or "js runtime" in full_low:
        return "js_runtime", MESSAGES["js_runtime"]
    if "unsupported url" in low:
        return "unsupported", MESSAGES["unsupported"]
    if "video unavailable" in low or "this video is not available" in low:
        return "unavailable", MESSAGES["unavailable"]

    detail = errors[-1][:200] if errors else ""
    return "unknown", (f"下載失敗（Exit code {rc}）：{detail}" if detail else f"下載失敗（Exit code {rc}）")


# --------------------------------------------------------------------------- #
# 行程控制
# --------------------------------------------------------------------------- #

def kill_process_tree(proc):
    """連同子行程（例如 ffmpeg）一起終止；單純 proc.kill() 在 Windows 會留下孤兒行程。"""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10, **utils.hidden_subprocess_kwargs())
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception as exc:
        logger.get().warning("Failed to kill process tree %s: %s", proc.pid, exc)
        try:
            proc.kill()
        except Exception:
            pass


class DownloadController:
    """一次下載工作的控制代碼：可取消，也可查詢是否已被取消。"""

    def __init__(self):
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._proc = None
        self.thread = None

    @property
    def cancelled(self):
        return self._cancel_event.is_set()

    def attach(self, proc):
        with self._lock:
            self._proc = proc
        if self.cancelled:  # 取消發生在行程剛啟動的空檔
            kill_process_tree(proc)

    def detach(self):
        with self._lock:
            self._proc = None

    def cancel(self):
        self._cancel_event.set()
        with self._lock:
            proc = self._proc
        if proc is not None:
            kill_process_tree(proc)


_controllers = []
_controllers_lock = threading.Lock()


def terminate_all_downloads():
    """終止所有進行中的下載（關閉視窗時呼叫）。"""
    with _controllers_lock:
        controllers = list(_controllers)
    logger.get().info("Terminating %d running downloads", len(controllers))
    for controller in controllers:
        controller.cancel()


# --------------------------------------------------------------------------- #
# 下載流程
# --------------------------------------------------------------------------- #

def run_download(base_dir, items, outdir, opts, progress_callback=None, log_callback=None,
                 item_started=None, item_finished=None, finished_callback=None):
    """在背景執行緒依序下載 items（DownloadItem 清單）。

    Callbacks（都在背景執行緒呼叫，UI 端請自行切回主執行緒）：
      progress_callback(fraction | None, text)   進度條；fraction 為 0~1 的整體進度
      log_callback(text)                           一般日誌行
      item_started(index, total, item)
      item_finished(index, total, item, success, message, files)
      finished_callback(DownloadResult)
    """
    controller = DownloadController()
    thread = threading.Thread(
        target=_download_worker,
        args=(base_dir, list(items), outdir, opts, controller, progress_callback, log_callback,
              item_started, item_finished, finished_callback),
        daemon=True,
    )
    controller.thread = thread
    with _controllers_lock:
        _controllers.append(controller)
    thread.start()
    return controller


def _noop(*args, **kwargs):
    return None


def _download_worker(base_dir, items, outdir, opts, controller, progress_cb, log_cb,
                     item_started, item_finished, finished_cb):
    progress_cb = progress_cb or _noop
    log_cb = log_cb or _noop
    item_started = item_started or _noop
    item_finished = item_finished or _noop
    result = DownloadResult()
    try:
        os.makedirs(outdir, exist_ok=True)
        eng = engine_mod.resolve_engine(base_dir)
        if eng is None:
            result.code, result.message = "no_engine", MESSAGES["no_engine"]
            return
        logger.get().info("Using yt-dlp engine: %s", eng.label)

        if not items:
            result.code, result.message = "empty", "沒有可下載的項目。"
            return

        result.total = total = len(items)
        for index, item in enumerate(items, start=1):
            if controller.cancelled:
                break
            item_started(index, total, item)
            try:
                if item.playlist:  # 播放清單資料夾在這一刻（開始下載時）才建立
                    os.makedirs(item.target_dir(outdir), exist_ok=True)
                ok, message, code, files = _run_single(
                    base_dir, eng, item, outdir, opts, controller, index, total, progress_cb, log_cb)
            except OSError as exc:
                ok, message, code, files = False, f"無法建立資料夾：{exc}", "mkdir_failed", []
            item_finished(index, total, item, ok, message, files)
            result.files.extend(f for f in files if f not in result.files)
            if ok:
                result.succeeded += 1
            else:
                result.code = code
                if code != "cancelled":
                    result.failures.append((item.url, message))
                if code == "cancelled":
                    break

        result.cancelled = controller.cancelled
        result.success = (not result.cancelled and not result.failures
                          and result.succeeded == result.total)
        if result.cancelled:
            result.code, result.message = "cancelled", "已取消下載。"
        elif total == 1:
            result.message = "下載完成" if result.success else result.failures[0][1]
        else:
            result.message = f"完成 {result.succeeded}/{total}，失敗 {len(result.failures)}"
            if result.success:
                result.code = ""
    except Exception as exc:  # noqa: BLE001 — 背景執行緒必須把任何錯誤回報給 UI
        logger.get().exception("Error during download thread: %s", exc)
        result.code, result.message = "exception", str(exc)
    finally:
        controller.detach()
        with _controllers_lock:
            if controller in _controllers:
                _controllers.remove(controller)
        if finished_cb:
            finished_cb(result)


def _popen_kwargs():
    kwargs = utils.hidden_subprocess_kwargs()
    if os.name != "nt":
        kwargs["start_new_session"] = True  # 讓 killpg 能一併終止 ffmpeg
    return kwargs


def _record_output_file(files, line, path):
    """記錄輸出檔；合併／轉檔後的最終檔會取代已被刪除的中間檔。"""
    if line.startswith("[Merger]"):
        files[:] = [f for f in files if not _INTERMEDIATE_RE.search(f)]
    elif line.startswith("[ExtractAudio]") and files:
        files.pop()  # 抽音前下載的原始檔會被刪除
    if path not in files:
        files.append(path)


def _run_single(base_dir, eng, item, outdir, opts, controller, index, total, progress_cb, log_cb):
    cmd = build_command(base_dir, eng, item, outdir, opts)
    logger.get().info("Running command: %s", format_command(cmd))
    prefix = f"[{index}/{total}] " if total > 1 else ""
    output = deque(maxlen=400)
    files = []

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=base_dir,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=utils.child_env(), **_popen_kwargs(),
        )
    except OSError as exc:
        return False, f"無法啟動 yt-dlp：{exc}", "start_failed", files

    controller.attach(proc)
    try:
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            output.append(line)
            logger.get().debug("yt-dlp: %s", line)

            match = _PROGRESS_RE.match(line)
            if match:
                fraction = float(match.group(1).replace(",", ".")) / 100.0
                fraction = max(0.0, min(1.0, fraction))
                progress_cb((index - 1 + fraction) / total, prefix + line)
            elif line.startswith("Downloading:"):
                progress_cb(None, prefix + line)  # 例如 N/A%，只更新文字
            else:
                path = extract_output_path(line)
                if path:
                    _record_output_file(files, line, path)
                log_cb(line)
        proc.wait()
    except Exception:
        kill_process_tree(proc)
        raise
    finally:
        controller.detach()
        try:
            proc.stdout.close()
        except Exception:
            pass

    if controller.cancelled:
        return False, "已取消", "cancelled", files
    if proc.returncode == 0:
        return True, "完成", "", files
    code, message = classify_error(output, proc.returncode)
    return False, message, code, files


# --------------------------------------------------------------------------- #
# 影片資訊
# --------------------------------------------------------------------------- #

def summarize_info(data):
    formats = data.get("formats") or []
    heights = sorted({f["height"] for f in formats
                      if f.get("height") and f.get("vcodec") not in (None, "none")}, reverse=True)
    entries = data.get("entries") or []
    return {
        "title": data.get("title") or "（未知標題）",
        "uploader": data.get("uploader") or data.get("channel") or "",
        "duration": data.get("duration"),
        "is_playlist": data.get("_type") == "playlist",
        "entry_count": len(entries),
        "heights": heights,
    }


def fetch_info(base_dir, url, opts, timeout=90):
    """讀取影片（或播放清單）資訊，不下載。失敗時丟出 RuntimeError（訊息已中文化）。"""
    eng = engine_mod.resolve_engine(base_dir)
    if eng is None:
        raise RuntimeError(MESSAGES["no_engine"])
    cmd = list(eng.command) + ["-J", "--no-warnings", "--no-update"]
    if classify_playlist_url(url) == "playlist":  # 純播放清單 → 讀清單摘要；影片加清單 → 只讀那支影片
        cmd += ["--yes-playlist", "--flat-playlist"]
    else:
        cmd += ["--no-playlist"]
    cmd += _network_args(url, opts)
    cmd += ["--", url]
    logger.get().info("Fetching info: %s", format_command(cmd))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, cwd=base_dir, env=utils.child_env(), **utils.hidden_subprocess_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("讀取影片資訊逾時，請檢查網路或稍後再試。") from exc
    if proc.returncode:
        _, message = classify_error((proc.stderr or proc.stdout or "").splitlines(), proc.returncode)
        raise RuntimeError(message)
    try:
        return summarize_info(json.loads(proc.stdout))
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(f"無法解析影片資訊：{exc}") from exc
