# modules/config_manager.py
import json
import os
from modules import logger

DEFAULT_CONFIG = {
    "download_path": "downloads",
    "last_format": "mp4",
    "appearance": "Light",
    "auto_check_yt_dlp": True,
    "use_internal_ytdlp": True  # prefer python module if available
}

def load_or_init(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            logger.get().info("Loaded config from %s", path)
            return cfg
        except Exception as e:
            logger.get().warning("Failed loading config, using defaults: %s", e)
    # write default
    with open(path, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, indent=2)
    logger.get().info("Wrote default config to %s", path)
    return DEFAULT_CONFIG.copy()

def save(path, config):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    logger.get().info("Saved config to %s", path)
