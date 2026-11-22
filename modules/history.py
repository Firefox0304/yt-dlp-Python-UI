# modules/history.py
import json
import os
import threading
from datetime import datetime

from modules import utils

MAX_ITEMS = 500
_lock = threading.Lock()


def _path(base_dir):
    return os.path.join(base_dir, "config", "history.json")


def _read(base_dir):
    try:
        with open(_path(base_dir), "r", encoding="utf-8") as f:
            items = json.load(f)
        return items if isinstance(items, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def add(base_dir, url, output_dir, fmt, quality, success, message, files=None, title=""):
    with _lock:
        items = _read(base_dir)
        items.append({
            "time": datetime.now().isoformat(timespec="seconds"),
            "url": url,
            "title": title or "",
            "output_dir": output_dir,
            "format": fmt,
            "quality": quality,
            "success": bool(success),
            "message": message or "",
            "files": list(files or []),
        })
        utils.atomic_write_json(_path(base_dir), items[-MAX_ITEMS:], ensure_ascii=False, indent=2)


def load(base_dir):
    with _lock:
        return _read(base_dir)


def clear(base_dir):
    with _lock:
        utils.atomic_write_json(_path(base_dir), [], ensure_ascii=False, indent=2)


def display_title(item):
    """歷史紀錄要顯示的標題：優先用紀錄的標題，其次檔名，最後才是網址。"""
    title = str(item.get("title") or "").strip()
    if not title:
        files = item.get("files") or []
        if files:
            title = os.path.splitext(os.path.basename(str(files[-1]).replace("\\", "/")))[0]
    return title or str(item.get("url", ""))


def latest_existing_file(items):
    """從歷史紀錄（舊→新）找最近一次下載成功、而且檔案還在的檔案路徑；找不到回傳 None。"""
    for item in reversed(items or []):
        if not item.get("success"):
            continue
        for path in reversed(item.get("files") or []):
            if isinstance(path, str) and os.path.isfile(path):
                return path
    return None
