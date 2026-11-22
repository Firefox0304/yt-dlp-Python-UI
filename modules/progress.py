# modules/progress.py
"""yt-dlp 進度行的範本與解析。

下載時用 --progress-template 讓 yt-dlp 輸出固定格式的進度行：

    Downloading:12.3% | 1.50MiB/s | ETA 00:10 | FRAG 8

FRAG 欄位只有「分片下載」（例如 HLS／DASH）才有數字，其他是 NA；網頁靠它判斷
「下載加速（-N）」對這支影片有沒有作用。進度行的格式與解析全部集中在這個檔案，
改範本時只需要動這裡（測試在 tests/test_progress.py）。
"""
import re
from dataclasses import dataclass
from typing import Optional

PROGRESS_TEMPLATE = ("Downloading:%(progress._percent_str)s | "
                     "%(progress._speed_str)s | ETA %(progress._eta_str)s | "
                     "FRAG %(progress.fragment_count)s")  # 分片下載才有數字，其他是 NA

# 自己的範本（Downloading:）與 yt-dlp 預設格式（[download]）都認得
_PERCENT_RE = re.compile(r"^(?:Downloading:|\[download\])\s*(\d+(?:[.,]\d+)?)\s*%")
_ITEM_PREFIX_RE = re.compile(r"^\[\d+/\d+\]\s*")  # 多個項目時，呼叫端會在行首加上「[2/5] 」
_NO_VALUE = frozenset(("", "NA", "N/A", "None"))  # 沒有分片數時 yt-dlp 印出的內容


@dataclass(frozen=True)
class ProgressInfo:
    percent: Optional[float]  # 0~100；範本行但百分比是 N/A 時為 None
    speed: str                # 原樣的速度文字（可能是 N/A）；沒有時為 ""
    eta: str                  # 去掉 "ETA" 前綴後的文字；沒有時為 ""
    fragmented: bool          # True 表示這是分片下載，-N 才有作用


def _field(parts, index, label=""):
    value = parts[index].strip() if len(parts) > index else ""
    if label and value.startswith(label):
        value = value[len(label):].strip()
    return value


def parse_progress_line(line):
    """解析一行輸出；不是進度行就回傳 None。允許行首有「[i/n] 」前綴。"""
    text = _ITEM_PREFIX_RE.sub("", (line or "").strip())
    match = _PERCENT_RE.match(text)
    if not match and not text.startswith("Downloading:"):
        return None

    percent = None
    if match:
        percent = max(0.0, min(100.0, float(match.group(1).replace(",", "."))))

    parts = [p.strip() for p in text.split("|")]
    speed = _field(parts, 1) if len(parts) >= 3 else ""
    eta = _field(parts, 2, "ETA") if len(parts) >= 3 else ""
    frag = _field(parts, 3, "FRAG") if len(parts) >= 4 else ""
    return ProgressInfo(percent=percent, speed=speed, eta=eta, fragmented=frag not in _NO_VALUE)
