# modules/urls.py
"""輸入網址的整理。

規則：
1. 文字裡有 http:// 或 https:// 時，它前面的任何字元都不讀，從它開始讀到第一個空白為止。
2. 沒有 http(s):// 時，找第一個「像 youtube.com、facebook.com/…」的合法網址，補上 https://。
   這個判斷只認 ASCII 網域（含 xn-- 開頭的 punycode），避免把緊貼在網址前面的中文字也當成網域的一部分。
3. 都找不到就回傳 None。

網址緊接著中文句子時（例如「…v=abc，謝謝」沒有空白），遇到全形標點（，。！？、「」（）等）就停止；
網址尾端的半形句子標點與沒有成對的右括號也會被去掉。
"""
import re
from urllib.parse import urlparse

_SCHEME_RE = re.compile(r"https?://", re.I)

# 沒有 http(s):// 的網址：網域（至少兩段，最後一段是 2 個以上的字母或 xn--）＋選用的埠號、路徑、查詢字串。
# 前一個字元不能是網址／帳號的一部分（避免把 user@x.com 或 ftp://x.com 當成網址），後一個字元不能讓網域變長。
_BARE_URL_RE = re.compile(
    r"(?<![A-Za-z0-9@._/:-])"
    r"(?P<url>(?:[A-Za-z0-9]+(?:-+[A-Za-z0-9]+)*\.)+(?:[A-Za-z]{2,63}|xn--[A-Za-z0-9-]{2,59})"
    r"(?::\d{1,5})?(?:[/?#]\S*)?)"
    r"(?![A-Za-z0-9@_-])")

_FULLWIDTH_STOP_RE = re.compile(r"[，。、；：！？（）「」『』【】《》“”‘’]")  # 網址不會包含這些，遇到就是句子接上來了
_TAIL_PUNCTUATION = set(".,;:!?\"'")
_CLOSERS = {")": "(", "]": "[", "}": "{", ">": "<"}


def _trim_tail(url):
    url = _FULLWIDTH_STOP_RE.split(url, 1)[0]
    while url:
        last = url[-1]
        if last in _TAIL_PUNCTUATION:
            url = url[:-1]
        elif last in _CLOSERS and url.count(_CLOSERS[last]) < url.count(last):  # 沒有成對的右括號
            url = url[:-1]
        else:
            break
    return url


def _has_host(url):
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
        return False
    try:
        return bool(urlparse(url).hostname)
    except ValueError:
        return False


def extract_url(text):
    """從一段文字取出要下載的網址；找不到回傳 None。"""
    text = str(text or "")
    for match in _SCHEME_RE.finditer(text):
        token = text[match.start():].split(None, 1)[0]
        url = _trim_tail(match.group(0).lower() + token[len(match.group(0)):])
        if _has_host(url):
            return url
    for match in _BARE_URL_RE.finditer(text):
        url = "https://" + _trim_tail(match.group("url"))
        if _has_host(url):
            return url
    return None
