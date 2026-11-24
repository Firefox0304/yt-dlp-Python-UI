# modules/webui.py
"""v6.0 網頁介面：用 pywebview 開無邊框視窗，載入 web/index.html，並把 Python 後端開放給網頁呼叫。

網頁 → Python：Api 類別的公開方法（window.pywebview.api.xxx）。
Python → 網頁：Api._emit() 以 evaluate_js 呼叫網頁的 window.__py(事件)。
下載、設定、歷史等實際邏輯全部沿用 downloader / config_manager / history。
"""
import importlib.metadata
import json
import os
import re
import sys
import threading
import time
import webbrowser

from modules import check, config_manager, diagnostics, downloader, history, i18n, logger, update_summary, updater, utils
from modules import engine as engine_mod
from modules.i18n import t
from modules.progress import parse_progress_line
from modules.urls import extract_url
from modules.version import APP_NAME, APP_VERSION
from modules.winshell import WindowMixin

LOG = logger.get()
THEMES = ("Light", "System", "Dark")
# yt-dlp 在每支影片開始下載時會輸出的訊息：播放清單的「第 N 支」，以及每支影片的「Downloading N format(s)」
_VIDEO_START_RE = re.compile(r"^\[(?:download\] Downloading item \d+ of \d+|info\] .+: Downloading \d+ format\(s\))")
HISTORY_VIEW_COUNT = 50
PROGRESS_INTERVAL = 0.1  # 進度事件最多每 0.1 秒送一次，避免塞滿網頁

# 會存進 settings.json 的布林／文字設定：網頁欄位名 → (設定檔鍵, 轉換函式)
_SETTING_FIELDS = {
    "fmt": ("last_format", str),
    "quality": ("last_quality", str),
    "cookie_browser": ("cookie_browser", str),
    "number": ("number_playlist_items", bool),
    "archive": ("use_archive", bool),
    "boost": ("concurrent_download", bool),
    "frags": ("concurrent_fragments", downloader.normalize_fragments),
    "subs": ("write_subtitles", bool),
    "embed": ("embed_subtitles", bool),
    "thumb": ("write_thumbnail", bool),
    "meta": ("add_metadata", bool),
    "sub_langs": ("subtitle_langs", lambda v: str(v).strip() or downloader.DEFAULT_SUBTITLE_LANGS),
    "open_file": ("open_file_after_download", bool),
    "open_folder": ("open_folder_after_download", bool),
}


def _dialog_type(webview, name):
    """pywebview 6 以後用 webview.FileDialog.XXX，舊版用 webview.XXX_DIALOG。"""
    file_dialog = getattr(webview, "FileDialog", None)
    return getattr(file_dialog, name) if file_dialog else getattr(webview, f"{name}_DIALOG")


class Api(WindowMixin):
    # 注意：pywebview 會把公開屬性都曝露給網頁，所以內部狀態一律用底線開頭，只留要給網頁呼叫的方法公開。
    def __init__(self, base_dir, cfg, config_path):
        self._base_dir = base_dir
        self._cfg = cfg
        self._config_path = config_path
        self._window = None
        self._controller = None
        self._last_progress = 0.0
        self._lock = threading.Lock()
        self._job = {}
        self._prompt = None            # 啟動檢查找到的待處理項目（網頁剛載入時也能補領）
        self._check_started = False
        self._installing = False
        self._install_lock = threading.Lock()
        self._init_window_state()  # 視窗控制用的內部狀態（見 modules/winshell.py）

    # ----------------------------------------------------------- Python → 網頁
    def _emit(self, **event):
        if self._window is None:
            return
        try:
            self._window.evaluate_js(f"window.__py && window.__py({json.dumps(event)})")
        except Exception as exc:  # noqa: BLE001 — 視窗關閉中時送事件會失敗，忽略即可
            LOG.debug("emit failed: %s", exc)

    # ----------------------------------------------------------- 設定
    def _stored_path(self, path):
        """在程式資料夾內就存相對路徑，搬動整個資料夾時設定仍有效。"""
        try:
            rel = os.path.relpath(path, self._base_dir)
            if not rel.startswith(".."):
                return rel
        except ValueError:  # 不同磁碟機
            pass
        return path

    def _resolve_dir(self, stored):
        stored = (stored or "Download").strip()
        return stored if os.path.isabs(stored) else os.path.normpath(os.path.join(self._base_dir, stored))

    def get_state(self):
        cfg = self._cfg
        appearance = cfg.get("appearance")
        state = {
            "version": APP_VERSION,
            "appearance": appearance if appearance in THEMES else "System",
            "language": i18n.normalize_setting(cfg.get("language")),
            "lang": i18n.current(),
            "strings": i18n.bundle(),
            "outdir": self._resolve_dir(cfg.get("download_path")),
            "default_dir": os.path.join(self._base_dir, "Download"),
        }
        defaults = config_manager.DEFAULT_CONFIG
        for field, (key, convert) in _SETTING_FIELDS.items():
            state[field] = convert(cfg.get(key, defaults.get(key)))
        return state

    def set_language(self, setting):
        """切換介面語言：setting 是 'auto' 或語言代號。存進設定檔、讓 Python 端訊息改用新語言，並回傳整包文字給網頁。"""
        setting = i18n.normalize_setting(setting)
        self._cfg["language"] = setting
        i18n.set_language(i18n.resolve(setting))
        try:
            config_manager.save(self._config_path, self._cfg)
        except OSError as exc:
            LOG.warning("Failed saving config: %s", exc)
        return {"lang": i18n.current(), "strings": i18n.bundle()}

    def save_settings(self, data):
        for key, value in (data or {}).items():
            if key == "appearance":
                self._cfg["appearance"] = value if value in THEMES else "System"
            elif key == "outdir":
                self._cfg["download_path"] = self._stored_path(str(value))
            elif key in _SETTING_FIELDS:
                cfg_key, convert = _SETTING_FIELDS[key]
                self._cfg[cfg_key] = convert(value)
        try:
            config_manager.save(self._config_path, self._cfg)
        except OSError as exc:
            LOG.warning("Failed saving config: %s", exc)
        return True

    # ----------------------------------------------------------- 檔案對話框
    def choose_folder(self):
        import webview
        result = self._window.create_file_dialog(
            _dialog_type(webview, "FOLDER"), directory=self._resolve_dir(self._cfg.get("download_path")))
        return result[0] if result else None

    def pick_cookie_file(self):
        import webview
        result = self._window.create_file_dialog(
            _dialog_type(webview, "OPEN"), file_types=(t("dialog.cookie"), t("dialog.all")))
        return result[0] if result else None

    def pick_batch_file(self):
        """選批次清單並先讀一次，確認可用；網址欄位會填成 file:<路徑>，開始下載時才真正展開。"""
        import webview
        result = self._window.create_file_dialog(
            _dialog_type(webview, "OPEN"), file_types=(t("dialog.text"), t("dialog.all")))
        if not result:
            return {"ok": False, "cancelled": True}
        return self._check_batch(result[0])

    def load_example(self):
        path = os.path.join(self._base_dir, "example", "example.txt")
        if not os.path.exists(path):
            return {"ok": False, "missing": True,
                    "error": t("err.example_missing")}
        return self._check_batch(path)

    @staticmethod
    def _check_batch(path):
        try:
            urls, ignored = downloader.read_batch(path)
        except OSError as exc:
            return {"ok": False, "error": t("err.list_read", error=exc)}
        if not urls:
            return {"ok": False, "error": t("err.list_empty")}
        return {"ok": True, "path": path, "count": len(urls), "ignored": ignored}

    # ----------------------------------------------------------- 下載
    def prepare_download(self, payload):
        """開始前先分析網址：含播放清單時，回傳需要詢問使用者的內容。"""
        outdir = self._resolve_dir(payload.get("outdir"))
        target = (payload.get("url") or "").strip()
        try:
            urls = downloader.expand_target(target)
        except OSError as exc:
            return {"ok": False, "error": t("err.list_read", error=exc)}
        if not target:
            return {"ok": False, "error": t("err.no_url")}
        if not urls:
            return {"ok": False, "error": t("err.list_empty" if target.startswith("file:") else "err.invalid_url")}
        kinds = [downloader.classify_playlist_url(url) for url in urls]
        if not target.startswith("file:"):
            if kinds[0] == "none":
                return {"ok": True, "ask": None, "url": urls[0]}  # url：整理後的網址，網頁會據此更新網址欄
            return {"ok": True, "ask": "single", "kind": kinds[0], "url": urls[0],
                    "default_name": downloader.next_playlist_folder(outdir)}
        playlist_kinds = [k for k in kinds if k != "none"]
        if not playlist_kinds:
            return {"ok": True, "ask": None}
        return {"ok": True, "ask": "batch", "total": len(playlist_kinds), "pure": kinds.count("playlist")}

    def _plan_items(self, target, urls, outdir, decision):
        kinds = [downloader.classify_playlist_url(url) for url in urls]
        mode = (decision or {}).get("mode", "single")
        if not target.startswith("file:"):  # 單一網址
            url, kind = urls[0], kinds[0]
            if kind == "none" or mode != "playlist":
                return [downloader.DownloadItem(url)]
            name = downloader.sanitize_folder_name((decision or {}).get("name", "")) \
                or downloader.next_playlist_folder(outdir)
            return [downloader.DownloadItem(url, playlist=True, folder=name)]
        items, reserved = [], set()  # 批次清單：資料夾到開始下載才建立，先預留名稱避免同一批次撞名
        for url, kind in zip(urls, kinds):
            if kind == "none":
                items.append(downloader.DownloadItem(url))
            elif mode == "playlist":
                name = downloader.next_playlist_folder(outdir, reserved)
                reserved.add(name)
                items.append(downloader.DownloadItem(url, playlist=True, folder=name))
            elif kind == "mixed":  # 只下載單一影片：影片加清單 → 只取該影片；純清單 → 略過
                items.append(downloader.DownloadItem(url))
        return items

    def start_download(self, payload, decision=None):
        with self._lock:
            if self._controller is not None:
                return {"ok": False, "error": t("err.busy")}
            outdir = self._resolve_dir(payload.get("outdir"))
            target = (payload.get("url") or "").strip()
            try:
                urls = downloader.expand_target(target)
            except OSError as exc:
                return {"ok": False, "error": t("err.batch_read", error=exc)}
            if not urls:
                key = "err.no_url" if not target else ("err.list_empty" if target.startswith("file:") else "err.invalid_url")
                return {"ok": False, "error": t(key)}
            items = self._plan_items(target, urls, outdir, decision)
            if not items:
                return {"ok": False, "error": t("err.only_playlists_skipped")}
            cfg = self._cfg
            opts = downloader.DownloadOptions(
                fmt=(payload.get("fmt") or "mp4").strip(),
                quality=(payload.get("quality") or "預設").strip(),
                cookie_browser=payload.get("cookie_browser", "無"),
                cookie_file=(payload.get("cookie_file") or "").strip(),  # 路徑刻意不存進設定檔
                write_subtitles=bool(payload.get("subs")),
                embed_subtitles=bool(payload.get("embed")),
                subtitle_langs=(payload.get("sub_langs") or "").strip() or downloader.DEFAULT_SUBTITLE_LANGS,
                write_thumbnail=bool(payload.get("thumb")),
                add_metadata=bool(payload.get("meta")),
                number_playlist_items=bool(payload.get("number")),
                use_archive=bool(payload.get("archive")),
                concurrent_download=bool(payload.get("boost")),
                concurrent_fragments=downloader.normalize_fragments(payload.get("frags")),
                filename_template=cfg.get("filename_template") or downloader.DEFAULT_FILENAME_TEMPLATE,
            )
            settings = {k: payload.get(k) for k in _SETTING_FIELDS if k in payload}
            self.save_settings({**settings, "outdir": outdir})
            playlist_items = [item for item in items if item.playlist]
            open_dir = playlist_items[0].target_dir(outdir) if len(items) == 1 and playlist_items else outdir
            self._job = {"outdir": outdir, "open_dir": open_dir, "fmt": opts.fmt, "quality": opts.quality,
                         "open_file": bool(payload.get("open_file")),
                         "open_folder": bool(payload.get("open_folder"))}
            self._controller = downloader.run_download(
                self._base_dir, items, outdir, opts,
                progress_callback=self._on_progress, log_callback=self._on_log,
                item_started=self._on_item_started, item_finished=self._on_item_finished,
                finished_callback=self._on_finished)
        self._emit(type="state", busy=True)
        self._emit(type="log", text=t("log.start", target=target, fmt=opts.fmt, quality=opts.quality, outdir=outdir),
                   cls="hl")
        return {"ok": True}

    def cancel_download(self):
        controller = self._controller
        if controller is not None:
            self._emit(type="log", text=t("log.cancelling"))
            controller.cancel()
        return True

    def _on_progress(self, fraction, text):
        now = time.monotonic()
        if now - self._last_progress < PROGRESS_INTERVAL and (fraction is None or fraction < 1):
            return
        self._last_progress = now
        info = parse_progress_line(text)  # 有分片數才表示這是分片下載，-N 才有作用
        self._emit(type="progress", pct=None if fraction is None else round(fraction * 100, 1),
                   speed=info.speed if info else "", eta=info.eta if info else "",
                   frag=info.fragmented if info else False)

    def _on_log(self, text):
        if _VIDEO_START_RE.match(text):
            self._emit(type="video_start")  # 通知網頁：新的一支影片開始，下一筆進度要重新判斷能不能分片
        self._emit(type="log", text=text)

    def _on_item_started(self, index, total, item):
        self._emit(type="video_start")  # 備援：有些網站不會輸出上面那種訊息
        if total > 1:
            self._emit(type="log", text=t("log.item_start", index=index, total=total, url=item.url))
        if item.playlist:
            self._emit(type="log", text=t("log.item_playlist", folder=item.folder))

    def _on_item_finished(self, index, total, item, success, message, files):
        if total > 1:
            status = t("hist.ok") if success else t("hist.fail")
            self._emit(type="log", text=t("log.item_result", index=index, total=total, status=status, message=message),
                       cls="ok" if success else "")
        if message == t("state.cancelled"):
            return  # 使用者主動取消的項目不計入歷史
        job = self._job
        try:
            history.add(self._base_dir, item.url, job.get("outdir", ""), job.get("fmt", ""),
                        job.get("quality", ""), success, message, files,
                        title=downloader.derive_title(item, files))
        except Exception as exc:  # noqa: BLE001 — 歷史紀錄失敗不該影響下載
            LOG.warning("Failed writing history: %s", exc)

    def _on_finished(self, result):
        job = self._job
        self._controller = None
        failures = [{"url": url, "message": msg} for url, msg in result.failures[:3]]
        self._emit(type="state", busy=False)
        self._emit(type="done", ok=result.success, cancelled=result.cancelled, message=result.message,
                   code=result.code, failures=failures, more=max(0, len(result.failures) - 3),
                   ffmpeg_url=downloader.FFMPEG_DOWNLOAD_URL if result.code == "ffmpeg" else "")
        if result.succeeded:
            if job.get("open_folder"):
                utils.open_path(job.get("open_dir") or job.get("outdir", ""))
            if job.get("open_file"):
                self._open_result_file(result.files)

    def _open_file_safely(self, path):
        """用系統預設程式開啟下載結果；可執行檔、腳本等類型一律不開（見 utils.is_safe_to_open）。"""
        if not utils.is_safe_to_open(path):
            LOG.warning("Refused to open file of unsafe type: %s", path)
            self._emit(type="log", text=t("log.open_blocked", name=os.path.basename(path)))
            return
        utils.open_path(path)

    def _open_result_file(self, files):
        for path in reversed(files):
            if os.path.isfile(path):
                self._open_file_safely(path)
                return
        latest = utils.latest_media_file(self._job.get("outdir", ""))  # 後備：輸出資料夾中最新的媒體檔
        if latest:
            self._open_file_safely(latest)

    def open_file_location(self, outdir=""):
        """快速操作「開啟檔案位置」：在檔案總管選取最近一次下載完成的檔案。
        找法：歷史紀錄中最近一筆成功且檔案還在的 → 輸出資料夾裡最新的媒體檔 → 直接開輸出資料夾。
        回傳 {"ok": bool, "mode": "file"|"folder"}；什麼都找不到時 {"ok": False, "missing": True}。"""
        folder = self._resolve_dir(outdir or self._cfg.get("download_path"))
        path = history.latest_existing_file(history.load(self._base_dir)) or utils.latest_media_file(folder)
        if path:
            return {"ok": utils.reveal_in_file_manager(path), "mode": "file"}
        if os.path.isdir(folder):
            return {"ok": utils.open_path(folder), "mode": "folder"}
        return {"ok": False, "missing": True}

    def open_ffmpeg_page(self):
        webbrowser.open(downloader.FFMPEG_DOWNLOAD_URL)
        return True

    # ----------------------------------------------------------- 影片資訊
    def fetch_info(self, url, cookie_browser="無", cookie_file=""):
        url = (url or "").strip()
        if not url:
            return {"ok": False, "error": t("err.no_url")}
        if url.startswith("file:"):
            return {"ok": False, "error": t("err.batch_info")}
        url = extract_url(url)  # 整理成實際要讀取的網址（見 modules/urls.py）
        if url is None:
            return {"ok": False, "error": t("err.invalid_url")}

        def work():
            try:
                opts = downloader.DownloadOptions(cookie_browser=cookie_browser, cookie_file=cookie_file or "")
                info = downloader.fetch_info(self._base_dir, url, opts)
                length = ""
                if info["duration"]:
                    minutes, seconds = divmod(int(info["duration"]), 60)
                    hours, minutes = divmod(minutes, 60)
                    length = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
                lines = [t("info.title", title=info["title"])]
                if info["uploader"]:
                    lines.append(t("info.uploader", uploader=info["uploader"]))
                if info["is_playlist"]:
                    lines.append(t("info.playlist", count=info["entry_count"]))
                if length:
                    lines.append(t("info.length", length=length))
                if info["heights"]:
                    lines.append(t("info.heights", list=t("info.sep").join(f"{h}P" for h in info["heights"])))
                for line in lines:
                    self._emit(type="log", text=line, cls="hl")
            except RuntimeError as exc:
                self._emit(type="log", text=str(exc))
            finally:
                self._emit(type="info_done")

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True, "url": url}

    # ----------------------------------------------------------- 歷史、說明、診斷、檢查更新
    def get_history(self):
        rows = []
        for item in history.load(self._base_dir)[-HISTORY_VIEW_COUNT:][::-1]:
            title = history.display_title(item)
            rows.append({
                "time": str(item.get("time", "")).replace("T", "_"),
                "ok": bool(item.get("success")),
                "fmt": str(item.get("format") or "").upper() or "-",
                "quality": str(item.get("quality") or "-"),
                "title": title if len(title) <= 80 else title[:79] + "…",
                "url": str(item.get("url", "")),
            })
        return rows

    def clear_history(self):
        history.clear(self._base_dir)
        return True

    def open_target(self, target):
        """歷史紀錄的標題連結：http(s) 網址用瀏覽器開，file: 只開存在且類型安全的本機檔案；其他一律不處理。"""
        target = str(target or "").strip()
        if target.lower().startswith(("http://", "https://")):
            if not utils.is_safe_web_url(target):
                LOG.warning("Refused to open malformed URL: %r", target[:200])
                return False
            webbrowser.open(target)
            return True
        if target.lower().startswith("file:"):
            path = target[5:]
            if not os.path.isfile(path) or not utils.is_safe_to_open(path):
                LOG.warning("Refused to open file target: %s", path)
                return False
            return bool(utils.open_path(path))
        return False

    def get_help(self):
        return {"text": t("help.text"), "credit": t("credit")}

    def run_diagnostics(self):
        def work():
            try:
                self._emit(type="diag", text=self._diagnostics_text())
            except Exception as exc:  # noqa: BLE001 — 診斷本身出錯也要讓按鈕恢復
                LOG.exception("Diagnostics failed")
                self._emit(type="diag", text=t("diag.fail", error=exc))

        threading.Thread(target=work, daemon=True).start()
        return True

    def export_diagnostics(self, report_text=""):
        """把診斷報告、日誌、設定打包成 zip（路徑中的使用者名稱會遮蔽，不含 Cookie 與歷史）。
        回傳 {"ok": True, "path": ...}，使用者取消存檔時 {"ok": False, "cancelled": True}。"""
        import webview
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            result = self._window.create_file_dialog(
                _dialog_type(webview, "SAVE"), save_filename=f"yt-dlp-ui-diagnostics-{stamp}.zip",
                file_types=(t("dialog.zip"),))
            if not result:
                return {"ok": False, "cancelled": True}
            path = str(result[0] if isinstance(result, (list, tuple)) else result)
            if not path.lower().endswith(".zip"):
                path += ".zip"
            report = str(report_text or "").strip() or self._diagnostics_text()
            diagnostics.build_package(path, self._base_dir, report, self._cfg)
            LOG.info("Diagnostics package exported: %s", path)
            return {"ok": True, "path": path}
        except Exception as exc:  # noqa: BLE001 — 匯出失敗要讓網頁顯示原因，而不是整個卡住
            LOG.exception("Diagnostics export failed")
            return {"ok": False, "error": str(exc)}

    def _diagnostics_text(self):
        eng = engine_mod.resolve_engine(self._base_dir)
        version = engine_mod.get_version(eng) if eng else None
        js = engine_mod.find_js_runtimes()
        try:
            webview_version = importlib.metadata.version("pywebview")
        except importlib.metadata.PackageNotFoundError:
            webview_version = t("diag.unknown")
        return t("diag.report",
                 platform=utils.describe_platform(),
                 python=sys.version.split()[0],
                 ytdlp_version=version or t("diag.not_installed"),
                 ytdlp_source=engine_mod.describe(eng) if eng else t("diag.not_found"),
                 ffmpeg=downloader.find_ffmpeg(self._base_dir) or t("diag.not_found"),
                 webview=webview_version,
                 js=", ".join(js) if js else t("diag.js_none"),
                 outdir=self._resolve_dir(self._cfg.get("download_path")),
                 config=self._config_path)

    def check_update(self):
        if self._controller is not None:
            return {"ok": False, "error": t("err.update_busy")}

        def work():
            try:
                eng = engine_mod.resolve_engine(self._base_dir)
                results = updater.check_all(eng, log=lambda text: self._emit(type="log", text=text),
                                            base_dir=self._base_dir, auto_update=False)
            except Exception as exc:  # noqa: BLE001
                LOG.exception("Update check failed")
                failed = {"status": "error", "message": str(exc)}
                results = {"ytdlp": failed, "python": failed, "ffmpeg": failed, "deno": failed}
            text, problem, suggest_python = update_summary.summarize_update(results)
            ffmpeg_missing = (results.get("ffmpeg") or {}).get("status") == "missing"
            command = None  # (按鈕文字, 命令, 種類)
            if suggest_python:
                winget = (results.get("python") or {}).get("winget_command")  # 只有 Windows 且 winget 可用才有
                if winget:
                    label = "update.copy_upgrade" if "upgrade" in winget[1] else "update.copy_install"
                    command = {"label": t(label), "text": winget[1], "kind": "python"}
            if command:
                title_key, note_key = update_summary.COMMAND_NOTES[command["kind"]]
                command["title"], command["note"] = t(title_key), t(note_key)
            self._emit(type="update_done", text=text, problem=problem,
                       items=check.items_from_results(results),  # 有新版的項目：網頁接上同一組安裝彈窗
                       python_url=updater.PYTHON_DOWNLOADS_URL if suggest_python else "",
                       project_url=updater.PROJECT_URL if (results.get("ui") or {}).get("status") == "update_available" else "",
                       ffmpeg_url=downloader.FFMPEG_DOWNLOAD_URL if ffmpeg_missing else "",
                       command=command)

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def open_python_page(self):
        webbrowser.open(updater.PYTHON_DOWNLOADS_URL)
        return True

    def open_project_page(self):
        """UI 有新版時的「yt-dlp-Python-UI」按鈕：開專案庫（網址固定寫在後端，網頁不能指定）。"""
        webbrowser.open(updater.PROJECT_URL)
        return True

    # ----------------------------------------------------------- 啟動檢查與自動安裝（邏輯在 modules/check.py）
    def _start_check(self, *_):
        """視窗顯示後呼叫一次：在背景檢查缺少的程式與 yt-dlp 新版。"""
        if self._check_started:
            return
        self._check_started = True
        threading.Thread(target=check.run_startup_check, args=(self._base_dir, self._deliver_prompt),
                         daemon=True).start()

    def _deliver_prompt(self, items, ui=None):
        """items：要安裝／更新的項目；ui：UI（本程式）有新版時是 {"current", "latest"}，否則 None。"""
        self._prompt = {"items": items, "ui": ui}
        self._emit(type="install_prompt", items=items, ui=ui)

    def get_install_prompt(self):
        """網頁載入完成後補領：避免檢查比網頁先做完時事件落空。同一組項目網頁只會顯示一次。"""
        return self._prompt

    def confirm_install(self, items):
        """使用者在彈窗按「確認」：開始安裝（項目只認白名單，指令都由 check.py 組裝）。"""
        if self._controller is not None:
            return {"ok": False, "error": t("check.err.download_busy")}
        clean = check.sanitize_items(items)
        if not clean:
            return {"ok": False, "error": t("check.err.no_items")}
        with self._install_lock:
            if self._installing:
                return {"ok": False, "error": t("check.err.installing")}
            self._installing = True
        threading.Thread(target=self._install_worker, args=(clean,), daemon=True).start()
        return {"ok": True}

    def _install_worker(self, items):
        try:
            check.run_install(self._base_dir, items, self._emit)
            self._emit(type="install_done", ok=True)
            time.sleep(1.0)  # 讓網頁顯示「完成」一下
            check.restart(self._base_dir)
        except check.InstallError as exc:
            self._fail_install(exc.item, str(exc), exc.pages)
            return
        except Exception as exc:  # noqa: BLE001 — 任何意外都要讓網頁顯示原因，並恢復可操作
            LOG.exception("Install failed")
            self._fail_install(None, str(exc), ())
            return
        self.win_close(animate=False)  # 新程序已啟動，關閉目前視窗

    def _fail_install(self, item, message, pages):
        with self._install_lock:
            self._installing = False
        self._emit(type="install_fail", item=item, message=message, pages=list(pages))

    def open_download_page(self, item):
        """安裝失敗時的下載頁（只開白名單內的網址）。"""
        url = check.DOWNLOAD_PAGES.get(item)
        if url:
            webbrowser.open(url)
        return bool(url)

    # ----------------------------------------------------------- 視窗事件
    def on_closing(self):
        downloader.terminate_all_downloads()


def main(base_dir):
    import webview

    os.makedirs(os.path.join(base_dir, "Download"), exist_ok=True)
    logger.setup_logging(os.path.join(base_dir, "logs"))
    config_path = os.path.join(base_dir, "config", "settings.json")
    cfg = config_manager.load_or_init(config_path)
    config_manager.ensure_example(base_dir, config_path, cfg)

    i18n.init(base_dir)
    i18n.set_language(i18n.resolve(cfg.get("language")))
    api = Api(base_dir, cfg, config_path)
    window = webview.create_window(
        f"{APP_NAME} v{APP_VERSION}", os.path.join(base_dir, "web", "index.html"),
        js_api=api, width=1120, height=1040, min_size=(900, 680),
        frameless=True, easy_drag=False, resizable=True)  # 標題列由網頁自己畫；拖曳移動由 win_start_drag 處理
    api._window = window
    window.events.closing += api.on_closing
    for name in ("shown", "moved"):  # 視窗顯示與移到別的螢幕（或工作列位置變了）時，更新最大化範圍
        event = getattr(window.events, name, None)
        if event is not None:
            event += (lambda *_: api._apply_work_area_throttled())
    shown = getattr(window.events, "shown", None)
    if shown is not None:  # 視窗建立後補上最小化框，Win+M 才會處理這個無邊框視窗
        shown += (lambda *_: (api._enable_minimize_box(), api._install_minimize_hook(), api._start_minimize_watcher()))
    # 視窗顯示後才開始檢查缺少的程式與更新（沒有 shown 事件的舊版 pywebview 改用 loaded）
    trigger = shown if shown is not None else getattr(window.events, "loaded", None)
    if trigger is not None:
        trigger += api._start_check
    for name, value in (("maximized", True), ("restored", False)):
        event = getattr(window.events, name, None)
        if event is not None:
            event += (lambda *_, v=value: api._set_maximized(v))
    webview.start()
    downloader.terminate_all_downloads()
