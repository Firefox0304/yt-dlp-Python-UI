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

# 範例清單自動產生：settings.json 的 examplegeneration 沒有這一條、為 false 或空白時，啟動會產生 example/example.txt，
# 成功後記成 true。刻意不放進 DEFAULT_CONFIG，所以預設的設定檔不會有這一條。
EXAMPLE_KEY = "examplegeneration"
EXAMPLE_CONTENT = (
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ\n"
    "\n"
    "# 在這裡放範例網址（每行一個，# 開頭為註解，重複網址會自動略過）\n"
    "# 建議自己新增一個 .txt，格式相同，再用「匯入批次下載」功能\n"
)

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


def should_generate_example(config):
    """沒有這一條、False、None、空白字串（含只有空格）或字串 "false" 時回傳 True；其餘（例如 True）回傳 False。"""
    value = config.get(EXAMPLE_KEY)
    if value is None or value is False:
        return True
    if isinstance(value, str):
        return value.strip() == "" or value.strip().lower() == "false"
    return False


def ensure_example(base_dir, config_path, config):
    """依 examplegeneration 決定是否產生 example/example.txt。

    回傳 "skipped"（不需處理）、"created"（已產生）、"existed"（檔案已存在，不覆蓋）或 "failed"（寫入失敗）。
    成功或檔案已存在後把 examplegeneration 記成 True；失敗時不改設定，下次啟動會再試一次。
    """
    if not should_generate_example(config):
        return "skipped"
    example_path = os.path.join(base_dir, "example", "example.txt")
    try:
        outcome = "existed"
        if not os.path.exists(example_path):
            os.makedirs(os.path.dirname(example_path), exist_ok=True)
            with open(example_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(EXAMPLE_CONTENT)
            outcome = "created"
        config[EXAMPLE_KEY] = True
        save(config_path, config)
        logger.get().info("Example list %s: %s", outcome, example_path)
        return outcome
    except OSError as e:
        logger.get().warning("Failed generating example list: %s", e)
        return "failed"


def save(path, config):
    with _lock:
        utils.atomic_write_json(path, config, indent=2, ensure_ascii=False)
    logger.get().info("Saved config to %s", path)
