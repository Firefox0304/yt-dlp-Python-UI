# modules/ui.py
import os
import tkinter as tk
import customtkinter as ctk
from tkinter import filedialog, messagebox
from modules import config_manager, downloader, logger, utils
import threading

ctk.set_appearance_mode("Light")  # 預設亮色
ctk.set_default_color_theme("blue")

LOG = logger.get()

def launch_ui(cfg, base_dir):
    root = ctk.CTk()
    root.title("yt-dlp-python-UI v5.3 | 作者：Firefox_0304 | 協助Bot：ChatGPT")
    root.geometry("920x560")
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
            banner_label = ctk.CTkLabel(top_frame, image=banner_img, text="")
            banner_label.image = banner_img
            banner_label.pack(expand=True)
        except Exception:
            lbl = ctk.CTkLabel(top_frame, text="yt-dlp-download-python-UI v5.3    作者：Firefox_0304    協助Bot：ChatGPT", font=("Helvetica", 14, "bold"))
            lbl.pack(padx=6, pady=10)
    else:
        lbl = ctk.CTkLabel(top_frame, text="yt-dlp-download-python-UI v5.3    作者：Firefox_0304    協助Bot：ChatGPT", font=("Helvetica", 14, "bold"))
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

    url_label = ctk.CTkLabel(left, text="網址：")
    url_label.grid(row=1, column=0, sticky="w", padx=8, pady=6)
    url_entry = ctk.CTkEntry(left, width=560)
    url_entry.grid(row=1, column=1, sticky="we", padx=8, pady=6)

    txt_btn = ctk.CTkButton(left, text="匯入 .txt", width=100, command=lambda: import_txt(url_entry))
    txt_btn.grid(row=1, column=2, padx=6, pady=6)

    path_label = ctk.CTkLabel(left, text="儲存位置：")
    path_label.grid(row=2, column=0, sticky="w", padx=8, pady=6)
    default_path = os.path.join(base_dir, cfg.get("download_path", "Download"))
    path_entry = ctk.CTkEntry(left, width=560)
    path_entry.insert(0, default_path)
    path_entry.grid(row=2, column=1, sticky="we", padx=8, pady=6)

    browse_btn = ctk.CTkButton(left, text="瀏覽", width=100, command=lambda: browse_folder(path_entry))
    browse_btn.grid(row=2, column=2, padx=6, pady=6)

    # Start button and progress bar
    start_btn = ctk.CTkButton(left, text="開始下載", width=140, command=lambda: start_download(base_dir, url_entry, fmt_combo, quality_combo, path_entry, start_btn, progress_bar, log_text))
    start_btn.grid(row=3, column=1, sticky="w", padx=8, pady=(6,10))

    progress_bar = ctk.CTkProgressBar(left, width=720)
    progress_bar.set(0.0)
    progress_bar.grid(row=4, column=0, columnspan=3, padx=8, pady=(6,4), sticky="we")

    percent_label = ctk.CTkLabel(left, text="0.0%")
    percent_label.grid(row=5, column=1, sticky="e", padx=8)

    # log text box
    log_text = ctk.CTkTextbox(left, width=720, height=160)
    log_text.grid(row=6, column=0, columnspan=3, padx=8, pady=(6,4))
    log_text.insert("0.0", "狀態/日誌…（會顯示 yt-dlp 輸出）\n")

    # Right side: examples / change background / mode
    right_label = ctk.CTkLabel(right, text="快速操作")
    right_label.pack(padx=8, pady=(8,4))

    ex_txt_btn = ctk.CTkButton(right, text="載入範例", command=lambda: load_example(base_dir, url_entry))
    ex_txt_btn.pack(fill="x", padx=8, pady=6)

    check_update_btn = ctk.CTkButton(right, text="檢查更新", command=lambda: check_update_action(base_dir, log_text))
    check_update_btn.pack(fill="x", padx=8, pady=6)

    help_btn = ctk.CTkButton(right, text="說明", command=lambda: show_help())
    help_btn.pack(fill="x", padx=8, pady=6)

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
            entry.delete(0, "end")
            entry.insert(0, d)

    def import_txt(entry):
        f = filedialog.askopenfilename(filetypes=[("Text files","*.txt"),("All files","*.*")])
        if not f:
            return
        entry.delete(0, "end")
        entry.insert(0, "file:" + f)
        log_text.insert("end", f"匯入 txt：{f}\n")
        log_text.see("end")

    def load_example(base_dir, entry):
        ex = os.path.join(base_dir, "example", "example.txt")
        if os.path.exists(ex):
            entry.delete(0, "end")
            entry.insert(0, "file:" + ex)
            log_text.insert("end", f"已載入 example.txt\n")
        else:
            messagebox.showinfo("範例不存在", f"請把 example/example.txt 放進專案中")
    
    def show_help():
        messagebox.showinfo("說明", "把網址貼到『網址』欄位，或匯入 .txt 批次。\n選擇格式後按「開始下載」。")

    def set_appearance(mode):
        ctk.set_appearance_mode(mode)
        cfg["appearance"] = mode
        config_manager.save(os.path.join(base_dir, "config", "settings.json"), cfg)

    def check_update_action(base_dir, logbox):
        import subprocess, sys
        logbox.insert("end", "正在檢查更新，請稍候...\n")
        logbox.see("end")
        
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
            # 1. 檢查是否已有最新版本
            check_result = subprocess.run(
                [sys.executable, "-m", "pip", "list", "--outdated", "--format=freeze"],
                capture_output=True, text=True, startupinfo=startupinfo, creationflags=creationflags
            )
            
            if "yt-dlp" not in check_result.stdout:
                logbox.insert("end", "目前已是最新版本。\n")
                logbox.see("end")
                messagebox.showinfo("檢查更新", "目前的下載引擎已是最新版本，無需更新。")
                return

            # 2. 執行更新
            logbox.insert("end", "偵測到新版本，正在更新下載引擎 (yt-dlp)...\n")
            logbox.see("end")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
                startupinfo=startupinfo, creationflags=creationflags
            )
            logbox.insert("end", "更新完成！\n")
            logbox.see("end")
            messagebox.showinfo("更新成功", "下載引擎已更新至最新版本！")
        except Exception as e:
            logbox.insert("end", f"更新過程發生錯誤：{str(e)}\n")
            logbox.see("end")
            messagebox.showerror("更新失敗", f"自動更新失敗：{str(e)}")

    # download control
    def start_download(base_dir, url_entry, fmt_combo, quality_combo, path_entry, start_button, pbar, logbox):
        urlv = url_entry.get().strip()
        if not urlv:
            messagebox.showwarning("未輸入網址", "請貼上網址或匯入 txt。")
            return
        dest = path_entry.get().strip() or os.path.join(base_dir, "Download")
        os.makedirs(dest, exist_ok=True)
        fmt = fmt_combo.get()
        quality = quality_combo.get()
        cfg["last_format"] = fmt
        cfg["last_quality"] = quality
        config_manager.save(os.path.join(base_dir, "config", "settings.json"), cfg)
        # disable button while running
        start_button.configure(state="disabled")
        logbox.insert("end", f"開始下載 -> {urlv} 格式：{fmt} 畫質：{quality} 輸出：{dest}\n")
        logbox.see("end")

        def progress_cb(percent, text):
            # run in different thread -> schedule on main thread
            def _ui_update():
                if percent is not None:
                    pbar.set(percent)
                    plabel_text = f"{percent*100:.1f}%"
                    percent_label.configure(text=plabel_text)
                logbox.insert("end", text + "\n")
                logbox.see("end")
            root.after(0, _ui_update)

        def finished_cb(success, msg):
            def _done():
                start_button.configure(state="normal")
                if success:
                    pbar.set(1.0)
                    percent_label.configure(text="100.0%")
                else:
                    percent_label.configure(text="錯誤")
                logbox.insert("end", f"結束：{msg}\n")
                logbox.see("end")
            root.after(0, _done)

        # if urls begins with file: delegate directly
        downloader.run_download(base_dir, urlv, dest, fmt, quality, progress_callback=progress_cb, finished_callback=finished_cb)

    # start main loop
    root.mainloop()

