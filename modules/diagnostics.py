# modules/diagnostics.py
"""匯出診斷包：把「系統診斷」報告、本次日誌、設定檔打包成一個 zip，方便別人幫忙除錯。

隱私：路徑中的使用者名稱會被遮蔽；不含 Cookie 內容與下載歷史。
日誌仍可能含有下載過的網址（命令列會記錄），匯出後請使用者分享前自行檢查。
"""
import json
import os
import re
import zipfile

from modules import logger
from modules import channel

MAX_LOG_BYTES = 2 * 1024 * 1024   # 日誌太大時只取最後這麼多，除錯需要的通常在尾端
MAX_REPORT_CHARS = 200_000        # 網頁傳來的診斷文字長度上限

# C:\Users\某人\...、D:/Users/某人/...、/Users/某人/...、/home/某人/...
_USER_DIR_RE = re.compile(r"(?i)((?:[A-Za-z]:)?[\\/](?:Users|home)[\\/])[^\\/\s\"'|<>:*?]+")


def redact(text, home=None):
    """遮蔽使用者家目錄與路徑中的使用者名稱。"""
    text = str(text)
    home = home if home is not None else os.path.expanduser("~")
    if home and home not in ("~", os.sep, "/"):
        for variant in {home, home.replace("\\", "/"), home.replace("/", "\\")}:
            text = re.sub(re.escape(variant), "%USERPROFILE%", text, flags=re.I)
    return _USER_DIR_RE.sub(lambda m: m.group(1) + "<user>", text)


def _redact_value(value, home):
    """遞迴遮蔽設定值裡的字串。要在轉成 JSON 之前做：轉成 JSON 後反斜線會變成兩個，路徑就對不上了。"""
    if isinstance(value, str):
        return redact(value, home)
    if isinstance(value, dict):
        return {key: _redact_value(item, home) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, home) for item in value]
    return value


def _read_log_tail(log_dir):
    path = os.path.join(log_dir, "latest.log")
    for handler in logger.get().handlers:  # 先把緩衝寫進檔案
        try:
            handler.flush()
        except Exception:  # noqa: BLE001
            pass
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > MAX_LOG_BYTES:
                f.seek(size - MAX_LOG_BYTES)
            data = f.read()
    except OSError as exc:
        return f"(無法讀取 latest.log：{exc})\n"
    return data.decode("utf-8", errors="replace")


def build_package(dest_path, base_dir, report_text, cfg, home=None):
    """寫出診斷包 zip，回傳放進去的檔名清單。"""
    header = f"{channel.title()}\n\n"
    files = {
        "diagnostics.txt": redact(header + str(report_text or "")[:MAX_REPORT_CHARS], home),
        "latest.log": redact(_read_log_tail(os.path.join(base_dir, "logs")), home),
        "settings.json": json.dumps(_redact_value(cfg or {}, home), ensure_ascii=False, indent=2),
    }
    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return list(files)
