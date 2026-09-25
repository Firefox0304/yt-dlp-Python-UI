# modules/ui.py
import os
import sys
import tkinter as tk
import customtkinter as ctk
from tkinter import filedialog, messagebox
from modules import config_manager, downloader, logger, utils, history
import threading
import subprocess

ctk.set_appearance_mode("Light")  # 預設亮色
ctk.set_default_color_theme("blue")

LOG = logger.get()

def launch_ui(cfg, base_dir):
    root = ctk.CTk()
    root.title("yt-dlp-python-UI v5.4 | 作者：Firefox_0304 | 協助Bot：ChatGPT & Manus")
    root.geometry("1060x560")
    # 鎖定視窗大小，避免 resize 時 UI 卡頓
    root.resizable(False, False)
    
    # 處理關閉事件，終止所有下載
    def on_close():
        logger.get().info("Closing application, terminating downloads")
        downloader.terminate_all_downloads()
        try:
            root.quit()  # 先退出mainloop
            root.destroy()  # 然後銷毀視窗
        except:
            pass  # 忽略可能的Tcl錯誤
    
    root.protocol("WM_DELETE_WINDOW", on_close)

    # Layout frames
    top_frame = ctk.CTkFrame(root, height=80)
    top_frame.pack(fill="x", padx=8, pady=(8,4))

    center_frame = ctk.CTkFrame(root)
    center_frame.pack(fill="both", expand=True, padx=8, pady=4)

    bottom_frame = ctk.CTkFrame(root, height=80)
    bottom_frame.pack(fill="x", padx=8, pady=(4,8))

    # Top: banner or title
    banner_path = utils.find_title_banner(base_dir)
    if banner_path:
        try:
            from PIL import Image, ImageTk
            img = Image.open(banner_path)
            img.thumbnail((900, 70), Image.LANCZOS)
            banner_img = ImageTk.PhotoImage(img)
            lbl = ctk.CTkLabel(top_frame, image=banner_img, text="")
            lbl.image = banner_img
            lbl.pack(expand=True)
        except Exception:
            lbl = ctk.CTkLabel(top_frame, text="yt-dlp-download-python-UI v5.4    作者：Firefox_0304    協助Bot：ChatGPT & Manus", font=("Helvetica", 14, "bold"))
            lbl.pack(padx=6, pady=10)
    else:
        lbl = ctk.CTkLabel(top_frame, text="yt-dlp-download-python-UI v5.4    作者：Firefox_0304    協助Bot：ChatGPT & Manus", font=("Helvetica", 14, "bold"))
        lbl.pack(padx=6, pady=10)

    # Center: left input / right examples area
    left = ctk.CTkFrame(center_frame)
    left.pack(side="left", fill="both", expand=True, padx=(6,4), pady=6)

    right = ctk.CTkFrame(center_frame, width=260)
    right.pack(side="right", fill="y", padx=(4,6), pady=6)

    # Left content: format + quality (fixed row), then url / import / path
    # Format + quality row (寬度與網址欄一致，並對齊)
    top_opts = ctk.CTkFrame(left, fg_color="transparent", border_width=0)
    # span 2 欄：與網址 label+entry 的整體寬度一致，讓格式組靠左對齊網址 label
    top_opts.grid(row=0, column=0, columnspan=2, sticky="we", padx=8, pady=(8,2))

    # 內部用 grid 做排版：格式在左、畫質在右
    fmt_label = ctk.CTkLabel(top_opts, text="格式：")
    fmt_label.grid(row=0, column=0, sticky="w", padx=0, pady=0)

    formats = ["mp4", "mp3", "mkv", "webm", "wav", "m4a", "flac"]
    fmt_combo = ctk.CTkComboBox(top_opts, values=formats, width=200)
    fmt_combo.set(cfg.get("last_format", "mp4"))
    fmt_combo.grid(row=0, column=1, sticky="w", padx=(43, 0), pady=0)

    # 中間填充，讓畫質組靠右
    top_opts.grid_columnconfigure(2, weight=1)

    quality_label = ctk.CTkLabel(top_opts, text="畫質：")
    quality_label.grid(row=0, column=3, sticky="e", padx=0, pady=0)

    qualities = ["預設", "8K", "4K", "2K", "1080P", "720P", "480P", "240P"]
    quality_combo = ctk.CTkComboBox(top_opts, values=qualities, width=200)
    quality_combo.set(cfg.get("last_quality", "預設"))
    quality_combo.grid(row=0, column=4, sticky="e", padx=(4, 0), pady=0)

    cookie_file_entry = None

    def set_readonly_entry(entry, value):
        entry.configure(state="normal")
        entry.delete(0, "end")
        if value:
            entry.insert(0, value)
        entry.configure(state="disabled")

    def clear_entry(entry):
        was_disabled = entry.cget("state") == "disabled"
        entry.configure(state="normal")
        entry.delete(0, "end")
        if was_disabled:
            entry.configure(state="disabled")

    def add_clear_button(parent, entry, clear_command=None):
        entry_color = entry.cget("fg_color")
        if entry_color == "transparent":
            entry_color = ("#ffffff", "#ffffff")
        button = ctk.CTkButton(
            parent, text="╳", width=15, height=20, corner_radius=0, border_width=0,
            fg_color=entry_color, hover_color=("#d9d9d9", "#555555"),
            text_color=("#777777", "#aaaaaa"), font=("Segoe UI Symbol", 12, "bold"),
            border_spacing=0,
            command=clear_command or (lambda: clear_entry(entry)),
        )
        button.place(relx=0.997, rely=0.5, anchor="e", x=0)
        return button

    def browse_cookie_file():
        f = filedialog.askopenfilename(filetypes=[("Cookie files", "*.txt"), ("All files", "*.*")])
        if f:
            set_readonly_entry(cookie_file_entry, f)

    url_label = ctk.CTkLabel(left, text="網址：")
    url_label.grid(row=1, column=0, sticky="w", padx=8, pady=6)
    url_box = ctk.CTkFrame(left, width=560, height=30, fg_color="transparent")
    url_box.grid(row=1, column=1, sticky="we", padx=(8, 5), pady=6)
    url_box.grid_propagate(False)
    url_entry = ctk.CTkEntry(url_box, width=560)
    url_entry.pack(fill="both", expand=True)
    add_clear_button(url_box, url_entry)

    txt_btn = ctk.CTkButton(left, text="匯入批次下載", width=100, command=lambda: import_txt(url_entry))
    txt_btn.grid(row=1, column=2, padx=(0, 8), pady=6)

    cookie_file_label = ctk.CTkLabel(left, text="Cookie：")
    cookie_file_label.grid(row=2, column=0, sticky="w", padx=8, pady=6)
    cookie_box = ctk.CTkFrame(left, width=560, height=30, fg_color="transparent")
    cookie_box.grid(row=2, column=1, sticky="we", padx=(8, 5), pady=6)
    cookie_box.grid_propagate(False)
    cookie_file_entry = ctk.CTkEntry(
        cookie_box, width=560, state="disabled",
        fg_color=("#eeeeee", "#333333"),
        text_color=("#777777", "#aaaaaa"),
    )
    cookie_file_entry.pack(fill="both", expand=True)
    set_readonly_entry(cookie_file_entry, cfg.get("cookie_file", ""))
    add_clear_button(cookie_box, cookie_file_entry)
    url_entry.bind("<Button-1>", lambda event: url_entry.focus_set(), add="+")
    cookie_file_btn = ctk.CTkButton(left, text="匯入 Cookies.txt", width=100, command=browse_cookie_file)
    cookie_file_btn.grid(row=2, column=2, padx=(0, 8), pady=6)

    path_label = ctk.CTkLabel(left, text="儲存位置：")
    path_label.grid(row=3, column=0, sticky="w", padx=8, pady=6)
    default_path = os.path.join(base_dir, cfg.get("download_path", "Download"))
    path_box = ctk.CTkFrame(left, width=560, height=30, fg_color="transparent")
    path_box.grid(row=3, column=1, sticky="we", padx=(8, 5), pady=6)
    path_box.grid_propagate(False)
    path_entry = ctk.CTkEntry(path_box, width=560)
    path_entry.pack(fill="both", expand=True)
    path_placeholder = {"active": True}
    path_entry.insert(0, default_path)
    path_entry.configure(text_color=("#888888", "#888888"))

    def clear_path_placeholder(event=None):
        if path_placeholder["active"]:
            path_entry.delete(0, "end")
            path_entry.configure(text_color=ctk.ThemeManager.theme["CTkEntry"]["text_color"])
            path_placeholder["active"] = False

    def restore_path_placeholder(event=None):
        if not path_entry.get().strip():
            path_entry.insert(0, default_path)
            path_entry.configure(text_color=("#888888", "#888888"))
            path_placeholder["active"] = True

    def clear_path():
        path_entry.delete(0, "end")
        path_entry.insert(0, default_path)
        path_entry.configure(text_color=("#888888", "#888888"))
        path_placeholder["active"] = True

    path_entry.bind("<FocusIn>", clear_path_placeholder)
    path_entry.bind("<FocusOut>", restore_path_placeholder)
    add_clear_button(path_box, path_entry, clear_path)

    browse_btn = ctk.CTkButton(left, text="瀏覽", width=100, command=lambda: browse_folder(path_entry))
    browse_btn.grid(row=3, column=2, padx=(0, 8), pady=6)

    # Start button and progress bar
    control_row = ctk.CTkFrame(left, fg_color="transparent")
    control_row.grid(row=4, column=1, columnspan=2, sticky="w", padx=8, pady=(6, 6))
    start_btn = ctk.CTkButton(control_row, text="開始下載", width=140, command=lambda: start_download(base_dir, url_entry, fmt_combo, quality_combo, cookie_file_entry, path_entry, start_btn, progress_bar, log_text))
    start_btn.pack(side="left")

    # Progress area is on its own row so it cannot overlap the start button.
    progress_frame = ctk.CTkFrame(left, width=720, height=28, fg_color="transparent")
    progress_frame.grid(row=5, column=0, columnspan=3, padx=8, pady=(8,6), sticky="we")
    progress_frame.grid_propagate(False)

    # Keep CTkProgressBar's rounded corners and draw the text on its internal
    # Canvas. A child label is still a separate window layer and can cover the
    # moving fill; drawing after the native bar avoids that artifact entirely.
    class _ProgressBarWithText(ctk.CTkProgressBar):
        def __init__(self, *args, **kwargs):
            self._status_text = None
            self._detail_text = None
            super().__init__(*args, **kwargs)

        def _draw(self, no_color_updates=False):
            super()._draw(no_color_updates)
            self._canvas.delete("progress_text")
            text = self._status_text or self._detail_text or f"{self._determinate_value * 100:.1f}%"
            self._canvas.create_text(
                self._apply_widget_scaling(self._current_width) / 2,
                self._apply_widget_scaling(self._current_height) / 2,
                text=text, fill="white", font=("Segoe UI", 10, "bold"),
                tags="progress_text",
            )

        def set_status(self, text):
            self._status_text = text
            self._draw(no_color_updates=True)

        def set_progress_info(self, text):
            self._detail_text = text
            self._draw(no_color_updates=True)

        def reset(self):
            self._status_text = None
            self._detail_text = None
            self.set(0.0)

    progress_bar = _ProgressBarWithText(progress_frame, width=720, height=20)
    progress_bar.set(0.0)
    progress_bar.place(relx=0.5, rely=0.5, anchor="center", relwidth=1.0)

    action_frame = ctk.CTkFrame(control_row, fg_color="transparent")
    action_frame.pack(side="left", padx=(5, 0))
    open_file_var = tk.BooleanVar(value=cfg.get("open_file_after_download", False))
    open_folder_var = tk.BooleanVar(value=cfg.get("open_folder_after_download", False))
    ctk.CTkCheckBox(action_frame, text="開啟檔案", variable=open_file_var, width=90).pack(side="left", padx=(0, 5))
    ctk.CTkCheckBox(action_frame, text="開啟輸出資料夾", variable=open_folder_var, width=120).pack(side="left", padx=0)

    # log text box
    log_text = ctk.CTkTextbox(left, width=720, height=160, state="disabled")
    log_text.grid(row=6, column=0, columnspan=3, padx=8, pady=(6,4))
    def append_log(text, index="end"):
        log_text.configure(state="normal")
        log_text.insert(index, text)
        log_text.configure(state="disabled")

    append_log("狀態/日誌…（會顯示 yt-dlp 輸出）\n", "0.0")

    input_widgets = {
        url_entry, path_entry,
        getattr(url_entry, "_entry", None),
        getattr(path_entry, "_entry", None),
    }

    def release_input_focus(event):
        if event.widget not in input_widgets:
            root.after_idle(root.focus_set)

    root.bind_all("<ButtonRelease-1>", release_input_focus, add="+")

    # Right side: examples / change background / mode
    right_label = ctk.CTkLabel(right, text="快速操作")
    right_label.pack(padx=8, pady=(8,4))

    ex_txt_btn = ctk.CTkButton(right, text="載入範例", command=lambda: load_example(base_dir, url_entry))
    ex_txt_btn.pack(fill="x", padx=8, pady=6)

    check_update_btn = ctk.CTkButton(right, text="檢查更新", command=lambda: check_update_action(base_dir, log_text))
    check_update_btn.pack(fill="x", padx=8, pady=6)

    help_btn = ctk.CTkButton(right, text="說明", command=lambda: show_help())
    help_btn.pack(fill="x", padx=8, pady=6)

    history_btn = ctk.CTkButton(right, text="下載歷史", command=lambda: show_history())
    history_btn.pack(fill="x", padx=8, pady=6)

    diagnostic_btn = ctk.CTkButton(right, text="系統診斷", command=lambda: show_diagnostics(base_dir))
    diagnostic_btn.pack(fill="x", padx=8, pady=6)

    advanced_frame = ctk.CTkFrame(right, fg_color="transparent")
    advanced_frame.pack(fill="x", padx=8, pady=(4, 8))
    ctk.CTkLabel(advanced_frame, text="進階下載").pack(anchor="w")
    subtitles_var = tk.BooleanVar(value=cfg.get("write_subtitles", False))
    embed_subtitles_var = tk.BooleanVar(value=cfg.get("embed_subtitles", False))
    thumbnail_var = tk.BooleanVar(value=cfg.get("write_thumbnail", False))
    metadata_var = tk.BooleanVar(value=cfg.get("add_metadata", False))
    ctk.CTkCheckBox(advanced_frame, text="下載字幕", variable=subtitles_var).pack(anchor="w")
    ctk.CTkCheckBox(advanced_frame, text="嵌入字幕", variable=embed_subtitles_var).pack(anchor="w")
    ctk.CTkCheckBox(advanced_frame, text="下載縮圖", variable=thumbnail_var).pack(anchor="w")
    ctk.CTkCheckBox(advanced_frame, text="加入影片資訊", variable=metadata_var).pack(anchor="w")

    # bottom controls: close, appearance mode
    close_btn = ctk.CTkButton(bottom_frame, text="關閉", command=on_close)
    close_btn.pack(side="left", padx=12, pady=12)

    appearance_label = ctk.CTkLabel(bottom_frame, text="主題：")
    appearance_label.pack(side="left", padx=(20,4))
    appearance_switch = ctk.CTkSegmentedButton(bottom_frame, values=["Light","Dark"], command=lambda v: set_appearance(v))
    appearance_switch.set(cfg.get("appearance","Light"))
    appearance_switch.pack(side="left", padx=4)

    # Grid configure
    # 讓網址/儲存位置那一欄會跟著視窗寬度拉伸；format/quality 在 top_opts 內固定不會被拉開
    left.grid_columnconfigure(1, weight=1)

    # helper functions

    def browse_folder(entry):
        d = filedialog.askdirectory(initialdir=entry.get() or base_dir)
        if d:
            path_placeholder["active"] = False
            entry.configure(text_color=ctk.ThemeManager.theme["CTkEntry"]["text_color"])
            entry.delete(0, "end")
            entry.insert(0, d)

    def import_txt(entry):
        f = filedialog.askopenfilename(filetypes=[("Text files","*.txt"),("All files","*.*")])
        if not f:
            return
        entry.delete(0, "end")
        entry.insert(0, "file:" + f)
        append_log(f"匯入批次下載清單：{f}\n")
        log_text.see("end")

    def load_example(base_dir, entry):
        ex = os.path.join(base_dir, "example", "example.txt")
        if os.path.exists(ex):
            entry.delete(0, "end")
            entry.insert(0, "file:" + ex)
            append_log("已載入 example.txt\n")
        else:
            messagebox.showinfo("範例不存在", f"請把 example/example.txt 放進專案中")
    
    def show_help():
        messagebox.showinfo("說明", "可在『網址』欄位貼上一個網址，或按『匯入批次下載』選取 .txt 批次清單（每行一個網址）。\n選擇格式、畫質與儲存位置後按「開始下載」。\n\n若需要登入 Cookie，請使用瀏覽器 Cookie 匯出工具產生 cookies.txt，再按上方『匯入 Cookies.txt』。Cookies.txt 會優先用於下載。")

    def show_history():
        items = history.load(base_dir)
        if not items:
            messagebox.showinfo("下載歷史", "目前沒有下載紀錄。")
            return
        lines = []
        for item in items[-20:][::-1]:
            status = "成功" if item.get("success") else "失敗"
            lines.append(f"[{item.get('time', '')}] {status} | {item.get('url', '')}")
        messagebox.showinfo("下載歷史（最近 20 筆）", "\n".join(lines))

    def show_diagnostics(base_dir):
        import importlib.metadata
        try:
            ytdlp_version = importlib.metadata.version("yt-dlp")
        except importlib.metadata.PackageNotFoundError:
            ytdlp_version = "未安裝 Python yt-dlp"
        ffmpeg = downloader.find_ffmpeg(base_dir) or "找不到"
        messagebox.showinfo(
            "系統診斷",
            f"Python：{sys.version.split()[0]}\n"
            f"yt-dlp（Python）：{ytdlp_version}\n"
            f"FFmpeg：{ffmpeg}\n"
            f"輸出資料夾：{path_entry.get().strip() or default_path}",
        )

    def set_appearance(mode):
        ctk.set_appearance_mode(mode)
        cfg["appearance"] = mode
        config_manager.save(os.path.join(base_dir, "config", "settings.json"), cfg)

    def check_update_action(base_dir, logbox):
        import subprocess, sys
        
        def _update_log(text):
            """在主線程中更新 log"""
            def _ui_update():
                append_log(text + "\n")
                logbox.see("end")
            root.after(0, _ui_update)
        
        def _show_info(title, message):
            """在主線程中顯示訊息框"""
            def _ui_update():
                messagebox.showinfo(title, message)
            root.after(0, _ui_update)
        
        def _show_error(title, message):
            """在主線程中顯示錯誤訊息框"""
            def _ui_update():
                messagebox.showerror(title, message)
            root.after(0, _ui_update)
        
        def _check_update_thread():
            _update_log("正在檢查更新，請稍候...")
            
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
                import importlib.metadata
                import re
                from packaging.version import Version

                def run_hidden(command):
                    return subprocess.run(
                        command, capture_output=True, text=True,
                        startupinfo=startupinfo, creationflags=creationflags,
                    )

                # GUI 是由 pythonw.exe 啟動時，不要再用 pythonw.exe 建立
                # pip 子程序；改用同一個環境的 python.exe，並等待它正常結束。
                # 本功能不會重啟目前的 UI。
                python_executable = sys.executable
                if os.path.basename(python_executable).lower() == "pythonw.exe":
                    console_python = os.path.join(os.path.dirname(python_executable), "python.exe")
                    if os.path.isfile(console_python):
                        python_executable = console_python

                # 取得目前實際使用的引擎版本；專案內 exe 優先於 Python 模組。
                local_exe = os.path.join(base_dir, "yt-dlp.exe")
                if os.path.isfile(local_exe):
                    current_result = run_hidden([local_exe, "--version"])
                    update_command = [local_exe, "-U"]
                else:
                    current = importlib.metadata.version("yt-dlp")
                    current_result = type("Result", (), {"returncode": 0, "stdout": current, "stderr": ""})()
                    update_command = [python_executable, "-m", "pip", "install", "--upgrade", "yt-dlp"]
                if current_result.returncode:
                    raise RuntimeError(current_result.stderr.strip() or "無法取得目前 yt-dlp 版本")
                current = current_result.stdout.strip().splitlines()[-1].strip()

                # PyPI 回傳的第一個版本就是目前可安裝的最新穩定版本。
                latest_result = run_hidden([
                    python_executable, "-m", "pip", "index", "versions", "yt-dlp",
                    "--disable-pip-version-check",
                ])
                if latest_result.returncode:
                    raise RuntimeError(latest_result.stderr.strip() or "無法查詢 PyPI 最新版本")
                match = re.search(r"yt-dlp\s*\(([^)]+)\)", latest_result.stdout)
                if not match:
                    match = re.search(r"Available versions:\s*([^,\s]+)", latest_result.stdout)
                if not match:
                    raise RuntimeError("無法解析 PyPI 最新版本")
                latest = match.group(1).strip()

                if Version(current) >= Version(latest):
                    _update_log(f"yt-dlp 已為最新版本：)")
                    _show_info("檢查更新", f"yt-dlp 已為最新版本：)\n目前版本：{current}")
                    return

                _update_log("yt-dlp 有新版本，正在為您安裝")
                result = run_hidden(update_command)
                if result.returncode:
                    raise RuntimeError(result.stderr.strip() or "yt-dlp 更新失敗")
                _update_log(f"yt-dlp 更新完成：{current} → {latest}")
                _show_info("檢查更新", f"yt-dlp 更新完成。\n目前版本：{latest}")
            except Exception as e:
                _update_log(f"更新過程發生錯誤：{str(e)}")
                _show_error("更新失敗", f"自動更新失敗：{str(e)}")
        
        # 在獨立線程中執行更新，避免阻塞 UI
        update_thread = threading.Thread(target=_check_update_thread, daemon=True)
        update_thread.start()

    # download control
    def start_download(base_dir, url_entry, fmt_combo, quality_combo, cookie_file_entry, path_entry, start_button, pbar, logbox):
        urlv = url_entry.get().strip()
        if not urlv:
            messagebox.showwarning("未輸入網址", "請貼上網址或匯入 txt。")
            return
        if downloader.find_ffmpeg(base_dir) is None:
            messagebox.showwarning(
                "缺少 FFmpeg",
                "找不到 FFmpeg，請將 ffmpeg.exe 放入程式資料夾，或將 FFmpeg 加入系統 PATH 後再試一次。",
            )
            append_log("下載取消：找不到 FFmpeg。\n")
            logbox.see("end")
            return
        dest = path_entry.get().strip() or os.path.join(base_dir, "Download")
        os.makedirs(dest, exist_ok=True)
        fmt = fmt_combo.get()
        quality = quality_combo.get()
        cookie_file = cookie_file_entry.get().strip()
        cfg["last_format"] = fmt
        cfg["last_quality"] = quality
        cfg["cookie_file"] = cookie_file
        cfg["open_file_after_download"] = bool(open_file_var.get())
        cfg["open_folder_after_download"] = bool(open_folder_var.get())
        cfg["write_subtitles"] = bool(subtitles_var.get())
        cfg["embed_subtitles"] = bool(embed_subtitles_var.get())
        cfg["write_thumbnail"] = bool(thumbnail_var.get())
        cfg["add_metadata"] = bool(metadata_var.get())
        config_manager.save(os.path.join(base_dir, "config", "settings.json"), cfg)
        # disable button while running
        start_button.configure(state="disabled")
        pbar.reset()
        append_log(f"開始下載 -> {urlv} 格式：{fmt} 畫質：{quality} 輸出：{dest}\n")
        logbox.see("end")

        # yt-dlp can emit many lines per second. Coalesce them so the Tk main
        # loop is not flooded with one root.after callback per line, which can
        # make the progress bar appear frozen during fast downloads.
        progress_state = {
            "percent": None,
            "detail": None,
            "lines": [],
            "scheduled": False,
            "progress_scheduled": False,
        }

        def flush_percent():
            progress_state["progress_scheduled"] = False
            percent = progress_state["percent"]
            if percent is not None:
                pbar.set(percent)
            if progress_state["detail"]:
                pbar.set_progress_info(progress_state["detail"])

        def flush_progress():
            progress_state["scheduled"] = False
            percent = progress_state["percent"]
            lines = progress_state["lines"]
            progress_state["lines"] = []
            if percent is not None:
                pbar.set(percent)
            for line in lines:
                append_log(line + "\n")
            if lines:
                logbox.see("end")

        def progress_cb(percent, text):
            # Percentage updates are kept separate from log batching so the
            # bar follows yt-dlp immediately instead of waiting for log flush.
            if percent is not None:
                progress_state["percent"] = max(0.0, min(1.0, percent))
                progress_state["detail"] = text or progress_state["detail"]
                if not progress_state["progress_scheduled"]:
                    progress_state["progress_scheduled"] = True
                    root.after(16, flush_percent)

            # Log lines are batched independently to keep the Tk main loop
            # responsive when yt-dlp emits many progress lines.
            if text:
                if percent is not None:
                    progress_state["detail"] = text
                progress_state["lines"].append(text)
            if not progress_state["scheduled"]:
                progress_state["scheduled"] = True
                root.after(50, flush_progress)

        def finished_cb(success, msg):
            def _done():
                if progress_state["progress_scheduled"]:
                    flush_percent()
                if progress_state["scheduled"]:
                    flush_progress()
                start_button.configure(state="normal")
                if success:
                    pbar.set(1.0)
                    pbar.set_progress_info("Downloading:100.0%")
                    if open_folder_var.get():
                        _open_path(dest)
                    if open_file_var.get():
                        _open_latest_file(dest)
                else:
                    pbar.set_status("錯誤")
                    error_text = (msg or "").lower()
                    if "yt-dlp 版本過舊" in msg or "older than 90 days" in error_text:
                        messagebox.showwarning(
                            "下載失敗",
                            "請嘗試更新yt-dlp 以確保下載功能",
                        )
                    elif "ffmpeg 缺失" in error_text or "ffmpeg" in error_text or "ffprobe" in error_text:
                        messagebox.showwarning(
                            "缺少 FFmpeg",
                            "找不到或無法執行 FFmpeg，請將 ffmpeg.exe 放入程式資料夾，或將 FFmpeg 加入系統 PATH 後再試一次。",
                        )
                    elif "youtube 要求重新載入頁面" in error_text:
                        messagebox.showwarning(
                            "YouTube 驗證失敗",
                            "請先按「檢查更新」更新 yt-dlp；若仍失敗，請重新匯入 YouTube Cookies.txt 後再試一次。",
                        )
                    elif any(keyword in error_text for keyword in (
                            "cookie", "cookies.txt", "dpapi", "precondition failed", "http error 412")):
                        messagebox.showwarning(
                            "下載失敗",
                            "請嘗試使用瀏覽器擴充功能獲取 Cookies.txt ，並於匯入後再試一次",
                        )
                append_log(f"結束：{msg}\n")
                logbox.see("end")
                history.add(base_dir, urlv, dest, fmt, quality, success, msg)
            root.after(0, _done)

        def _open_path(path):
            try:
                if os.name == "nt":
                    os.startfile(path)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", path])
                else:
                    subprocess.Popen(["xdg-open", path])
            except Exception as exc:
                LOG.warning("Unable to open path %s: %s", path, exc)

        def _open_latest_file(path):
            try:
                files = [os.path.join(path, name) for name in os.listdir(path)]
                files = [f for f in files if os.path.isfile(f) and not f.endswith((".part", ".ytdl"))]
                if files:
                    _open_path(max(files, key=os.path.getmtime))
            except Exception as exc:
                LOG.warning("Unable to open latest file in %s: %s", path, exc)

        # if urls begins with file: delegate directly
        advanced = {
            "write_subtitles": subtitles_var.get(),
            "embed_subtitles": embed_subtitles_var.get(),
            "write_thumbnail": thumbnail_var.get(),
            "add_metadata": metadata_var.get(),
        }
        downloader.run_download(base_dir, urlv, dest, fmt, quality, cookie_browser="無", cookie_file=cookie_file,
                                advanced=advanced,
                                progress_callback=progress_cb, finished_callback=finished_cb)

    # start main loop
    root.mainloop()
