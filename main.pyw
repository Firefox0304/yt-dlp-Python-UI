import os
import sys
import subprocess
import tkinter as tk
from tkinter import messagebox

# 依賴套件清單
REQUIRED_PACKAGES = ["customtkinter", "yt-dlp", "pillow", "requests"]

def check_and_install_dependencies():
    """
    檢查必要套件是否已安裝，若缺少則跳彈窗詢問是否安裝。
    """
    missing_packages = []
    for package in REQUIRED_PACKAGES:
        try:
            if package == "pillow":
                __import__("PIL")
            else:
                __import__(package.replace("-", "_"))
        except ImportError:
            missing_packages.append(package)
    
    if missing_packages:
        msg = f"偵測到您的電腦缺少以下必要組件：\n{', '.join(missing_packages)}\n\n是否立即自動下載並安裝？\n(這將確保程式能正常啟動)"
        if messagebox.askyesno("環境檢查", msg):
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
                subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing_packages)
                messagebox.showinfo("安裝成功", "所有組件已安裝完成！程式即將啟動。")
                return True
            except Exception as e:
                messagebox.showerror("安裝失敗", f"自動安裝失敗：{str(e)}\n請手動執行 pip install -r requirements.txt")
                return False
        else:
            messagebox.showwarning("警告", "缺少必要組件，程式可能無法正常運作。")
            return False
    return True

def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def ensure_download_folder(base_dir: str):
    """If missing, create a `Download` folder in the given base directory."""
    download_dir = os.path.join(base_dir, "Download")
    if not os.path.isdir(download_dir):
        os.makedirs(download_dir, exist_ok=True)


def start_app():
    from modules import config_manager, ui, logger
    base_dir = get_base_dir()
    ensure_download_folder(base_dir)
    log_path = os.path.join(base_dir, "logs", "app.log")
    logger.setup_logging(log_path)
    config_path = os.path.join(base_dir, "config", "settings.json")
    cfg = config_manager.load_or_init(config_path)
    ui.launch_ui(cfg, base_dir)

if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    
    # 僅在啟動前檢查依賴套件是否齊全
    if check_and_install_dependencies():
        root.destroy()  # 銷毀隱藏的root，因為不再需要
        # 啟動主程式
        try:
            start_app()
        except Exception as e:
            # 如果啟動失敗，需要重新創建root來顯示錯誤
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("啟動錯誤", f"程式啟動失敗：{str(e)}")
            root.destroy()
    else:
        root.destroy()
