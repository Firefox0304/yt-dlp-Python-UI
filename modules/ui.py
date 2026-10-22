# modules/ui.py
import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox

import customtkinter as ctk

from modules import config_manager, dialogs, downloader, fonts, history, logger, updater, utils
from modules import engine as engine_mod
from modules.version import APP_VERSION

LOG = logger.get()

APP_TITLE = f"yt-dlp-python-UI v{APP_VERSION} | 作者：Firefox_0304 | 協助Bot：ChatGPT  Manus  Claude"
BANNER_TEXT = f"yt-dlp-download-python-UI v{APP_VERSION}    作者：Firefox_0304    協助Bot：ChatGPT  Manus  Claude"

FORMATS = ["mp4", "mp3", "mkv", "webm", "wav", "m4a", "flac"]
QUALITIES = ["預設", "8K", "4K", "2K", "1080P", "720P", "480P", "240P"]
BROWSERS = ["無", "chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium"]

# 主題：切換鈕由左到右的順序；顯示的文字與設定檔存的值相同。預設 System（跟隨 Windows 的淺色／深色設定）
APPEARANCE_MODES = ("Light", "System", "Dark")
DEFAULT_APPEARANCE = "System"

MAX_LOG_LINES = 3000
HISTORY_VIEW_COUNT = 50
PUMP_INTERVAL_MS = 50

HELP_TEXT = (
    "【基本使用】\n"
    "在『網址』欄位貼上網址（Enter 可直接開始），或按『匯入批次下載』選取 .txt 清單\n"
    "（每行一個網址，# 開頭為註解，重複網址會自動略過，並依序逐筆下載）。\n\n"
    "【取消與資訊】\n"
    "下載中可按『取消下載』；『讀取影片資訊』可先查看標題與實際可用的畫質。\n\n"
    "【畫質】\n"
    "選 720P 代表「最高到 720P」；若影片沒有這麼低的畫質，會改取最低畫質。\n\n"
    "【播放清單】\n"
    "貼上含播放清單的 YouTube 網址時，開始下載前會詢問要下載單一影片還是整個清單。\n"
    "選擇整個清單會建立資料夾，可自訂名稱；留空則自動命名為「播放清單N」。\n"
    "勾選右側『播放清單自動編號』會在檔名前加上 01、02… 的順序編號（只對整個清單有效）。\n\n"
    "【Cookie】\n"
    "需要登入時，請用瀏覽器擴充功能匯出 cookies.txt 後按『匯入 Cookies.txt』（優先使用）；\n"
    "出於安全考量，Cookies.txt 的路徑不會被記住，每次啟動都要重新選擇。\n"
    "也可在右側選擇瀏覽器直接讀取 Cookie（Chrome / Edge 新版可能無法解密）。\n\n"
    "【進階】\n"
    "『記錄已下載』會用 config/archive.txt 記住下載過的影片，之後自動略過重複。"
)


def normalize_appearance(value):
    """設定檔裡沒有或不認得的值，一律使用預設的 System。"""
    return value if value in APPEARANCE_MODES else DEFAULT_APPEARANCE


class _ProgressBarWithText(ctk.CTkProgressBar):
    """直接在 CTkProgressBar 的 Canvas 上畫文字，避免子元件蓋住進度填色。

    使用了 customtkinter 的內部屬性；若未來版本改名，文字會停用但進度條仍可正常使用。
    """

    font_family = "Segoe UI"  # App 啟動時會改成實際可用的介面字型

    def __init__(self, *args, **kwargs):
        self._status_text = None
        self._detail_text = None
        super().__init__(*args, **kwargs)

    def _draw(self, no_color_updates=False):
        super()._draw(no_color_updates)
        try:
            self._canvas.delete("progress_text")
            text = self._status_text or self._detail_text or f"{self._determinate_value * 100:.1f}%"
            self._canvas.create_text(
                self._apply_widget_scaling(self._current_width) / 2,
                self._apply_widget_scaling(self._current_height) / 2,
                text=text, fill="white", font=(self.font_family, 10, "bold"), tags="progress_text",
            )
        except Exception:  # noqa: BLE001
            pass

    def set_status(self, text):
        self._status_text = text
        self._draw(no_color_updates=True)

    def show(self, fraction=None, text=None):
        """一次更新進度與文字（只重繪一次）。"""
        if text is not None:
            self._detail_text = text
        if fraction is not None:
            self.set(max(0.0, min(1.0, fraction)))
        else:
            self._draw(no_color_updates=True)

    def reset(self):
        self._status_text = None
        self._detail_text = None
        self.set(0.0)


class App:
    def __init__(self, cfg, base_dir, startup=None):
        self.startup = startup  # 啟動畫面回呼（可為 None）
        self.poll_hooks = []  # 每個 _pump 週期呼叫一次的函式（主執行緒）
        self.cfg = cfg
        self.base_dir = base_dir
        self.config_path = os.path.join(base_dir, "config", "settings.json")
        self.default_dir = os.path.join(base_dir, "Download")

        self.ui_queue = queue.Queue()  # 背景執行緒 → 主執行緒
        self.controller = None
        self.job = {}
        self.busy = False
        self._closing = False
        self._pump_id = None

        appearance = normalize_appearance(cfg.get("appearance"))
        ctk.set_appearance_mode(appearance)  # "System" 會跟著 Windows 的淺色／深色設定，系統切換時也會即時更新
        ctk.set_default_color_theme("blue")

        self.root = ctk.CTk()
        if startup:
            self.root.withdraw()  # 建立完成前先不顯示，由啟動畫面頂著
            startup.attach(self.root)
            startup.progress(80, "正在建立主視窗")
        # 介面字型：優先用隨附的 Noto Sans TC，否則退回系統字型；必須在建立任何元件之前套用
        self.font_family = fonts.ui_family(self.root)
        applied = fonts.apply_to_customtkinter(ctk, self.font_family)
        _ProgressBarWithText.font_family = self.font_family or _ProgressBarWithText.font_family
        LOG.info("UI font family: %s（套用方式：%s；元件預設字型：%s；註冊方式：%s）", self.font_family or "(theme default)",
                 applied or "未套用", fonts.theme_default_family(ctk) or "未知", fonts.active_mode())
        if self.font_family:  # 問 Tk 每個字實際用哪個字型畫，這才是畫面上真正的結果
            probe = fonts.probe(self.root, self.font_family)
            LOG.info("Tk 實際繪製字型：%s", fonts.describe_probe(probe))
            if fonts.probe_mismatches(probe, self.font_family):
                LOG.warning("部分字元沒有使用 %s（見上一行），畫面會退回其他字型", self.font_family)
        self.root.title(APP_TITLE)
        self.root.geometry("1100x700")
        self.root.minsize(1020, 640)
        icon = utils.find_app_icon(base_dir)
        if icon:
            try:
                self.root.iconbitmap(icon)
            except Exception:  # noqa: BLE001
                pass
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._startup_step(84, "正在建立版面")
        self._build_layout()
        self._startup_step(89, "正在建立下載選項")
        self._build_left()
        self._startup_step(94, "正在建立功能面板")
        self._build_right()
        self._build_bottom()
        self._bind_focus_release()

        self._log("狀態/日誌…（會顯示 yt-dlp 輸出）")
        self._pump_id = self.root.after(PUMP_INTERVAL_MS, self._pump)

        if startup:
            startup.progress(99, "即將完成")
            self._show_window()
            startup.ready(self)

    def _startup_step(self, value, text):
        if self.startup:
            self.startup.progress(value, text)

    def _show_window(self):
        """主視窗完全建立後才顯示，使用者不會看到半成品。"""
        self.root.update_idletasks()
        self.root.deiconify()
        self.root.lift()
        self.root.update()

    # ------------------------------------------------------------------ #
    # 版面
    # ------------------------------------------------------------------ #
    def _build_layout(self):
        self.top_frame = ctk.CTkFrame(self.root, height=80)
        self.top_frame.pack(fill="x", padx=8, pady=(8, 4))

        self.center_frame = ctk.CTkFrame(self.root)
        self.center_frame.pack(fill="both", expand=True, padx=8, pady=4)

        self.bottom_frame = ctk.CTkFrame(self.root, height=60)
        self.bottom_frame.pack(fill="x", padx=8, pady=(4, 8))

        banner_path = utils.find_title_banner(self.base_dir)
        label = None
        if banner_path:
            try:
                from PIL import Image, ImageTk
                img = Image.open(banner_path)
                img.thumbnail((900, 70), Image.LANCZOS)
                banner_img = ImageTk.PhotoImage(img)
                label = ctk.CTkLabel(self.top_frame, image=banner_img, text="")
                label.image = banner_img
                label.pack(expand=True)
            except Exception:  # noqa: BLE001
                label = None
        if label is None:
            ctk.CTkLabel(self.top_frame, text=BANNER_TEXT, font=ctk.CTkFont(size=14, weight="bold")).pack(padx=6, pady=10)

    def _add_clear_button(self, parent, entry, command=None):
        entry_color = entry.cget("fg_color")
        if entry_color == "transparent":
            entry_color = ("#ffffff", "#ffffff")
        button = ctk.CTkButton(
            parent, text="╳", width=15, height=20, corner_radius=0, border_width=0,
            fg_color=entry_color, hover_color=("#d9d9d9", "#555555"),
            text_color=("#777777", "#aaaaaa"), font=("Segoe UI Symbol", 12, "bold"),
            border_spacing=0, command=command or (lambda: self._clear_entry(entry)),
        )
        button.place(relx=0.997, rely=0.5, anchor="e", x=0)
        return button

    @staticmethod
    def _clear_entry(entry):
        was_disabled = entry.cget("state") == "disabled"
        entry.configure(state="normal")
        entry.delete(0, "end")
        if was_disabled:
            entry.configure(state="disabled")

    @staticmethod
    def _set_readonly_entry(entry, value):
        entry.configure(state="normal")
        entry.delete(0, "end")
        if value:
            entry.insert(0, value)
        entry.configure(state="disabled")

    def _make_entry_row(self, parent, row, label_text, button_text, button_cmd, **entry_kwargs):
        ctk.CTkLabel(parent, text=label_text).grid(row=row, column=0, sticky="w", padx=8, pady=6)
        box = ctk.CTkFrame(parent, height=30, fg_color="transparent")
        box.grid(row=row, column=1, sticky="we", padx=(8, 5), pady=6)
        box.grid_propagate(False)
        entry = ctk.CTkEntry(box, **entry_kwargs)
        entry.pack(fill="both", expand=True)
        button = ctk.CTkButton(parent, text=button_text, width=110, command=button_cmd)
        button.grid(row=row, column=2, padx=(0, 8), pady=6)
        return entry, box, button

    def _build_left(self):
        cfg = self.cfg
        left = ctk.CTkFrame(self.center_frame)
        left.pack(side="left", fill="both", expand=True, padx=(6, 4), pady=6)
        left.grid_columnconfigure(1, weight=1)
        left.grid_rowconfigure(6, weight=1)  # 日誌區跟著視窗高度伸縮
        self.left = left

        # 格式 / 畫質
        top_opts = ctk.CTkFrame(left, fg_color="transparent", border_width=0)
        top_opts.grid(row=0, column=0, columnspan=3, sticky="we", padx=8, pady=(8, 2))
        top_opts.grid_columnconfigure(2, weight=1)
        ctk.CTkLabel(top_opts, text="格式：").grid(row=0, column=0, sticky="w")
        self.fmt_combo = ctk.CTkComboBox(top_opts, values=FORMATS, width=200)
        self.fmt_combo.set(cfg.get("last_format", "mp4"))
        self.fmt_combo.grid(row=0, column=1, sticky="w", padx=(43, 0))
        ctk.CTkLabel(top_opts, text="畫質：").grid(row=0, column=3, sticky="e")
        self.quality_combo = ctk.CTkComboBox(top_opts, values=QUALITIES, width=200)
        self.quality_combo.set(cfg.get("last_quality", "預設"))
        self.quality_combo.grid(row=0, column=4, sticky="e", padx=(4, 0))

        # 網址
        self.url_entry, url_box, self.import_btn = self._make_entry_row(
            left, 1, "網址：", "匯入批次下載", self.import_txt)
        self._add_clear_button(url_box, self.url_entry)
        self.url_entry.bind("<Return>", lambda _e: self.start_download())

        # Cookie 檔
        self.cookie_entry, cookie_box, _ = self._make_entry_row(
            left, 2, "Cookie：", "匯入 Cookies.txt", self.browse_cookie_file,
            state="disabled", fg_color=("#eeeeee", "#333333"), text_color=("#777777", "#aaaaaa"))
        self._set_readonly_entry(self.cookie_entry, "")  # Cookies 路徑刻意不記憶，每次啟動都從空白開始
        self._add_clear_button(cookie_box, self.cookie_entry)

        # 儲存位置
        self.path_entry, path_box, _ = self._make_entry_row(
            left, 3, "儲存位置：", "瀏覽", self.browse_folder)
        self.path_entry.insert(0, self._display_path(cfg.get("download_path", "Download")))
        self._add_clear_button(path_box, self.path_entry, self.reset_path)

        # 控制列
        control_row = ctk.CTkFrame(left, fg_color="transparent")
        control_row.grid(row=4, column=1, columnspan=2, sticky="w", padx=8, pady=(6, 6))
        self.start_btn = ctk.CTkButton(control_row, text="開始下載", width=120, command=self.start_download)
        self.start_btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(
            control_row, text="取消下載", width=100, state="disabled",
            fg_color=("#c0392b", "#a93226"), hover_color=("#a93226", "#8e2a20"),
            command=self.cancel_download)
        self.cancel_btn.pack(side="left", padx=(6, 0))
        self.info_btn = ctk.CTkButton(control_row, text="讀取影片資訊", width=110, command=self.fetch_video_info)
        self.info_btn.pack(side="left", padx=(6, 0))
        self.var_open_file = tk.BooleanVar(master=self.root, value=cfg.get("open_file_after_download", False))
        self.var_open_folder = tk.BooleanVar(master=self.root, value=cfg.get("open_folder_after_download", False))
        ctk.CTkCheckBox(control_row, text="開啟檔案", variable=self.var_open_file, width=90).pack(side="left", padx=(12, 5))
        ctk.CTkCheckBox(control_row, text="開啟輸出資料夾", variable=self.var_open_folder, width=120).pack(side="left")

        # 進度條
        progress_frame = ctk.CTkFrame(left, height=28, fg_color="transparent")
        progress_frame.grid(row=5, column=0, columnspan=3, padx=8, pady=(8, 6), sticky="we")
        progress_frame.grid_propagate(False)
        self.pbar = _ProgressBarWithText(progress_frame, height=20)
        self.pbar.set(0.0)
        self.pbar.place(relx=0.5, rely=0.5, anchor="center", relwidth=1.0)

        # 日誌
        self.log_text = ctk.CTkTextbox(left, height=160, state="disabled")
        self.log_text.grid(row=6, column=0, columnspan=3, padx=8, pady=(6, 4), sticky="nsew")

    def _build_right(self):
        cfg = self.cfg
        right = ctk.CTkScrollableFrame(self.center_frame, width=230)
        right.pack(side="right", fill="y", padx=(4, 6), pady=6)

        ctk.CTkLabel(right, text="快速操作").pack(padx=8, pady=(4, 2))
        for text, command in (
            ("載入範例", self.load_example),
            ("檢查更新", self.check_update),
            ("說明", self.show_help),
            ("下載歷史", self.show_history),
            ("系統診斷", self.show_diagnostics),
        ):
            button = ctk.CTkButton(right, text=text, command=command)
            button.pack(fill="x", padx=8, pady=5)
            if text == "檢查更新":
                self.update_btn = button

        adv = ctk.CTkFrame(right, fg_color="transparent")
        adv.pack(fill="x", padx=8, pady=(6, 4))
        ctk.CTkLabel(adv, text="進階下載").pack(anchor="w")
        self.var_subs = tk.BooleanVar(master=self.root, value=cfg.get("write_subtitles", False))
        self.var_embed = tk.BooleanVar(master=self.root, value=cfg.get("embed_subtitles", False))
        self.var_thumb = tk.BooleanVar(master=self.root, value=cfg.get("write_thumbnail", False))
        self.var_meta = tk.BooleanVar(master=self.root, value=cfg.get("add_metadata", False))
        self.var_number = tk.BooleanVar(master=self.root, value=cfg.get("number_playlist_items", False))
        self.var_archive = tk.BooleanVar(master=self.root, value=cfg.get("use_archive", False))
        for text, var in (
            ("下載字幕", self.var_subs), ("嵌入字幕", self.var_embed),
            ("下載縮圖", self.var_thumb), ("加入影片資訊", self.var_meta),
            ("播放清單自動編號", self.var_number), ("記錄已下載（略過重複）", self.var_archive),
        ):
            ctk.CTkCheckBox(adv, text=text, variable=var).pack(anchor="w", pady=2)

        ctk.CTkLabel(adv, text="字幕語言：").pack(anchor="w", pady=(8, 0))
        self.sub_langs_entry = ctk.CTkEntry(adv)
        self.sub_langs_entry.insert(0, cfg.get("subtitle_langs") or downloader.DEFAULT_SUBTITLE_LANGS)
        self.sub_langs_entry.pack(fill="x")

        ctk.CTkLabel(adv, text="瀏覽器 Cookie：").pack(anchor="w", pady=(8, 0))
        self.browser_combo = ctk.CTkComboBox(adv, values=BROWSERS, state="readonly")
        self.browser_combo.set(cfg.get("cookie_browser", "無") if cfg.get("cookie_browser", "無") in BROWSERS else "無")
        self.browser_combo.pack(fill="x")

    def _build_bottom(self):
        ctk.CTkButton(self.bottom_frame, text="關閉", command=self.on_close).pack(side="left", padx=12, pady=12)
        ctk.CTkLabel(self.bottom_frame, text="主題：").pack(side="left", padx=(20, 4))
        self.appearance_switch = ctk.CTkSegmentedButton(
            self.bottom_frame, values=list(APPEARANCE_MODES), command=self.set_appearance)
        self.appearance_switch.set(normalize_appearance(self.cfg.get("appearance")))
        self.appearance_switch.pack(side="left", padx=4)

    def _bind_focus_release(self):
        """點到輸入框以外的地方時，讓輸入框失去焦點。"""
        widgets = {self.url_entry, self.path_entry, self.sub_langs_entry}
        widgets |= {getattr(w, "_entry", None) for w in list(widgets)}
        self._input_widgets = widgets

        def release(event):
            if event.widget not in self._input_widgets:
                self.root.after_idle(self.root.focus_set)

        self.root.bind_all("<ButtonRelease-1>", release, add="+")

    # ------------------------------------------------------------------ #
    # 執行緒安全的 UI 更新（背景執行緒 → queue → 主執行緒）
    # ------------------------------------------------------------------ #
    def post(self, kind, *args):
        self.ui_queue.put((kind, args))

    def _pump(self):
        if self._closing:
            return
        log_buf, progress = [], None
        try:
            while True:
                kind, args = self.ui_queue.get_nowait()
                if kind == "log":  # 大量輸出時合併成一次寫入，避免拖慢 Tk 主迴圈
                    log_buf.append(args[0])
                    continue
                if kind == "progress":
                    progress = args
                    continue
                self._flush(log_buf, progress)  # 先沖出已累積的內容，維持事件順序
                log_buf, progress = [], None
                handler = getattr(self, f"_on_{kind}", None)
                if handler:
                    try:
                        handler(*args)
                    except Exception:  # noqa: BLE001
                        LOG.exception("UI handler %s failed", kind)
        except queue.Empty:
            pass
        self._flush(log_buf, progress)
        for hook in self.poll_hooks:
            try:
                hook()
            except Exception:  # noqa: BLE001
                LOG.exception("poll hook failed")
        if not self._closing:
            self._pump_id = self.root.after(PUMP_INTERVAL_MS, self._pump)

    def _flush(self, log_buf, progress):
        if log_buf:
            self._log("\n".join(log_buf))
        if progress:
            self.pbar.show(*progress)

    def _log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        total_lines = int(self.log_text.index("end-1c").split(".")[0])
        if total_lines > MAX_LOG_LINES:  # 長時間使用時避免日誌無限成長
            self.log_text.delete("1.0", f"{total_lines - MAX_LOG_LINES}.0")
        self.log_text.configure(state="disabled")
        self.log_text.see("end")

    # ------------------------------------------------------------------ #
    # 路徑 / 設定
    # ------------------------------------------------------------------ #
    def _display_path(self, stored):
        stored = (stored or "Download").strip() or "Download"
        return os.path.normpath(stored if os.path.isabs(stored) else os.path.join(self.base_dir, stored))

    def _output_dir(self):
        return self._display_path(self.path_entry.get())

    def reset_path(self):
        self.path_entry.delete(0, "end")
        self.path_entry.insert(0, self.default_dir)

    def browse_folder(self):
        d = filedialog.askdirectory(initialdir=self._output_dir())
        if d:
            self.path_entry.delete(0, "end")
            self.path_entry.insert(0, os.path.normpath(d))

    def browse_cookie_file(self):
        f = filedialog.askopenfilename(filetypes=[("Cookie files", "*.txt"), ("All files", "*.*")])
        if f:
            self._set_readonly_entry(self.cookie_entry, f)

    def import_txt(self):
        f = filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not f:
            return
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, "file:" + f)
        self._log(f"匯入批次下載清單：{f}")

    def load_example(self):
        ex = os.path.join(self.base_dir, "example", "example.txt")
        if os.path.exists(ex):
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, "file:" + ex)
            self._log("已載入 example.txt")
        else:
            messagebox.showinfo("範例不存在", "請把 example/example.txt 放進專案中")

    def _collect_options(self):
        return downloader.DownloadOptions(
            fmt=self.fmt_combo.get().strip() or "mp4",
            quality=self.quality_combo.get().strip() or "預設",
            cookie_browser=self.browser_combo.get(),
            cookie_file=self.cookie_entry.get().strip(),
            write_subtitles=bool(self.var_subs.get()),
            embed_subtitles=bool(self.var_embed.get()),
            subtitle_langs=self.sub_langs_entry.get().strip() or downloader.DEFAULT_SUBTITLE_LANGS,
            write_thumbnail=bool(self.var_thumb.get()),
            add_metadata=bool(self.var_meta.get()),
            number_playlist_items=bool(self.var_number.get()),
            use_archive=bool(self.var_archive.get()),
            filename_template=self.cfg.get("filename_template") or downloader.DEFAULT_FILENAME_TEMPLATE,
        )

    def _save_settings(self):
        opts = self._collect_options()
        outdir = self._output_dir()
        stored_path = "Download" if os.path.normcase(outdir) == os.path.normcase(os.path.normpath(self.default_dir)) else outdir
        self.cfg.update({
            "download_path": stored_path,
            "last_format": opts.fmt,
            "last_quality": opts.quality,
            "cookie_browser": opts.cookie_browser,
            "open_file_after_download": bool(self.var_open_file.get()),
            "open_folder_after_download": bool(self.var_open_folder.get()),
            "write_subtitles": opts.write_subtitles,
            "embed_subtitles": opts.embed_subtitles,
            "subtitle_langs": opts.subtitle_langs,
            "write_thumbnail": opts.write_thumbnail,
            "add_metadata": opts.add_metadata,
            "number_playlist_items": opts.number_playlist_items,
            "use_archive": opts.use_archive,
        })
        try:
            config_manager.save(self.config_path, self.cfg)
        except OSError as exc:
            LOG.warning("Unable to save settings: %s", exc)

    def set_appearance(self, mode):
        mode = normalize_appearance(mode)
        ctk.set_appearance_mode(mode)
        self.cfg["appearance"] = mode
        try:
            config_manager.save(self.config_path, self.cfg)
        except OSError as exc:
            LOG.warning("Unable to save settings: %s", exc)

    # ------------------------------------------------------------------ #
    # 下載
    # ------------------------------------------------------------------ #
    def _set_busy(self, busy):
        self.busy = busy
        self.start_btn.configure(state="disabled" if busy else "normal")
        self.cancel_btn.configure(state="normal" if busy else "disabled")

    def start_download(self):
        if self.busy:
            return
        target = self.url_entry.get().strip()
        if not target:
            messagebox.showwarning("未輸入網址", "請貼上網址或匯入 txt。")
            return
        if target.startswith("file:") and not os.path.isfile(target[5:]):
            messagebox.showwarning("找不到清單", f"找不到批次清單檔案：\n{target[5:]}")
            return
        if downloader.find_ffmpeg(self.base_dir) is None:
            self._log("下載取消：找不到 FFmpeg。")
            dialogs.ask_open_ffmpeg_page(self.root, downloader.ffmpeg_missing_message(self.base_dir),
                                         downloader.FFMPEG_DOWNLOAD_URL, downloader.ffmpeg_install_command())
            return
        if engine_mod.resolve_engine(self.base_dir) is None:
            messagebox.showwarning("找不到 yt-dlp", downloader.MESSAGES["no_engine"])
            return

        outdir = self._output_dir()
        try:
            os.makedirs(outdir, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("無法建立資料夾", f"{outdir}\n{exc}")
            return

        opts = self._collect_options()
        try:
            urls = downloader.expand_target(target)
        except OSError as exc:
            messagebox.showerror("無法讀取清單", str(exc))
            return
        if not urls:
            messagebox.showwarning("沒有可用的網址", "批次清單中沒有可用的網址（空行與 # 註解會被忽略）。")
            return

        items = self._plan_items(target, urls, outdir)  # 含播放清單時會在這裡詢問使用者
        if items is None:
            self._log("已取消下載。")
            return
        if not items:
            messagebox.showinfo("沒有可下載的項目", "清單中只有播放清單網址，而且已選擇略過，所以沒有要下載的項目。")
            return

        self._save_settings()
        playlist_items = [item for item in items if item.playlist]
        open_dir = playlist_items[0].target_dir(outdir) if len(items) == 1 and playlist_items else outdir
        self.job = {"outdir": outdir, "open_dir": open_dir, "fmt": opts.fmt, "quality": opts.quality,
                    "open_file": bool(self.var_open_file.get()),
                    "open_folder": bool(self.var_open_folder.get())}
        self._set_busy(True)
        self.pbar.reset()
        self._log(f"開始下載 -> {target} 格式：{opts.fmt} 畫質：{opts.quality} 輸出：{outdir}")

        self.controller = downloader.run_download(
            self.base_dir, items, outdir, opts,
            progress_callback=lambda frac, text: self.post("progress", frac, text),
            log_callback=lambda text: self.post("log", text),
            item_started=lambda i, n, item: self.post("item_start", i, n, item),
            item_finished=lambda i, n, item, ok, msg, files: self.post("item_done", i, n, item, ok, msg, files),
            finished_callback=lambda result: self.post("done", result),
        )

    def cancel_download(self):
        if self.controller is not None:
            self.cancel_btn.configure(state="disabled")
            self._log("正在取消下載…")
            self.controller.cancel()

    def _plan_items(self, target, urls, outdir):
        """把網址轉成下載項目；遇到播放清單網址時詢問使用者。回傳 None 代表使用者取消。"""
        kinds = [downloader.classify_playlist_url(url) for url in urls]

        if not target.startswith("file:"):  # 單一網址
            url, kind = urls[0], kinds[0]
            if kind == "none":
                return [downloader.DownloadItem(url)]
            default_name = downloader.next_playlist_folder(outdir)
            choice = dialogs.ask_playlist_choice(self.root, kind, default_name)
            if choice is None:
                return None
            if choice.mode == "single":
                return [downloader.DownloadItem(url)]
            name = downloader.sanitize_folder_name(choice.name) or default_name
            return [downloader.DownloadItem(url, playlist=True, folder=name)]

        playlist_kinds = [k for k in kinds if k != "none"]  # 批次清單
        if not playlist_kinds:
            return [downloader.DownloadItem(url) for url in urls]
        decision = dialogs.ask_batch_playlist_choice(self.root, len(playlist_kinds), kinds.count("playlist"))
        if decision is None:
            return None
        items, reserved = [], set()
        for url, kind in zip(urls, kinds):
            if kind == "none":
                items.append(downloader.DownloadItem(url))
            elif decision == "playlist":
                name = downloader.next_playlist_folder(outdir, reserved)
                reserved.add(name)  # 資料夾要到開始下載才會建立，先預留名稱避免同一批次撞名
                items.append(downloader.DownloadItem(url, playlist=True, folder=name))
            elif kind == "mixed":  # 只下載單一影片：影片加清單 → 只取該影片；純清單 → 略過
                items.append(downloader.DownloadItem(url))
        return items

    def _on_item_start(self, index, total, item):
        if total > 1:
            self._log(f"[{index}/{total}] 開始：{item.url}")
        if item.playlist:
            self._log(f"下載整個播放清單 → 資料夾：{item.folder}")

    def _on_item_done(self, index, total, item, ok, message, files):
        if total > 1:
            self._log(f"[{index}/{total}] {'成功' if ok else '失敗'}：{message}")
        if message == "已取消":
            return  # 使用者主動取消的項目不計入歷史
        try:
            history.add(self.base_dir, item.url, self.job.get("outdir", ""), self.job.get("fmt", ""),
                        self.job.get("quality", ""), ok, message, files,
                        title=downloader.derive_title(item, files))
        except OSError as exc:
            LOG.warning("Unable to write history: %s", exc)

    def _on_done(self, result):
        self._set_busy(False)
        self.controller = None
        if result.cancelled:
            self.pbar.set_status("已取消")
        elif result.success:
            self.pbar.set_status(None)
            self.pbar.show(1.0, "100.0%  下載完成")
        else:
            self.pbar.set_status("錯誤")
        self._log(f"結束：{result.message}")

        if result.succeeded:
            if self.job.get("open_folder"):
                utils.open_path(self.job.get("open_dir") or self.job["outdir"])
            if self.job.get("open_file"):
                self._open_result_file(result.files)

        if not result.success and not result.cancelled:
            detail = result.message
            if len(result.failures) > 1:
                shown = "\n".join(f"• {url}\n  {msg}" for url, msg in result.failures[:3])
                more = f"\n…另有 {len(result.failures) - 3} 筆失敗" if len(result.failures) > 3 else ""
                detail = f"{result.message}\n\n{shown}{more}"
            if result.code == "ffmpeg":  # yt-dlp 回報 FFmpeg 缺失或無法執行：同樣提供官網連結
                dialogs.ask_open_ffmpeg_page(self.root, detail, downloader.FFMPEG_DOWNLOAD_URL,
                                             downloader.ffmpeg_install_command())
            else:
                messagebox.showwarning("下載失敗", detail)

    def _open_result_file(self, files):
        for path in reversed(files):
            if os.path.isfile(path):
                utils.open_path(path)
                return
        # 後備：輸出資料夾中最新的媒體檔
        outdir = self.job.get("outdir", "")
        try:
            candidates = [os.path.join(outdir, n) for n in os.listdir(outdir)]
            candidates = [f for f in candidates if os.path.isfile(f)
                          and not f.endswith((".part", ".ytdl", ".vtt", ".srt", ".jpg", ".png", ".webp"))]
            if candidates:
                utils.open_path(max(candidates, key=os.path.getmtime))
        except OSError as exc:
            LOG.warning("Unable to open latest file in %s: %s", outdir, exc)

    # ------------------------------------------------------------------ #
    # 影片資訊
    # ------------------------------------------------------------------ #
    def fetch_video_info(self):
        target = self.url_entry.get().strip()
        if not target:
            messagebox.showwarning("未輸入網址", "請先貼上要查詢的網址。")
            return
        if target.startswith("file:"):
            messagebox.showinfo("讀取影片資訊", "批次清單不支援讀取資訊，請貼上單一網址。")
            return
        opts = self._collect_options()
        self.info_btn.configure(state="disabled")
        self._log(f"讀取影片資訊：{target}")

        def work():
            try:
                self.post("info_ok", downloader.fetch_info(self.base_dir, target, opts))
            except Exception as exc:  # noqa: BLE001
                self.post("info_err", str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _on_info_ok(self, info):
        self.info_btn.configure(state="normal")
        lines = [f"標題：{info['title']}"]
        if info["uploader"]:
            lines.append(f"上傳者：{info['uploader']}")
        if info["is_playlist"]:
            lines.append(f"類型：播放清單（共 {info['entry_count']} 部）")
        duration = info["duration"]
        if duration:
            duration = int(duration)
            lines.append(f"長度：{duration // 3600:02d}:{duration % 3600 // 60:02d}:{duration % 60:02d}")
        if info["heights"]:
            lines.append("可用畫質：" + " / ".join(f"{h}P" for h in info["heights"]))
        self._log("\n".join(lines))
        messagebox.showinfo("影片資訊", "\n".join(lines))

    def _on_info_err(self, message):
        self.info_btn.configure(state="normal")
        self._log(f"讀取影片資訊失敗：{message}")
        messagebox.showerror("讀取影片資訊失敗", message)

    # ------------------------------------------------------------------ #
    # 更新 / 診斷 / 說明 / 歷史
    # ------------------------------------------------------------------ #
    def check_update(self):
        self.update_btn.configure(state="disabled")
        self._log("正在檢查更新（yt-dlp、Python、FFmpeg），請稍候…")

        def work():
            try:
                eng = engine_mod.resolve_engine(self.base_dir)
                results = updater.check_all(eng, log=lambda text: self.post("log", text), base_dir=self.base_dir)
            except Exception as exc:  # noqa: BLE001
                LOG.exception("Update check failed")
                failed = {"status": "error", "message": str(exc)}
                results = {"ytdlp": failed, "python": failed, "ffmpeg": failed}
            self.post("update_done", results)

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _summarize_update(results):
        """回傳 (對話框訊息, 是否有檢查失敗, 是否建議前往 Python 下載頁)。"""
        lines, problem, suggest_python = [], False, False

        y = results.get("ytdlp", {})
        if y.get("status") == "up_to_date":
            lines.append(f"yt-dlp：已為最新版本（{y['current']}）")
        elif y.get("status") == "updated":
            lines.append(f"yt-dlp：已更新 {y['current']} → {y['latest']}")
        else:
            problem = True
            lines.append(f"yt-dlp：檢查失敗：{y.get('message', '未知錯誤')}")

        py = results.get("python", {})
        if py.get("status") == "ok":
            notes = []
            if py["patch_update"]:
                notes.append(f"同系列有新的修補版 {py['series_latest']}（建議更新）")
            if py["new_series"]:
                notes.append(f"已有新的主要版本 {py['overall']}（不一定要升級，請先確認套件相容）")
            if py["eol"]:
                notes.append(f"Python {py['series']} 已結束支援，建議改用較新的版本")
            if notes:
                suggest_python = True
                lines.append(f"Python：目前 {py['current']}\n  • " + "\n  • ".join(notes))
            else:
                lines.append(f"Python：目前 {py['current']}，已為最新版本")
        else:
            problem = True
            lines.append(f"Python：檢查失敗：{py.get('message', '未知錯誤')}")

        ff = results.get("ffmpeg")  # 只顯示版本與位置，不提供更新
        if ff is not None:
            if ff.get("status") == "ok":
                where = "程式資料夾" if ff.get("source") == "program" else "系統 PATH"
                version = ff.get("version") or "無法讀取版本"
                lines.append(f"FFmpeg：{version}\n  （{where}：{ff.get('path', '')}）")
            elif ff.get("status") == "missing":
                problem = True
                lines.append("FFmpeg：找不到，請將 ffmpeg.exe 放入程式資料夾，或將 FFmpeg 加入系統 PATH")
            else:
                problem = True
                lines.append(f"FFmpeg：檢查失敗：{ff.get('message', '未知錯誤')}")
        return "\n\n".join(lines), problem, suggest_python

    def _on_update_done(self, results):
        self.update_btn.configure(state="normal")
        text, problem, suggest_python = self._summarize_update(results)
        self._log(text)
        ffmpeg_missing = (results.get("ffmpeg") or {}).get("status") == "missing"
        if suggest_python or ffmpeg_missing:
            command = (results.get("python") or {}).get("winget_command") if suggest_python else None  # 只有 Windows 且 winget 可用時才有
            command_kind = "python"
            if command is None and ffmpeg_missing:  # Linux（有 apt）找不到 FFmpeg：提供安裝命令
                command = downloader.ffmpeg_install_command()
                command_kind = "ffmpeg"
            dialogs.show_update_result(
                self.root, text,
                python_url=updater.PYTHON_DOWNLOADS_URL if suggest_python else None,
                command=command, command_kind=command_kind,
                ffmpeg_url=downloader.FFMPEG_DOWNLOAD_URL if ffmpeg_missing else None)
        elif problem:
            messagebox.showwarning("檢查更新", text)
        else:
            messagebox.showinfo("檢查更新", text)

    def show_help(self):
        messagebox.showinfo("說明", HELP_TEXT)

    def show_diagnostics(self):
        # Tk 的呼叫要在主執行緒做，所以先取得字型結果再交給背景執行緒組合文字
        if self.font_family:
            font_line = (f"介面字型：{self.font_family}\n  Tk 實際繪製：" +
                         fonts.describe_probe(fonts.probe(self.root, self.font_family)) +
                         f"\n  註冊方式：{fonts.active_mode()}\n")
        else:
            font_line = "介面字型：使用系統預設\n"

        def work():
            eng = engine_mod.resolve_engine(self.base_dir)
            version = engine_mod.get_version(eng) if eng else None
            js = engine_mod.find_js_runtimes()
            text = (
                f"系統平台：{utils.describe_platform()}\n"
                f"Python：{sys.version.split()[0]}\n"
                f"yt-dlp 版本：{version or '未安裝或無法取得'}\n"
                f"yt-dlp 來源：{eng.label if eng else '找不到'}\n"
                f"FFmpeg：{downloader.find_ffmpeg(self.base_dir) or '找不到'}\n"
                f"{font_line}"
                f"JS 執行環境：{', '.join(js) if js else '未偵測到（部分 YouTube 影片可能需要，例如 Deno）'}\n"
                f"輸出資料夾：{self._output_dir()}\n"
                f"設定檔：{self.config_path}"
            )
            self.post("diag", text)

        threading.Thread(target=work, daemon=True).start()

    def _on_diag(self, text):
        messagebox.showinfo("系統診斷", text)

    @staticmethod
    def _history_title(item):
        """歷史紀錄要顯示的標題：優先用紀錄的標題，其次檔名，最後才是網址。"""
        title = str(item.get("title") or "").strip()
        if not title:
            files = item.get("files") or []
            if files:
                title = os.path.splitext(os.path.basename(str(files[-1]).replace("\\", "/")))[0]
        return title or str(item.get("url", ""))

    def show_history(self):
        items = history.load(self.base_dir)
        if not items:
            messagebox.showinfo("下載歷史", "目前沒有下載紀錄。")
            return

        window = ctk.CTkToplevel(self.root)
        window.title(f"下載歷史（最近 {HISTORY_VIEW_COUNT} 筆）")
        window.geometry("980x500")
        window.minsize(720, 320)
        window.transient(self.root)
        listing = ctk.CTkScrollableFrame(window)
        listing.pack(fill="both", expand=True, padx=12, pady=(12, 6))

        def open_target(target):
            target = str(target or "").strip()
            if target.lower().startswith("file:"):
                utils.open_path(target[5:])
            elif target:
                webbrowser.open(target)

        for item in items[-HISTORY_VIEW_COUNT:][::-1]:
            ok = bool(item.get("success"))
            stamp = str(item.get("time", "")).replace("T", "_")
            file_type = str(item.get("format") or "").upper() or "-"
            quality = str(item.get("quality") or "-")
            url = str(item.get("url", ""))
            title = self._history_title(item)
            if len(title) > 80:
                title = title[:79] + "…"

            # [yyyy-mm-dd_hh:mm:ss] 成功 | MP4 | 1080P | 影片標題（標題是超連結，點了開啟原始網址）
            row = ctk.CTkFrame(listing, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=1)
            ctk.CTkLabel(row, text=f"[{stamp}]").pack(side="left")
            ctk.CTkLabel(row, text="成功" if ok else "失敗",
                         text_color=("#1e8e3e", "#4cc26b") if ok else ("#c0392b", "#ff6b5e")).pack(side="left", padx=(6, 0))
            ctk.CTkLabel(row, text=f"|  {file_type}  |  {quality}  |").pack(side="left", padx=(6, 0))
            ctk.CTkButton(
                row, text=title, anchor="w", height=26, fg_color="transparent",
                hover_color=("#e8e8e8", "#3a3a3a"), text_color=("#1677c8", "#65aee8"),
                font=ctk.CTkFont(size=13, underline=True), border_width=0, corner_radius=0,
                command=lambda target=url: open_target(target),
            ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        buttons = ctk.CTkFrame(window, fg_color="transparent")
        buttons.pack(pady=(0, 10))

        def clear_history():
            if messagebox.askyesno("清除歷史", "確定要清除所有下載紀錄嗎？", parent=window):
                history.clear(self.base_dir)
                window.destroy()

        ctk.CTkButton(buttons, text="清除歷史", width=100, fg_color=("#c0392b", "#a93226"),
                      hover_color=("#a93226", "#8e2a20"), command=clear_history).pack(side="left", padx=6)
        ctk.CTkButton(buttons, text="關閉", width=100, command=window.destroy).pack(side="left", padx=6)

    # ------------------------------------------------------------------ #
    # 結束
    # ------------------------------------------------------------------ #
    def on_close(self):
        LOG.info("Closing application, terminating downloads")
        self._closing = True
        if self._pump_id is not None:
            try:
                self.root.after_cancel(self._pump_id)
            except Exception:  # noqa: BLE001
                pass
        downloader.terminate_all_downloads()
        fonts.unregister(self.base_dir)
        self._save_settings()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()


def launch_ui(cfg, base_dir, startup=None):
    App(cfg, base_dir, startup=startup).run()
