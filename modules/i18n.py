# modules/i18n.py — 多語言：每個語言一個資料夾 locales/<語言代號>/，裡面所有 .json 檔合併成一份文字表
# （strings.json 介面文字、errors.json 錯誤訊息 err.*、logs.json 日誌訊息 log.*），程式用 t("代號") 取字。
import json
import locale
import os

from modules import logger

LOG = logger.get()

LANGUAGES = (("zh-TW", "繁體中文"), ("zh-CN", "简体中文"), ("en", "English"), ("ja", "日本語"))
CODES = tuple(code for code, _ in LANGUAGES)
AUTO = "auto"
BASE_LANGUAGE = "zh-TW"      # 開發用的基準語言；任何語言缺的文字最後都退回它
FALLBACK_LANGUAGE = "en"     # 缺文字時先退回英文，再退回基準語言

_locales_dir = ""
_cache = {}
_current = BASE_LANGUAGE


def init(base_dir):
    global _locales_dir
    _locales_dir = os.path.join(base_dir, "locales")
    _cache.clear()


def _load(code):
    """讀 locales/<code>/ 底下所有 .json 並合併；缺資料夾或壞檔只記警告，其餘照常。"""
    if code not in _cache:
        data = {}
        folder = os.path.join(_locales_dir, code)
        try:
            names = sorted(n for n in os.listdir(folder) if n.lower().endswith(".json"))
        except OSError as exc:
            LOG.warning("Failed loading locale %s: %s", code, exc)
            names = []
        for name in names:
            try:
                with open(os.path.join(folder, name), "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data.update({str(k): str(v) for k, v in loaded.items()})
            except (OSError, ValueError) as exc:
                LOG.warning("Failed loading locale %s/%s: %s", code, name, exc)
        _cache[code] = data
    return _cache[code]


def detect_system_language():
    """依 Windows 的顯示語言決定：繁中（臺灣／香港／澳門）→ zh-TW，簡中 → zh-CN，日文 → ja，其餘 → en。"""
    try:
        import ctypes
        langid = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
        primary = langid & 0x3FF
        if primary == 0x04:  # 中文
            return "zh-TW" if langid in (0x0404, 0x0C04, 0x1404, 0x7C04) else "zh-CN"
        if primary == 0x11:  # 日文
            return "ja"
        return "en"
    except Exception:  # noqa: BLE001 — 非 Windows 或取不到，改看環境的語系名稱
        pass
    try:
        name = (locale.getlocale()[0] or "").replace("_", "-").lower()
    except (ValueError, TypeError):
        name = ""
    if name.startswith("zh"):
        return "zh-TW" if any(tag in name for tag in ("tw", "hk", "mo", "hant")) else "zh-CN"
    if name.startswith("ja"):
        return "ja"
    return "en"


def normalize_setting(value):
    """設定檔裡的語言設定：'auto' 或支援的語言代號；其他值一律視為 'auto'。"""
    return value if value in CODES else AUTO


def resolve(setting):
    """把設定（'auto' 或語言代號）換成實際要用的語言代號。"""
    setting = normalize_setting(setting)
    return detect_system_language() if setting == AUTO else setting


def set_language(code):
    global _current
    _current = code if code in CODES else BASE_LANGUAGE


def current():
    return _current


def bundle(code=None):
    """某個語言的完整文字表（缺的退回英文、再退回基準語言），整包交給網頁用。"""
    code = code if code in CODES else _current
    merged = dict(_load(BASE_LANGUAGE))
    merged.update(_load(FALLBACK_LANGUAGE))
    merged.update(_load(code))
    return merged


def t(key, **kwargs):
    for code in (_current, FALLBACK_LANGUAGE, BASE_LANGUAGE):
        text = _load(code).get(key)
        if text is not None:
            break
    else:
        return key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text
