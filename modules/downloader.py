# modules/downloader.py
import os
import subprocess
import threading
import shlex
import re
from modules import logger

_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)%")
_running_processes = []


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


def _build_command(base_dir, urls, outdir, fmt, quality="預設", cookie_browser="無", cookie_file=""):
    """Build a yt-dlp command for one URL or a file: batch list."""
    exe = os.path.join(base_dir, "yt-dlp.exe")
    cmd = [exe] if os.path.exists(exe) else ["yt-dlp"]

    cmd += ["--no-update", "--newline", "--retries", "3", "--fragment-retries", "3",
            "--extractor-retries", "3", "--retry-sleep", "http:exp=1:20"]

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
    return cmd


def run_download(base_dir, urls, outdir, fmt, quality="預設", cookie_browser="無", cookie_file="",
                 progress_callback=None, finished_callback=None):
    t = threading.Thread(
        target=_download_thread,
        args=(base_dir, urls, outdir, fmt, quality, cookie_browser, cookie_file,
              progress_callback, finished_callback),
        daemon=True,
    )
    t.start()
    return t


def _friendly_error(output, rc):
    text = output or ""
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


def _download_thread(base_dir, urls, outdir, fmt, quality, cookie_browser, cookie_file,
                     progress_callback, finished_callback):
    os.makedirs(outdir, exist_ok=True)
    cmd = _build_command(base_dir, urls, outdir, fmt, quality, cookie_browser, cookie_file)
    logger.get().info("Running command: %s", " ".join(shlex.quote(x) for x in cmd))
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
        try:
            p = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=base_dir, startupinfo=startupinfo, creationflags=creationflags,
                text=True, encoding="utf-8", errors="replace",
            )
            _running_processes.append(p)
        except FileNotFoundError:
            # Module fallback is useful when the user installed yt-dlp with pip.
            import yt_dlp as ytdlp  # type: ignore
            opts = {
                "outtmpl": os.path.join(outdir, "%(title)s.%(ext)s"),
                "retries": 3,
                "fragment_retries": 3,
                "extractor_retries": 3,
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
            with ytdlp.YoutubeDL(opts) as ydl:
                targets = [line.strip() for line in open(urls[5:], encoding="utf-8") if line.strip()] if urls.startswith("file:") else [urls]
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
                progress_callback(float(m.group(1)) / 100.0, line)
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
