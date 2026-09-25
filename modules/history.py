import json
import os
from datetime import datetime


def _path(base_dir):
    return os.path.join(base_dir, "config", "history.json")


def add(base_dir, url, output_dir, fmt, quality, success, message):
    path = _path(base_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "r", encoding="utf-8") as f:
            items = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        items = []
    items.append({
        "time": datetime.now().isoformat(timespec="seconds"),
        "url": url,
        "output_dir": output_dir,
        "format": fmt,
        "quality": quality,
        "success": bool(success),
        "message": message or "",
    })
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items[-500:], f, ensure_ascii=False, indent=2)


def load(base_dir):
    try:
        with open(_path(base_dir), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
