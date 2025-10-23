# modules/updater.py
import os
from modules import logger

def check_yt_dlp_existence(base_dir, auto_check=True):
    """
    先檢查是否有本地 yt-dlp.exe，否則檢查是否安裝 yt_dlp module.
    auto_check: 若為 True，logger 會提示；若你要自動下載，可擴充這裡。
    """
    exe_path = os.path.join(base_dir, "yt-dlp.exe")
    has_exe = os.path.exists(exe_path)
    try:
        import yt_dlp as _  # type: ignore
        has_module = True
    except Exception:
        has_module = False

    if has_exe:
        logger.get().info("Found local yt-dlp.exe")
    elif has_module:
        logger.get().info("yt_dlp python module available")
    else:
        logger.get().warning("No yt-dlp detected. Please put yt-dlp.exe in project root or pip install yt-dlp.")
        if auto_check:
            # 只提示；若要自動下載，可實作requests抓 release
            pass
    return has_exe or has_module
