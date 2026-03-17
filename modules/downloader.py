# modules/downloader.py
import os
import subprocess
import threading
import shlex
import re
from modules import logger

# 解析 yt-dlp 主動輸出之進度百分比，會回呼給 progress_callback(percent, text)
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)%")

def _build_command(base_dir, urls, outdir, fmt):
    """
    fmt: 'mp4', 'mp3', etc.
    urls: single url string or path-to-txt (if startswith 'file:')
    Return list for subprocess.
    """
    exe = os.path.join(base_dir, "yt-dlp.exe")
    use_local_exe = os.path.exists(exe)

    if isinstance(urls, list):
        # write a temp file?
        pass

    if url := urls:
        pass

    # base args
    if use_local_exe:
        cmd = [exe]
    else:
        # use system 'yt-dlp' if available in PATH; else try python module mode
        cmd = ["yt-dlp"]

    # handle batch file input
    if urls.startswith("file:"):
        txt = urls[5:]
        cmd += ["-a", txt]
    else:
        cmd += [urls]

    # format handling
    if fmt.lower() == "mp3":
        cmd += ["-x", "--audio-format", "mp3"]
    elif fmt.lower() in ("m4a","wav","aac","flac","opus"):
        cmd += ["-x", "--audio-format", fmt.lower()]
    else:
        # video format - best mp4
        # use remux to mp4 when necessary
        cmd += ["-f", "best"]
        # ensure filename includes ext
    # output template
    # use outtmpl to put into outdir
    outtmpl = os.path.join(outdir, "%(title)s.%(ext)s")
    cmd += ["-o", outtmpl, "--newline"]
    return cmd

def run_download(base_dir, urls, outdir, fmt, progress_callback=None, finished_callback=None):
    """
    urls: either single URL str, or 'file:/absolute/path/to/file.txt' for batch
    progress_callback(percent_float, textline)
    finished_callback(success_bool, message)
    """
    t = threading.Thread(target=_download_thread, args=(base_dir, urls, outdir, fmt, progress_callback, finished_callback), daemon=True)
    t.start()
    return t

def _download_thread(base_dir, urls, outdir, fmt, progress_callback, finished_callback):
    os.makedirs(outdir, exist_ok=True)
    cmd = _build_command(base_dir, urls, outdir, fmt)
    logger.get().info("Running command: %s", " ".join(shlex.quote(x) for x in cmd))
    # Windows specific: hide console window
    startupinfo = None
    creationflags = 0
    if os.name == 'nt':
        import subprocess as sp
        startupinfo = sp.STARTUPINFO()
        startupinfo.dwFlags |= sp.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = sp.SW_HIDE
        creationflags = sp.CREATE_NO_WINDOW

    try:
        p = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT, 
            cwd=base_dir,
            startupinfo=startupinfo,
            creationflags=creationflags
        )
    except FileNotFoundError as e:
        # try using python module fallback
        try:
            import yt_dlp as ytdlp  # type: ignore
            logger.get().info("Falling back to yt_dlp python module runner")
            # run via module API (simpler: call ytdlp.YoutubeDL)
            opts = {"outtmpl": os.path.join(outdir, "%(title)s.%(ext)s")}
            if fmt.lower() == "mp3":
                opts["postprocessors"] = [{"key": "FFmpegExtractAudio","preferredcodec":"mp3"}]
            with ytdlp.YoutubeDL(opts) as ydl:
                if urls.startswith("file:"):
                    ydl.download([line.strip() for line in open(urls[5:], encoding="utf-8") if line.strip()])
                else:
                    ydl.download([urls])
            if finished_callback:
                finished_callback(True, "Finished (module)")
            return
        except Exception as ee:
            logger.get().exception("Failed to run yt-dlp: %s", ee)
            if finished_callback:
                finished_callback(False, str(ee))
            return

    # parse output
    try:
        assert p.stdout
        for raw in iter(p.stdout.readline, b""):
            try:
                line = raw.decode(errors="replace").strip()
            except:
                line = str(raw)
            logger.get().debug("yt-dlp: %s", line)
            m = _PERCENT_RE.search(line)
            if m:
                try:
                    percent = float(m.group(1))
                except:
                    percent = None
                if progress_callback and percent is not None:
                    progress_callback(percent/100.0, line)
            # also send general lines as 0 progress messages
            if progress_callback and not m:
                progress_callback(None, line)
        p.wait()
        rc = p.returncode
        if rc == 0:
            logger.get().info("yt-dlp finished successfully")
            if finished_callback:
                finished_callback(True, "Finished")
        else:
            logger.get().warning("yt-dlp returned code %s", rc)
            if finished_callback:
                finished_callback(False, f"Exit code {rc}")
    except Exception as e:
        logger.get().exception("Error during download thread: %s", e)
        if finished_callback:
            finished_callback(False, str(e))
