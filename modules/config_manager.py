# modules/config_manager.py
import json
import os
import shutil
import threading

from modules import logger, utils

DEFAULT_CONFIG = {
    "download_path": "Download",
    "last_format": "mp4",
    "last_quality": "預設",
    "cookie_browser": "無",
    "appearance": "System",
    "font_registration": "private",
    "open_file_after_download": False,
    "open_folder_after_download": True,
    "write_subtitles": False,
    "embed_subtitles": False,
    "subtitle_langs": "zh-Hant,zh-TW,en.*",
    "write_thumbnail": False,
    "add_metadata": False,
    "number_playlist_items": False,
    "use_archive": False,
    "filename_template": "%(title)s.%(ext)s",
}

# 已移除或刻意不記憶的設定：載入時一律丟棄（Cookies 檔案路徑涉及帳號登入資料，每次啟動都要重新選擇）。
DEPRECATED_KEYS = ("download_playlist", "cookie_file")

_lock = threading.Lock()


def load_or_init(path):
    """讀取設定並與預設值合併（新版新增的設定項，舊設定檔也不會缺鍵）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError("settings.json 的最外層必須是物件")
            cfg = {**DEFAULT_CONFIG, **loaded}
            for key in DEPRECATED_KEYS:
                cfg.pop(key, None)
            logger.get().info("Loaded config from %s", path)
            return cfg
        except Exception as e:
            logger.get().warning("Failed loading config, using defaults: %s", e)
            # 損毀的設定檔先備份，避免使用者的資料被預設值直接覆蓋。
            try:
                shutil.copy2(path, path + ".bak")
            except OSError:
                pass
    cfg = DEFAULT_CONFIG.copy()
    save(path, cfg)
    logger.get().info("Wrote default config to %s", path)
    return cfg


def save(path, config):
    with _lock:
        utils.atomic_write_json(path, config, indent=2, ensure_ascii=False)
    logger.get().info("Saved config to %s", path)
