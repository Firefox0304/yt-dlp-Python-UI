# modules/fonts.py
"""介面字型：註冊隨附的 Noto Sans TC，找不到就退回系統字型（只依賴標準函式庫）。

重點：
- 只註冊靜態字型（fonts/*.ttf）。Windows 版 Tk 用 GDI，只看得到可變字型的預設實例（Noto Sans TC 是 Thin），
  所以可變字型不相容，也不要放進 fonts/。
- 註冊使用 FR_PRIVATE：字型只有本行程看得到，不會安裝到系統，程式結束即消失。
"""
import ctypes
import glob
import json
import os

FAMILY = "Noto Sans TC"
FALLBACK_FAMILIES = ("Microsoft JhengHei UI", "PingFang TC", "Noto Sans CJK TC")
FR_PRIVATE = 0x10
WM_FONTCHANGE = 0x001D

# 字型註冊方式（設定檔 font_registration）：
#   private：只有本行程看得到（預設，不會影響其他程式）
#   session：在這次 Windows 登入期間對所有程式可見，程式關閉時會移除（實驗選項）
MODE_PRIVATE, MODE_SESSION = "private", "session"
_state = {"mode": MODE_PRIVATE}


def font_dir(base_dir):
    return os.path.join(base_dir, "fonts")


def read_mode(base_dir):
    """從設定檔讀取字型註冊方式；沒有、讀不到或不認得時使用預設的 private。"""
    try:
        with open(os.path.join(base_dir, "config", "settings.json"), encoding="utf-8") as f:
            value = json.load(f).get("font_registration")
    except (OSError, ValueError, AttributeError):
        return MODE_PRIVATE
    return value if value in (MODE_PRIVATE, MODE_SESSION) else MODE_PRIVATE


def active_mode():
    return _state["mode"]


def _broadcast_font_change():
    """通知其他程式字型清單有變動（只有 session 模式需要）。"""
    try:
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, WM_FONTCHANGE, 0, 0, 0x2, 1000, None)
    except (OSError, AttributeError):
        pass


def _font_files(base_dir):
    return [os.path.abspath(p) for p in sorted(glob.glob(os.path.join(font_dir(base_dir), "*.ttf")))]


def register(base_dir, mode=MODE_PRIVATE):
    """Windows：註冊 fonts/*.ttf，回傳成功的檔案數。其他平台不處理（使用系統已安裝的字型）。"""
    if os.name != "nt":
        return 0
    mode = mode if mode in (MODE_PRIVATE, MODE_SESSION) else MODE_PRIVATE
    flags = FR_PRIVATE if mode == MODE_PRIVATE else 0
    count = 0
    for path in _font_files(base_dir):
        try:
            if ctypes.windll.gdi32.AddFontResourceExW(path, flags, 0):
                count += 1
        except (OSError, AttributeError):
            pass
    if count:
        _state["mode"] = mode
        if mode == MODE_SESSION:
            _broadcast_font_change()
    return count


def unregister(base_dir, mode=None):
    """session 模式在程式結束時移除字型（private 模式隨行程結束自動消失，不需要處理）。"""
    mode = mode or _state["mode"]
    if os.name != "nt" or mode != MODE_SESSION:
        return 0
    count = 0
    for path in _font_files(base_dir):
        try:
            if ctypes.windll.gdi32.RemoveFontResourceExW(path, 0, 0):
                count += 1
        except (OSError, AttributeError):
            pass
    if count:
        _broadcast_font_change()
    return count


def ui_family(root=None):
    """回傳實際可用的介面字型家族名稱；都不可用時回傳 None（代表沿用主題預設）。"""
    try:
        import tkinter.font as tkfont
        available = {name.lower(): name for name in tkfont.families(root)}
    except Exception:  # noqa: BLE001 — 沒有 Tk 或查詢失敗時就不指定字型
        return None
    for name in (FAMILY,) + FALLBACK_FAMILIES:
        if name.lower() in available:
            return available[name.lower()]
    return None


_PLATFORM_KEYS = ("macOS", "Windows", "Linux")


def _patch_theme(ctk, family):
    """改 customtkinter 主題裡的預設字型家族。回傳有沒有真的改到東西。

    主題 JSON 裡 CTkFont 是依平台分開的（macOS／Windows／Linux 各一組），但 customtkinter 載入主題時
    會依目前平台「展平」成單一組 {"family", "size", "weight"}。兩種結構都處理，哪一種都不會靜靜地沒作用。
    """
    try:
        font_theme = ctk.ThemeManager.theme["CTkFont"]
    except (AttributeError, KeyError, TypeError):
        return False
    changed = False
    if isinstance(font_theme, dict) and "family" in font_theme:  # 展平後（實際執行時的結構）
        font_theme["family"] = family
        changed = True
    for key in _PLATFORM_KEYS:  # 尚未展平的結構
        entry = font_theme.get(key) if isinstance(font_theme, dict) else None
        if isinstance(entry, dict):
            entry["family"] = family
            changed = True
    return changed


def theme_default_family(ctk):
    """目前 customtkinter 元件預設會用的字型家族；無法判斷（例如還沒有 Tk 視窗）時回傳 None。"""
    try:
        return ctk.CTkFont().cget("family")
    except Exception:  # noqa: BLE001
        return None


def _patch_font_init(ctk, family):
    """後備做法：讓沒有指定家族的 CTkFont 一律使用 family（不依賴主題的內部結構）。"""
    try:
        cls = ctk.CTkFont
        current = cls.__init__
        if hasattr(current, "default_family"):  # 已經包過了，只更新家族
            current.default_family = family
            return True

        def init(self, family=None, *args, **kwargs):
            current(self, family if family is not None else init.default_family, *args, **kwargs)

        init.default_family = family
        cls.__init__ = init
        return True
    except Exception:  # noqa: BLE001
        return False


def apply_to_customtkinter(ctk, family):
    """把 customtkinter 元件的預設字型改成 family。必須在建立任何元件之前、Tk 視窗建立之後呼叫。

    customtkinter 在 Windows 的預設字型是 Roboto（沒有中文字形），中文會逐字退回系統字型，
    所以光是註冊了字型、卻沒有套用，介面看起來還是系統字型。

    回傳套用方式："theme"（改主題）、"init"（後備：改 CTkFont 的預設家族）或 None（沒有套用）。
    改完會實際檢查元件預設字型是不是 family，沒生效就改用後備做法。
    """
    if not family:
        return None
    changed = _patch_theme(ctk, family)
    probe = theme_default_family(ctk)
    if probe == family or (probe is None and changed):  # 驗證通過；或無法驗證但主題確實改到了
        return "theme"
    if _patch_font_init(ctk, family):
        return "init"
    return None


def probe(root, family, chars="A中體"):
    """詢問 Tk：每個字元「實際」用哪個字型繪製，回傳 {字元: 字型家族}。

    Tk 會逐字判斷字型有沒有這個字，沒有就退回別的字型（繁體中文 Windows 常見是新細明體）。
    「元件預設字型是 Noto Sans TC」只代表有要求它，這個結果才是實際畫面用的字型。
    """
    result = {}
    for ch in chars:
        try:
            result[ch] = str(root.tk.call("font", "actual", (family, 13), "-displayof", ".", "-family", "--", ch))
        except Exception as exc:  # noqa: BLE001
            result[ch] = f"?({type(exc).__name__})"
    return result


def probe_mismatches(result, family):
    """實際使用的字型和要求的不同的字元。"""
    return [ch for ch, actual in result.items() if actual.lower() != str(family).lower()]


def describe_probe(result):
    return "  ".join(f"「{ch}」→{actual}" for ch, actual in result.items())
