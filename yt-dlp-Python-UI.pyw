"""yt-dlp-python-UI 啟動器（加載工具）。

所有實際程式都在 modules/ 內；這個檔案只負責找到程式資料夾並交給 modules.bootstrap，
由它顯示啟動畫面、檢查依賴並載入主程式。
"""
import os
import sys

BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
    else os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

if __name__ == "__main__":
    try:
        from modules import bootstrap
    except ImportError as exc:  # modules 資料夾遺失或損毀時，至少讓使用者知道原因
        try:
            import tkinter as tk
            from tkinter import messagebox
        except ImportError:  # 連 tkinter 都沒有（Debian／Ubuntu 的 python3 預設不含）
            if sys.stderr:
                sys.stderr.write("找不到 tkinter（圖形介面元件）。\n"
                                 "Debian／Ubuntu 請執行：sudo apt install python3-tk\n"
                                 "其他系統請安裝含 Tk 的 Python。\n")
            sys.exit(1)
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("啟動錯誤", f"找不到程式模組（modules 資料夾）：{exc}\n請重新解壓縮完整的程式資料夾。")
        root.destroy()
        sys.exit(1)
    bootstrap.main(BASE_DIR)
