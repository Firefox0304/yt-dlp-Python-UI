# modules/downloader.py
import os
import subprocess
import threading
import shlex
import re
import shutil
import tempfile
from modules import logger

# Match both the normal yt-dlp progress line and the explicit
# ``Downloading:...`` line emitted by --progress-template.
_PERCENT_RE = re.compile(r"(?:Downloading:\s*)?(\d+(?:[.,]\d+)?)\s*%", re.IGNORECASE)
_running_processes = []


def find_ffmpeg(base_dir):
    """Find bundled or PATH ffmpeg and return its path, or None."""
    candidates = [
        os.path.join(base_dir, "ffmpeg.exe"),
        os.path.join(base_dir, "ffmpeg"),
        shutil.which("ffmpeg"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


def _read_batch_urls(path):
    """Read URL lines while ignoring blank lines and full-line comments."""
    with open(path, encoding="utf-8-sig") as f:
        return [
            line.strip() for line in f
            if line.strip() and not line.lstrip().startswith("#")
        ]


def _prepare_batch_file(path):
    """Create a temporary yt-dlp list without comments or blank lines."""
    urls = _read_batch_urls(path)
    fd, clean_path = tempfile.mkstemp(prefix="ytui-batch-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(urls))
        if urls:
            f.write("\n")
    return clean_path


def terminate_all_downloads():
    """終止所有運行中的下載進程。"""
    global _running_processes
    logger.get().info("Terminating %d running downloads", len(_running_processes))
    for p in _running_processes[:]:
        try:
            logger.get().info("Killing process %s", p.pid)
            p.kill()
        except Exception as e:
            logger.get().warning("Failed to kill process %s: %s", p.pid, e)
    _running_processes.clear()


def _browser_cookie_arg(browser):
    """Return the yt-dlp browser name, or None when cookies are disabled."""
    if not browser or browser in ("無", "None", "none", "不使用"):
        return None
    return browser.strip().lower()


def _build_command(base_dir, urls, outdir, fmt, quality="預設", cookie_browser="無", cookie_file="", advanced=None):
    """Build a yt-dlp command for one URL or a file: batch list."""
    exe = os.path.join(base_dir, "yt-dlp.exe")
    cmd = [exe] if os.path.exists(exe) else ["yt-dlp"]

    cmd += ["--no-update", "--newline", "--progress-template",
            "Downloading:%(progress._percent_str)s | %(progress._speed_str)s | ETA %(progress._eta_str)s",
            "--retries", "3", "--fragment-retries", "3", "--extractor-retries", "3",
            "--retry-sleep", "http:exp=1:20"]

    # Bilibili currently rejects some unsigned/default clients. These headers also
    # make the request look like the browser page that supplied the URL.
    cmd += ["--add-headers", "Origin:https://www.bilibili.com",
            "--add-headers", "Referer:https://www.bilibili.com/"]
    browser = _browser_cookie_arg(cookie_browser)
    if cookie_file and os.path.isfile(cookie_file):
        cmd += ["--cookies", cookie_file]
    elif browser:
        cmd += ["--cookies-from-browser", browser]

    if urls.startswith("file:"):
        cmd += ["-a", urls[5:]]
    else:
        cmd += [urls]

    audio_formats = ("mp3", "m4a", "wav", "aac", "flac", "opus")
    normalized_fmt = (fmt or "mp4").lower()
    if normalized_fmt == "mp3":
        cmd += ["-x", "--audio-format", "mp3"]
    elif normalized_fmt in audio_formats:
        cmd += ["-x", "--audio-format", normalized_fmt]
    else:
        quality_map = {
            "預設": "bv*+ba/b",
            "8K": "bestvideo[height>=4320]+bestaudio/best",
            "4K": "bestvideo[height>=2160]+bestaudio/best",
            "2K": "bestvideo[height>=1440]+bestaudio/best",
            "1080P": "bestvideo[height>=1080]+bestaudio/best",
            "720P": "bestvideo[height>=720]+bestaudio/best",
            "480P": "bestvideo[height>=480]+bestaudio/best",
            "240P": "bestvideo[height>=240]+bestaudio/best",
        }
        cmd += ["-f", quality_map.get(quality, quality_map["預設"])]
        if normalized_fmt in ("mp4", "mkv", "webm"):
            cmd += ["--merge-output-format", normalized_fmt]

    outtmpl = os.path.join(outdir, "%(title)s.%(ext)s")
    cmd += ["-o", outtmpl]
    advanced = advanced or {}
    if advanced.get("write_subtitles"):
        cmd += ["--write-subs", "--sub-langs", "all"]
    if advanced.get("embed_subtitles"):
        cmd += ["--embed-subs"]
    if advanced.get("write_thumbnail"):
        cmd += ["--write-thumbnail"]
    if advanced.get("add_metadata"):
        cmd += ["--add-metadata"]
    return cmd


def run_download(base_dir, urls, outdir, fmt, quality="預設", cookie_browser="無", cookie_file="", advanced=None,
                 progress_callback=None, finished_callback=None):
    t = threading.Thread(
        target=_download_thread,
        args=(base_dir, urls, outdir, fmt, quality, cookie_browser, cookie_file, advanced,
              progress_callback, finished_callback),
        daemon=True,
    )
    t.start()
    return t


def _friendly_error(output, rc):
    text = output or ""
    if "the page needs to be reloaded" in text.lower():
        return ("YouTube 要求重新載入頁面，可能是 yt-dlp 版本、播放器驗證或登入狀態造成。"
                "請先按「檢查更新」；若仍失敗，請重新匯入 YouTube Cookies.txt。")
    if "ffmpeg" in text.lower() or "ffprobe" in text.lower():
        return "FFmpeg 缺失或無法執行。"
    if ("older than 90 days" in text.lower()
            or "yt-dlp version" in text.lower() and ("old" in text.lower() or "outdated" in text.lower())):
        return "yt-dlp 版本過舊，下載失敗。"
    if "HTTP Error 412" in text or "Precondition Failed" in text:
        return ("Bilibili 回傳 HTTP 412（反爬驗證）。請先在瀏覽器登入 bilibili.com，"
                "也可能需要重新匯入 Cookies.txt；請按「檢查更新」更新 yt-dlp。")
    if "cookies-from-browser" in text and ("Could not copy" in text or "No such file" in text):
        return "Cookie 讀取失敗：無法讀取瀏覽器 Cookie。"
    if "Failed to decrypt with DPAPI" in text:
        return "Cookie 讀取失敗：Windows DPAPI 無法解密瀏覽器 Cookie。"
    if any(keyword in text.lower() for keyword in (
            "cookie", "cookies.txt", "cookie database", "decrypt with dpapi")):
        return "Cookie 讀取或解析失敗。"
    return f"Exit code {rc}"


def _download_thread(base_dir, urls, outdir, fmt, quality, cookie_browser, cookie_file, advanced,
                     progress_callback, finished_callback):
    os.makedirs(outdir, exist_ok=True)
    clean_batch_file = None
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        creationflags = subprocess.CREATE_NO_WINDOW

    p = None
    output_lines = []
    try:
        command_urls = urls
        if urls.startswith("file:"):
            clean_batch_file = _prepare_batch_file(urls[5:])
            command_urls = "file:" + clean_batch_file
        cmd = _build_command(base_dir, command_urls, outdir, fmt, quality, cookie_browser, cookie_file, advanced)
        logger.get().info("Running command: %s", " ".join(shlex.quote(x) for x in cmd))
        try:
            p = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=base_dir, startupinfo=startupinfo, creationflags=creationflags,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
            _running_processes.append(p)
        except FileNotFoundError:
            # Module fallback is useful when the user installed yt-dlp with pip.
            import yt_dlp as ytdlp  # type: ignore

            def module_progress_hook(status):
                if not progress_callback:
                    return
                state = status.get("status")
                if state == "downloading":
                    raw_percent = status.get("_percent_str") or status.get("percent")
                    if raw_percent is not None:
                        match = _PERCENT_RE.search(str(raw_percent))
                        if match:
                            percent = float(match.group(1).replace(",", ".")) / 100.0
                            progress_callback(percent, f"Downloading:{raw_percent}")
                        elif isinstance(raw_percent, (int, float)):
                            progress_callback(float(raw_percent) / 100.0, f"Downloading:{raw_percent:.1f}%")
                elif state == "finished":
                    progress_callback(1.0, "Downloading:100.0%")

            opts = {
                "outtmpl": os.path.join(outdir, "%(title)s.%(ext)s"),
                "retries": 3,
                "fragment_retries": 3,
                "extractor_retries": 3,
                "progress_hooks": [module_progress_hook],
                "http_headers": {
                    "Origin": "https://www.bilibili.com",
                    "Referer": "https://www.bilibili.com/",
                },
            }
            browser = _browser_cookie_arg(cookie_browser)
            if cookie_file and os.path.isfile(cookie_file):
                opts["cookiefile"] = cookie_file
            elif browser:
                opts["cookiesfrombrowser"] = (browser,)
            if fmt.lower() == "mp3":
                opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}]
            advanced = advanced or {}
            if advanced.get("write_subtitles"):
                opts.update({"writesubtitles": True, "subtitleslangs": ["all"]})
            if advanced.get("embed_subtitles"):
                opts["embedsubtitles"] = True
            if advanced.get("write_thumbnail"):
                opts["writethumbnail"] = True
            if advanced.get("add_metadata"):
                opts["addmetadata"] = True
            with ytdlp.YoutubeDL(opts) as ydl:
                targets = _read_batch_urls(urls[5:]) if urls.startswith("file:") else [urls]
                ydl.download(targets)
            if finished_callback:
                finished_callback(True, "Finished (module)")
            return

        assert p.stdout
        for line in p.stdout:
            line = line.strip()
            output_lines.append(line)
            logger.get().debug("yt-dlp: %s", line)
            m = _PERCENT_RE.search(line)
            if m and progress_callback:
                progress_callback(float(m.group(1).replace(",", ".")) / 100.0, line)
            elif progress_callback:
                progress_callback(None, line)
        p.wait()
        rc = p.returncode
        if finished_callback:
            finished_callback(rc == 0, "Finished" if rc == 0 else _friendly_error("\n".join(output_lines), rc))
    except Exception as e:
        logger.get().exception("Error during download thread: %s", e)
        if finished_callback:
            finished_callback(False, str(e))
    finally:
        if p is not None and p in _running_processes:
            _running_processes.remove(p)
        if clean_batch_file and os.path.isfile(clean_batch_file):
            try:
                os.remove(clean_batch_file)
            except OSError:
                logger.get().warning("Unable to remove temporary batch file: %s", clean_batch_file)
