# modules/webui.py
"""v6.0 網頁介面：用 pywebview 開無邊框視窗，載入 web/index.html，並把 Python 後端開放給網頁呼叫。

網頁 → Python：Api 類別的公開方法（window.pywebview.api.xxx）。
Python → 網頁：Api._emit() 以 evaluate_js 呼叫網頁的 window.__py(事件)。
下載、設定、歷史等實際邏輯全部沿用 downloader / config_manager / history。
"""
import ctypes
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser

from modules import config_manager, downloader, history, i18n, logger, updater, utils
from modules import engine as engine_mod
from modules.i18n import t
from modules.version import APP_NAME, APP_VERSION

LOG = logger.get()
THEMES = ("Light", "System", "Dark")
CLOSE_ANIMATION_SECONDS = 0.5
MINIMIZE_ANIMATION_SECONDS = 0.15  # 最小化淡出／從工作列還原淡入的秒數
# yt-dlp 在每支影片開始下載時會輸出的訊息：播放清單的「第 N 支」，以及每支影片的「Downloading N format(s)」
_VIDEO_START_RE = re.compile(r"^\[(?:download\] Downloading item \d+ of \d+|info\] .+: Downloading \d+ format\(s\))")
HISTORY_VIEW_COUNT = 50
# 升級／安裝命令旁的說明（標題, 提醒）所用的文字代號
_COMMAND_NOTES = {
    "python": ("cmd.python.title", "cmd.python.note"),
    "ffmpeg": ("cmd.ffmpeg.title", "cmd.ffmpeg.note"),
}


def summarize_update(results):
    """把檢查更新的結果整理成 (顯示文字, 是否有檢查失敗, 是否建議前往 Python 下載頁)。"""
    lines, problem, suggest_python = [], False, False
    unknown = t("update.unknown")

    y = results.get("ytdlp", {})
    if y.get("status") == "up_to_date":
        lines.append(t("update.ytdlp.latest", current=y["current"]))
    elif y.get("status") == "updated":
        lines.append(t("update.ytdlp.updated", current=y["current"], latest=y["latest"]))
    else:
        problem = True
        lines.append(t("update.fail", name="yt-dlp", message=y.get("message", unknown)))

    py = results.get("python", {})
    if py.get("status") == "ok":
        notes = []
        if py["patch_update"]:
            notes.append(t("update.python.patch", version=py["series_latest"]))
        if py["new_series"]:
            notes.append(t("update.python.major", version=py["overall"]))
        if py["eol"]:
            notes.append(t("update.python.eol", series=py["series"]))
        if notes:
            suggest_python = True
            lines.append(t("update.python.current", current=py["current"]) + "\n  • " + "\n  • ".join(notes))
        else:
            lines.append(t("update.python.latest", current=py["current"]))
    else:
        problem = True
        lines.append(t("update.fail", name="Python", message=py.get("message", unknown)))

    ff = results.get("ffmpeg")  # 只顯示版本與位置，不提供更新
    if ff is not None:
        if ff.get("status") == "ok":
            where = t("update.ffmpeg.where.program") if ff.get("source") == "program" else t("update.ffmpeg.where.path")
            lines.append(t("update.ffmpeg.ok", version=ff.get("version") or t("update.ffmpeg.noversion"),
                           where=where, path=ff.get("path", "")))
        elif ff.get("status") == "missing":
            problem = True
            lines.append(t("update.ffmpeg.missing"))
        else:
            problem = True
            lines.append(t("update.fail", name="FFmpeg", message=ff.get("message", unknown)))
    return "\n\n".join(lines), problem, suggest_python


def _history_title(item):
    """歷史紀錄要顯示的標題：優先用紀錄的標題，其次檔名，最後才是網址。"""
    title = str(item.get("title") or "").strip()
    if not title:
        files = item.get("files") or []
        if files:
            title = os.path.splitext(os.path.basename(str(files[-1]).replace("\\", "/")))[0]
    return title or str(item.get("url", ""))
PROGRESS_INTERVAL = 0.1  # 進度事件最多每 0.1 秒送一次，避免塞滿網頁

# Windows 視窗邊緣的命中測試代碼，用來讓無邊框視窗可以拖邊緣縮放
_HIT_TEST = {"w": 10, "e": 11, "n": 12, "nw": 13, "ne": 14, "s": 15, "sw": 16, "se": 17}
_WM_NCLBUTTONDOWN = 0x00A1

# 視窗樣式：無邊框視窗預設沒有「最小化框」與系統選單，Win+M（全部最小化）與工作列點擊最小化都會略過它。
# WS_MINIMIZEBOX 要搭配 WS_SYSMENU 才算數。標題列拖曳由本程式自己處理（win_start_drag），不依賴這些樣式。
_GWL_STYLE = -16
_WS_MINIMIZEBOX = 0x00020000
_WS_SYSMENU = 0x00080000
_SWP_FRAMECHANGED_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  # NOSIZE | NOMOVE | NOZORDER | NOACTIVATE | FRAMECHANGED
_SWP_MOVE_FLAGS = 0x0001 | 0x0004 | 0x0010  # NOSIZE | NOZORDER | NOACTIVATE
_VK_LBUTTON = 0x01
_WM_NCLBUTTONDOWN_CAPTION = 2  # HTCAPTION：告訴系統「這是在標題列按下」，由系統接手移動視窗
NATIVE_DRAG = True  # True：先試 Windows 原生移動（順暢），沒生效會自動改成自己逐幀移動；False：一律自己移動
_DRAG_THRESHOLD = 3  # 自己移動時：滑鼠按下後移動超過幾像素才開始拖曳視窗，避免單擊／雙擊時視窗抖動
INTERCEPT_MINIMIZE = True  # True：攔截系統的最小化指令（點工作列圖示等）先播淡出動畫；False：不攔截
_WM_SYSCOMMAND = 0x0112
_SC_MINIMIZE = 0xF020
_GWLP_WNDPROC = -4
_NATIVE_PROBE_PX = 6  # 原生移動：游標移動超過幾像素、視窗卻 _NATIVE_PROBE_SECONDS 內沒動，就判定原生移動沒生效
_NATIVE_PROBE_SECONDS = 0.15

_user32_private = None
_dwm_flush_fn = None


def _wait_next_frame():
    """等到下一次螢幕更新（DwmFlush），讓自己逐幀移動視窗時和螢幕更新對齊；不可用就小睡一下。"""
    global _dwm_flush_fn
    started = time.monotonic()
    try:
        if _dwm_flush_fn is None:
            _dwm_flush_fn = ctypes.WinDLL("dwmapi").DwmFlush
        _dwm_flush_fn()
    except Exception:  # noqa: BLE001
        pass
    elapsed = time.monotonic() - started
    if elapsed < 0.004:  # DwmFlush 立刻返回（例如 DWM 沒開）時避免空轉
        time.sleep(0.004 - elapsed)


def _user32():
    """本程式專用的 user32（獨立載入、獨立設定參數型別），不去動 ctypes.windll.user32 那份共用物件，
    避免影響同行程內 pywebview 等其他程式碼的呼叫。"""
    global _user32_private
    if _user32_private is None:
        from ctypes import wintypes
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowLongW.restype = ctypes.c_long
        u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        u.SetWindowLongW.restype = ctypes.c_long
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        u.SetWindowPos.restype = wintypes.BOOL
        u.IsIconic.argtypes = [wintypes.HWND]
        u.IsIconic.restype = wintypes.BOOL
        u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        u.GetCursorPos.restype = wintypes.BOOL
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.GetWindowRect.restype = wintypes.BOOL
        u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        u.SetWindowLongPtrW.restype = ctypes.c_void_p
        u.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.CallWindowProcW.restype = ctypes.c_ssize_t
        u.ReleaseCapture.argtypes = []
        u.ReleaseCapture.restype = wintypes.BOOL
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.PostMessageW.restype = wintypes.BOOL
        u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        u.GetAsyncKeyState.restype = ctypes.c_short
        _user32_private = u
    return _user32_private

# 會存進 settings.json 的布林／文字設定：網頁欄位名 → (設定檔鍵, 轉換函式)
_SETTING_FIELDS = {
    "fmt": ("last_format", str),
    "quality": ("last_quality", str),
    "cookie_browser": ("cookie_browser", str),
    "number": ("number_playlist_items", bool),
    "archive": ("use_archive", bool),
    "boost": ("concurrent_download", bool),
    "frags": ("concurrent_fragments", downloader.normalize_fragments),
    "subs": ("write_subtitles", bool),
    "embed": ("embed_subtitles", bool),
    "thumb": ("write_thumbnail", bool),
    "meta": ("add_metadata", bool),
    "sub_langs": ("subtitle_langs", lambda v: str(v).strip() or downloader.DEFAULT_SUBTITLE_LANGS),
    "open_file": ("open_file_after_download", bool),
    "open_folder": ("open_folder_after_download", bool),
}


def _dialog_type(webview, name):
    """pywebview 6 以後用 webview.FileDialog.XXX，舊版用 webview.XXX_DIALOG。"""
    file_dialog = getattr(webview, "FileDialog", None)
    return getattr(file_dialog, name) if file_dialog else getattr(webview, f"{name}_DIALOG")


class Api:
    # 注意：pywebview 會把公開屬性都曝露給網頁，所以內部狀態一律用底線開頭，只留要給網頁呼叫的方法公開。
    def __init__(self, base_dir, cfg, config_path):
        self._base_dir = base_dir
        self._cfg = cfg
        self._config_path = config_path
        self._window = None
        self._controller = None
        self._last_progress = 0.0
        self._lock = threading.Lock()
        self._job = {}
        self._maximized = False
        self._last_work_area = 0.0
        self._opacity_token = 0  # 每次開始新的不透明度動畫就加 1，舊動畫看到數字變了就停
        self._watching = False
        self._dragging = False
        self._minimize_pending = False  # 正在播最小化動畫（期間再收到最小化指令就忽略）
        self._allow_minimize = False    # True 時放行最小化指令（我們自己要真的最小化的那一刻）
        self._hooked_hwnd = 0
        self._wndproc_ref = None
        self._prev_wndproc = None
        self._native_ok = NATIVE_DRAG

    # ----------------------------------------------------------- Python → 網頁
    def _emit(self, **event):
        if self._window is None:
            return
        try:
            self._window.evaluate_js(f"window.__py && window.__py({json.dumps(event)})")
        except Exception as exc:  # noqa: BLE001 — 視窗關閉中時送事件會失敗，忽略即可
            LOG.debug("emit failed: %s", exc)

    # ----------------------------------------------------------- 視窗控制（自訂標題列）
    def win_minimize(self):
        """最小化：整個畫面下移、縮小 5% 並淡出，完成後才真的最小化。還原時的淡入由 _watch_minimize 負責。
        標題列按鈕與系統的最小化指令（點工作列圖示）都走這裡，所以動畫一致。"""
        self._minimize_pending = True
        try:
            self._emit(type="winhide", seconds=MINIMIZE_ANIMATION_SECONDS, instant=False)
            self._animate_opacity(1.0, 0.0, MINIMIZE_ANIMATION_SECONDS)
            self._allow_minimize = True  # 放行這一次，不然會被自己的攔截擋下
            self._window.minimize()
        except Exception:  # noqa: BLE001 — 失敗時一定要把視窗變回可見，不然會留下看不到的視窗
            LOG.exception("minimize failed")
            self._set_opacity(1.0)
            self._emit(type="winshow", seconds=0.0)
        finally:
            self._allow_minimize = False
            self._minimize_pending = False

    def win_toggle_maximize(self):
        if self._maximized:
            self._window.restore()
        else:
            self._apply_work_area()  # 無邊框視窗最大化預設會蓋住工作列，先指定只佔「工作區」
            self._window.maximize()

    def _apply_work_area_throttled(self):
        """moved 事件拖曳時會連續觸發，每 0.3 秒最多更新一次。"""
        now = time.monotonic()
        if now - self._last_work_area >= 0.3:
            self._last_work_area = now
            self._apply_work_area()

    def _apply_work_area(self):
        """把視窗「最大化時的範圍」設成它目前所在螢幕的工作區（螢幕扣掉工作列）。

        MaximizedBounds 的位置是相對於該螢幕左上角，所以要用工作區減掉螢幕邊界。
        失敗（非 Windows、拿不到視窗等）就略過，行為退回系統預設。
        """
        if os.name != "nt" or self._window is None:
            return
        try:
            import clr  # noqa: F401
            from System import Action
            from System.Drawing import Rectangle
            from System.Windows.Forms import Screen
            form = self._window.native

            def apply():
                screen = Screen.FromHandle(form.Handle)
                area, bounds = screen.WorkingArea, screen.Bounds
                form.MaximizedBounds = Rectangle(area.X - bounds.X, area.Y - bounds.Y, area.Width, area.Height)

            form.Invoke(Action(apply))
        except Exception as exc:  # noqa: BLE001
            LOG.debug("apply work area failed: %s", exc)

    def _hwnd(self):
        handle = getattr(getattr(self._window, "native", None), "Handle", None)
        return int(handle.ToInt64()) if handle is not None else 0

    def _apply_min_style(self, hwnd):
        """確保視窗有「最小化框」與系統選單樣式（沒有標題列樣式，畫面不變）。樣式被改回去時會補上。"""
        user32 = _user32()
        style = user32.GetWindowLongW(hwnd, _GWL_STYLE) & 0xFFFFFFFF
        wanted = style | _WS_MINIMIZEBOX | _WS_SYSMENU
        if wanted == style:
            return False
        user32.SetWindowLongW(hwnd, _GWL_STYLE, ctypes.c_long(wanted).value)  # 要轉成有號數（WS_POPUP 是第 31 位元）
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, _SWP_FRAMECHANGED_FLAGS)  # 讓系統重新讀取樣式
        return True

    def _enable_minimize_box(self):
        """視窗顯示後補上最小化框樣式，讓 Win+M 與工作列點擊能最小化這個無邊框視窗。失敗就略過。"""
        if os.name != "nt" or self._window is None:
            return
        try:
            import clr  # noqa: F401
            from System import Action
            form = self._window.native
            form.Invoke(Action(lambda: self._apply_min_style(self._hwnd())))
        except Exception as exc:  # noqa: BLE001
            LOG.warning("enable minimize box failed: %s", exc)

    # ----------------------------------------------------------- 攔截系統的最小化指令
    def _install_minimize_hook(self):
        """子類別化視窗程序：收到 WM_SYSCOMMAND／SC_MINIMIZE（點工作列圖示等）時，先播淡出動畫再最小化。
        其餘訊息原樣交還給原本的視窗程序；任何錯誤都放行，不影響視窗。"""
        if not INTERCEPT_MINIMIZE or os.name != "nt" or self._window is None:
            return
        try:
            import clr  # noqa: F401
            from ctypes import wintypes
            from System import Action
            form = self._window.native
            user32 = _user32()

            def install():
                hwnd = self._hwnd()
                if not hwnd or hwnd == self._hooked_hwnd:
                    return
                proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                               wintypes.WPARAM, wintypes.LPARAM)

                def proc(h, msg, wparam, lparam):
                    try:
                        if msg == _WM_SYSCOMMAND and (wparam & 0xFFF0) == _SC_MINIMIZE and not self._allow_minimize:
                            if self._minimize_pending:
                                return 0  # 動畫進行中，忽略重複的指令
                            self._minimize_pending = True
                            threading.Thread(target=self.win_minimize, daemon=True).start()
                            return 0
                    except Exception:  # noqa: BLE001 — 出錯就放行
                        pass
                    return user32.CallWindowProcW(self._prev_wndproc, h, msg, wparam, lparam)

                callback = proc_type(proc)
                previous = user32.SetWindowLongPtrW(hwnd, _GWLP_WNDPROC, ctypes.cast(callback, ctypes.c_void_p))
                if previous:
                    self._wndproc_ref, self._prev_wndproc, self._hooked_hwnd = callback, previous, hwnd

            form.Invoke(Action(install))
        except Exception as exc:  # noqa: BLE001
            LOG.warning("install minimize hook failed: %s", exc)

    # ----------------------------------------------------------- 標題列拖曳（自己處理，不用 pywebview 的拖曳區）
    def win_start_drag(self):
        """網頁在標題列按下滑鼠時呼叫；按住期間視窗跟著游標移動，放開就結束。"""
        if os.name != "nt" or self._window is None or self._maximized or self._dragging:
            return False
        hwnd = self._hwnd()
        if not hwnd:
            return False
        self._dragging = True
        threading.Thread(target=self._drag_session, args=(hwnd,), daemon=True).start()
        return True

    def _strip_min_style(self, hwnd):
        """暫時拿掉最小化框／系統選單樣式（我推測有這兩個樣式時，系統不接手「標題列按下」的移動）。"""
        user32 = _user32()
        style = user32.GetWindowLongW(hwnd, _GWL_STYLE) & 0xFFFFFFFF
        stripped = style & ~(_WS_MINIMIZEBOX | _WS_SYSMENU)
        if stripped != style:
            user32.SetWindowLongW(hwnd, _GWL_STYLE, ctypes.c_long(stripped).value)

    def _drag_session(self, hwnd):
        """一次拖曳：先試 Windows 原生移動；沒生效就自動改成自己逐幀移動，並記住往後都用自己的。"""
        from ctypes import wintypes
        user32 = _user32()
        point, rect = wintypes.POINT(), wintypes.RECT()
        mode, moved = "native" if self._native_ok else "manual", False
        try:
            user32.GetCursorPos(ctypes.byref(point))
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            start_x, start_y, win_x, win_y = point.x, point.y, rect.left, rect.top
            if mode == "native":
                self._strip_min_style(hwnd)
                user32.ReleaseCapture()
                user32.PostMessageW(hwnd, _WM_NCLBUTTONDOWN, _WM_NCLBUTTONDOWN_CAPTION, 0)
            probe_since = None
            while user32.GetAsyncKeyState(_VK_LBUTTON) & 0x8000:
                user32.GetCursorPos(ctypes.byref(point))
                dx, dy = point.x - start_x, point.y - start_y
                if mode == "native":
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    if (rect.left, rect.top) != (win_x, win_y):
                        moved = True  # 視窗位置變了：原生移動有生效，之後只要等放開
                    elif not moved and max(abs(dx), abs(dy)) >= _NATIVE_PROBE_PX:
                        probe_since = probe_since or time.monotonic()
                        if time.monotonic() - probe_since > _NATIVE_PROBE_SECONDS:
                            LOG.warning("native window drag had no effect; using manual drag from now on")
                            self._native_ok = False
                            mode, moved = "manual", False
                    time.sleep(0.01)
                    continue
                if not moved and abs(dx) < _DRAG_THRESHOLD and abs(dy) < _DRAG_THRESHOLD:
                    time.sleep(0.008)
                    continue
                moved = True
                user32.SetWindowPos(hwnd, None, win_x + dx, win_y + dy, 0, 0, _SWP_MOVE_FLAGS)
                _wait_next_frame()
        except Exception as exc:  # noqa: BLE001
            LOG.warning("drag failed: %s", exc)
        finally:
            try:
                self._apply_min_style(hwnd)  # 拖曳結束，把最小化框／系統選單樣式補回去
            except Exception as exc:  # noqa: BLE001
                LOG.warning("restore window style after drag failed: %s", exc)
            self._dragging = False

    # ----------------------------------------------------------- 最小化／還原動畫
    def _set_opacity(self, value):
        """設定視窗不透明度（在視窗所屬的執行緒上）。失敗回傳 False。"""
        try:
            import clr  # noqa: F401
            from System import Action
            form = self._window.native
            form.Invoke(Action(lambda: setattr(form, "Opacity", value)))
            return True
        except Exception as exc:  # noqa: BLE001
            LOG.debug("set opacity failed: %s", exc)
            return False

    def _animate_opacity(self, start, end, seconds):
        """把視窗不透明度從 start 漸變到 end。中途有新的動畫開始就停止（以 token 判斷）。"""
        self._opacity_token += 1
        token = self._opacity_token
        started = time.monotonic()
        while True:
            progress = (time.monotonic() - started) / seconds
            if progress >= 1 or token != self._opacity_token:
                break
            if not self._set_opacity(start + (end - start) * progress):
                time.sleep(max(0.0, seconds - (time.monotonic() - started)))  # 不能調透明度就只等時間到
                break
            time.sleep(0.016)
        if token == self._opacity_token:
            self._set_opacity(end)

    def _start_minimize_watcher(self):
        """監看視窗是否被最小化（按鈕、Win+M、工作列都算），最小化時藏起來，還原時淡入。

        用 IsIconic 輪詢而不依賴 pywebview 的 minimized／restored 事件，行為在各版本都一致；
        順便定期確認最小化框樣式還在。
        """
        if os.name != "nt" or self._window is None or self._watching:
            return
        self._watching = True

        def watch():
            user32 = _user32()
            hidden = False
            failures = 0
            tick = 0
            while True:
                time.sleep(0.1)
                try:
                    hwnd = self._hwnd()
                    if not hwnd:
                        continue
                    iconic = bool(user32.IsIconic(hwnd))
                    failures = 0
                    tick += 1
                    if tick % 5 == 0 and not iconic and not self._dragging:  # 每 0.5 秒確認一次樣式有沒有被 WinForms 改回去
                        self._apply_min_style(hwnd)
                        if hwnd != self._hooked_hwnd:  # 視窗控制代碼重建過，重新掛上攔截
                            self._install_minimize_hook()
                except Exception:  # noqa: BLE001 — 視窗已關閉
                    failures += 1
                    if failures >= 5:
                        return
                    continue
                if iconic and not hidden:
                    hidden = True
                    self._opacity_token += 1  # 取消進行中的動畫
                    self._set_opacity(0.0)   # 最小化期間保持透明，還原時才不會先閃出完整畫面
                    self._emit(type="winhide", seconds=0.0, instant=True)
                elif not iconic and hidden:
                    hidden = False
                    self._emit(type="winshow", seconds=MINIMIZE_ANIMATION_SECONDS)
                    self._animate_opacity(0.0, 1.0, MINIMIZE_ANIMATION_SECONDS)

        threading.Thread(target=watch, daemon=True).start()

    def win_close(self, animate=True):
        """關閉視窗。網頁那邊同時做縮小 5% 的動畫，這裡讓整個視窗在 0.5 秒內淡出。"""
        downloader.terminate_all_downloads()
        if animate:
            self._fade_out(CLOSE_ANIMATION_SECONDS)
        self._window.destroy()

    def _fade_out(self, seconds):
        started = time.monotonic()
        try:
            import clr  # noqa: F401 — pythonnet，pywebview 在 Windows 上本來就會載入
            from System import Action
            form = self._window.native

            def set_opacity(value):
                form.Invoke(Action(lambda: setattr(form, "Opacity", value)))  # 要在視窗所屬的執行緒上改

            while True:
                progress = (time.monotonic() - started) / seconds
                if progress >= 1:
                    break
                set_opacity(max(0.0, 1 - progress))
                time.sleep(0.016)
            set_opacity(0.0)
        except Exception as exc:  # noqa: BLE001 — 淡出失敗就只等時間到，網頁那邊的縮小動畫仍會播
            LOG.debug("fade out unavailable: %s", exc)
            time.sleep(max(0.0, seconds - (time.monotonic() - started)))

    def win_start_resize(self, edge):
        """從視窗邊緣開始縮放：交給 Windows 原生的縮放流程，所以 DPI 縮放與邊緣吸附都正常。"""
        code = _HIT_TEST.get(edge)
        if code is None or os.name != "nt" or self._maximized:
            return False
        try:
            user32 = ctypes.windll.user32
            handle = getattr(getattr(self._window, "native", None), "Handle", None)
            hwnd = int(handle.ToInt64()) if handle is not None else user32.GetForegroundWindow()
            user32.ReleaseCapture()
            user32.PostMessageW(hwnd, _WM_NCLBUTTONDOWN, code, 0)
            return True
        except Exception as exc:  # noqa: BLE001
            LOG.warning("start_resize failed: %s", exc)
            return False

    def _set_maximized(self, value):
        self._maximized = value
        self._emit(type="win", maximized=value)

    # ----------------------------------------------------------- 設定
    def _stored_path(self, path):
        """在程式資料夾內就存相對路徑，搬動整個資料夾時設定仍有效。"""
        try:
            rel = os.path.relpath(path, self._base_dir)
            if not rel.startswith(".."):
                return rel
        except ValueError:  # 不同磁碟機
            pass
        return path

    def _resolve_dir(self, stored):
        stored = (stored or "Download").strip()
        return stored if os.path.isabs(stored) else os.path.normpath(os.path.join(self._base_dir, stored))

    def get_state(self):
        cfg = self._cfg
        appearance = cfg.get("appearance")
        state = {
            "version": APP_VERSION,
            "appearance": appearance if appearance in THEMES else "System",
            "language": i18n.normalize_setting(cfg.get("language")),
            "lang": i18n.current(),
            "strings": i18n.bundle(),
            "outdir": self._resolve_dir(cfg.get("download_path")),
            "default_dir": os.path.join(self._base_dir, "Download"),
        }
        defaults = config_manager.DEFAULT_CONFIG
        for field, (key, convert) in _SETTING_FIELDS.items():
            state[field] = convert(cfg.get(key, defaults.get(key)))
        return state

    def set_language(self, setting):
        """切換介面語言：setting 是 'auto' 或語言代號。存進設定檔、讓 Python 端訊息改用新語言，並回傳整包文字給網頁。"""
        setting = i18n.normalize_setting(setting)
        self._cfg["language"] = setting
        i18n.set_language(i18n.resolve(setting))
        try:
            config_manager.save(self._config_path, self._cfg)
        except OSError as exc:
            LOG.warning("Failed saving config: %s", exc)
        return {"lang": i18n.current(), "strings": i18n.bundle()}

    def save_settings(self, data):
        for key, value in (data or {}).items():
            if key == "appearance":
                self._cfg["appearance"] = value if value in THEMES else "System"
            elif key == "outdir":
                self._cfg["download_path"] = self._stored_path(str(value))
            elif key in _SETTING_FIELDS:
                cfg_key, convert = _SETTING_FIELDS[key]
                self._cfg[cfg_key] = convert(value)
        try:
            config_manager.save(self._config_path, self._cfg)
        except OSError as exc:
            LOG.warning("Failed saving config: %s", exc)
        return True

    # ----------------------------------------------------------- 檔案對話框
    def choose_folder(self):
        import webview
        result = self._window.create_file_dialog(
            _dialog_type(webview, "FOLDER"), directory=self._resolve_dir(self._cfg.get("download_path")))
        return result[0] if result else None

    def pick_cookie_file(self):
        import webview
        result = self._window.create_file_dialog(
            _dialog_type(webview, "OPEN"), file_types=(t("dialog.cookie"), t("dialog.all")))
        return result[0] if result else None

    def pick_batch_file(self):
        """選批次清單並先讀一次，確認可用；網址欄位會填成 file:<路徑>，開始下載時才真正展開。"""
        import webview
        result = self._window.create_file_dialog(
            _dialog_type(webview, "OPEN"), file_types=(t("dialog.text"), t("dialog.all")))
        if not result:
            return {"ok": False, "cancelled": True}
        return self._check_batch(result[0])

    def load_example(self):
        path = os.path.join(self._base_dir, "example", "example.txt")
        if not os.path.exists(path):
            return {"ok": False, "missing": True,
                    "error": t("err.example_missing")}
        return self._check_batch(path)

    @staticmethod
    def _check_batch(path):
        try:
            urls = downloader.read_batch_urls(path)
        except OSError as exc:
            return {"ok": False, "error": t("err.list_read", error=exc)}
        if not urls:
            return {"ok": False, "error": t("err.list_empty")}
        return {"ok": True, "path": path, "count": len(urls)}

    # ----------------------------------------------------------- 下載
    def prepare_download(self, payload):
        """開始前先分析網址：含播放清單時，回傳需要詢問使用者的內容。"""
        outdir = self._resolve_dir(payload.get("outdir"))
        target = (payload.get("url") or "").strip()
        try:
            urls = downloader.expand_target(target)
        except OSError as exc:
            return {"ok": False, "error": t("err.list_read", error=exc)}
        if not target:
            return {"ok": False, "error": t("err.no_url")}
        if not urls:
            return {"ok": False, "error": t("err.list_empty")}
        kinds = [downloader.classify_playlist_url(url) for url in urls]
        if not target.startswith("file:"):
            if kinds[0] == "none":
                return {"ok": True, "ask": None}
            return {"ok": True, "ask": "single", "kind": kinds[0],
                    "default_name": downloader.next_playlist_folder(outdir)}
        playlist_kinds = [k for k in kinds if k != "none"]
        if not playlist_kinds:
            return {"ok": True, "ask": None}
        return {"ok": True, "ask": "batch", "total": len(playlist_kinds), "pure": kinds.count("playlist")}

    def _plan_items(self, target, urls, outdir, decision):
        kinds = [downloader.classify_playlist_url(url) for url in urls]
        mode = (decision or {}).get("mode", "single")
        if not target.startswith("file:"):  # 單一網址
            url, kind = urls[0], kinds[0]
            if kind == "none" or mode != "playlist":
                return [downloader.DownloadItem(url)]
            name = downloader.sanitize_folder_name((decision or {}).get("name", "")) \
                or downloader.next_playlist_folder(outdir)
            return [downloader.DownloadItem(url, playlist=True, folder=name)]
        items, reserved = [], set()  # 批次清單：資料夾到開始下載才建立，先預留名稱避免同一批次撞名
        for url, kind in zip(urls, kinds):
            if kind == "none":
                items.append(downloader.DownloadItem(url))
            elif mode == "playlist":
                name = downloader.next_playlist_folder(outdir, reserved)
                reserved.add(name)
                items.append(downloader.DownloadItem(url, playlist=True, folder=name))
            elif kind == "mixed":  # 只下載單一影片：影片加清單 → 只取該影片；純清單 → 略過
                items.append(downloader.DownloadItem(url))
        return items

    def start_download(self, payload, decision=None):
        with self._lock:
            if self._controller is not None:
                return {"ok": False, "error": t("err.busy")}
            outdir = self._resolve_dir(payload.get("outdir"))
            target = (payload.get("url") or "").strip()
            try:
                urls = downloader.expand_target(target)
            except OSError as exc:
                return {"ok": False, "error": t("err.batch_read", error=exc)}
            if not urls:
                return {"ok": False, "error": t("err.no_url")}
            items = self._plan_items(target, urls, outdir, decision)
            if not items:
                return {"ok": False, "error": t("err.only_playlists_skipped")}
            cfg = self._cfg
            opts = downloader.DownloadOptions(
                fmt=(payload.get("fmt") or "mp4").strip(),
                quality=(payload.get("quality") or "預設").strip(),
                cookie_browser=payload.get("cookie_browser", "無"),
                cookie_file=(payload.get("cookie_file") or "").strip(),  # 路徑刻意不存進設定檔
                write_subtitles=bool(payload.get("subs")),
                embed_subtitles=bool(payload.get("embed")),
                subtitle_langs=(payload.get("sub_langs") or "").strip() or downloader.DEFAULT_SUBTITLE_LANGS,
                write_thumbnail=bool(payload.get("thumb")),
                add_metadata=bool(payload.get("meta")),
                number_playlist_items=bool(payload.get("number")),
                use_archive=bool(payload.get("archive")),
                concurrent_download=bool(payload.get("boost")),
                concurrent_fragments=downloader.normalize_fragments(payload.get("frags")),
                filename_template=cfg.get("filename_template") or downloader.DEFAULT_FILENAME_TEMPLATE,
            )
            settings = {k: payload.get(k) for k in _SETTING_FIELDS if k in payload}
            self.save_settings({**settings, "outdir": outdir})
            playlist_items = [item for item in items if item.playlist]
            open_dir = playlist_items[0].target_dir(outdir) if len(items) == 1 and playlist_items else outdir
            self._job = {"outdir": outdir, "open_dir": open_dir, "fmt": opts.fmt, "quality": opts.quality,
                         "open_file": bool(payload.get("open_file")),
                         "open_folder": bool(payload.get("open_folder"))}
            self._controller = downloader.run_download(
                self._base_dir, items, outdir, opts,
                progress_callback=self._on_progress, log_callback=self._on_log,
                item_started=self._on_item_started, item_finished=self._on_item_finished,
                finished_callback=self._on_finished)
        self._emit(type="state", busy=True)
        self._emit(type="log", text=t("log.start", target=target, fmt=opts.fmt, quality=opts.quality, outdir=outdir),
                   cls="hl")
        return {"ok": True}

    def cancel_download(self):
        controller = self._controller
        if controller is not None:
            self._emit(type="log", text=t("log.cancelling"))
            controller.cancel()
        return True

    def _on_progress(self, fraction, text):
        now = time.monotonic()
        if now - self._last_progress < PROGRESS_INTERVAL and (fraction is None or fraction < 1):
            return
        self._last_progress = now
        parts = [p.strip() for p in (text or "").split("|")]
        speed = parts[1] if len(parts) >= 3 else ""
        eta = parts[2].replace("ETA", "").strip() if len(parts) >= 3 else ""
        frag_count = parts[3].replace("FRAG", "").strip() if len(parts) >= 4 else ""
        fragmented = frag_count not in ("", "NA", "N/A", "None")  # 有分片數才表示這是分片下載，-N 才有作用
        self._emit(type="progress", pct=None if fraction is None else round(fraction * 100, 1),
                   speed=speed, eta=eta, frag=fragmented)

    def _on_log(self, text):
        if _VIDEO_START_RE.match(text):
            self._emit(type="video_start")  # 通知網頁：新的一支影片開始，下一筆進度要重新判斷能不能分片
        self._emit(type="log", text=text)

    def _on_item_started(self, index, total, item):
        self._emit(type="video_start")  # 備援：有些網站不會輸出上面那種訊息
        if total > 1:
            self._emit(type="log", text=t("log.item_start", index=index, total=total, url=item.url))
        if item.playlist:
            self._emit(type="log", text=t("log.item_playlist", folder=item.folder))

    def _on_item_finished(self, index, total, item, success, message, files):
        if total > 1:
            status = t("hist.ok") if success else t("hist.fail")
            self._emit(type="log", text=t("log.item_result", index=index, total=total, status=status, message=message),
                       cls="ok" if success else "")
        if message == t("state.cancelled"):
            return  # 使用者主動取消的項目不計入歷史
        job = self._job
        try:
            history.add(self._base_dir, item.url, job.get("outdir", ""), job.get("fmt", ""),
                        job.get("quality", ""), success, message, files,
                        title=downloader.derive_title(item, files))
        except Exception as exc:  # noqa: BLE001 — 歷史紀錄失敗不該影響下載
            LOG.warning("Failed writing history: %s", exc)

    def _on_finished(self, result):
        job = self._job
        self._controller = None
        failures = [{"url": url, "message": msg} for url, msg in result.failures[:3]]
        self._emit(type="state", busy=False)
        self._emit(type="done", ok=result.success, cancelled=result.cancelled, message=result.message,
                   code=result.code, failures=failures, more=max(0, len(result.failures) - 3),
                   ffmpeg_url=downloader.FFMPEG_DOWNLOAD_URL if result.code == "ffmpeg" else "")
        if result.succeeded:
            if job.get("open_folder"):
                utils.open_path(job.get("open_dir") or job.get("outdir", ""))
            if job.get("open_file"):
                self._open_result_file(result.files)

    def _open_result_file(self, files):
        for path in reversed(files):
            if os.path.isfile(path):
                utils.open_path(path)
                return
        outdir = self._job.get("outdir", "")  # 後備：輸出資料夾中最新的媒體檔
        try:
            skip = (".part", ".ytdl", ".vtt", ".srt", ".jpg", ".png", ".webp")
            candidates = [os.path.join(outdir, n) for n in os.listdir(outdir)]
            candidates = [f for f in candidates if os.path.isfile(f) and not f.endswith(skip)]
            if candidates:
                utils.open_path(max(candidates, key=os.path.getmtime))
        except OSError as exc:
            LOG.warning("Unable to open latest file in %s: %s", outdir, exc)

    def open_ffmpeg_page(self):
        webbrowser.open(downloader.FFMPEG_DOWNLOAD_URL)
        return True

    # ----------------------------------------------------------- 影片資訊
    def fetch_info(self, url, cookie_browser="無", cookie_file=""):
        url = (url or "").strip()
        if not url:
            return {"ok": False, "error": t("err.no_url")}
        if url.startswith("file:"):
            return {"ok": False, "error": t("err.batch_info")}

        def work():
            try:
                opts = downloader.DownloadOptions(cookie_browser=cookie_browser, cookie_file=cookie_file or "")
                info = downloader.fetch_info(self._base_dir, url, opts)
                length = ""
                if info["duration"]:
                    minutes, seconds = divmod(int(info["duration"]), 60)
                    hours, minutes = divmod(minutes, 60)
                    length = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
                lines = [t("info.title", title=info["title"])]
                if info["uploader"]:
                    lines.append(t("info.uploader", uploader=info["uploader"]))
                if info["is_playlist"]:
                    lines.append(t("info.playlist", count=info["entry_count"]))
                if length:
                    lines.append(t("info.length", length=length))
                if info["heights"]:
                    lines.append(t("info.heights", list=t("info.sep").join(f"{h}P" for h in info["heights"])))
                for line in lines:
                    self._emit(type="log", text=line, cls="hl")
            except RuntimeError as exc:
                self._emit(type="log", text=str(exc))
            finally:
                self._emit(type="info_done")

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    # ----------------------------------------------------------- 歷史、說明、診斷、檢查更新
    def get_history(self):
        rows = []
        for item in history.load(self._base_dir)[-HISTORY_VIEW_COUNT:][::-1]:
            title = _history_title(item)
            rows.append({
                "time": str(item.get("time", "")).replace("T", "_"),
                "ok": bool(item.get("success")),
                "fmt": str(item.get("format") or "").upper() or "-",
                "quality": str(item.get("quality") or "-"),
                "title": title if len(title) <= 80 else title[:79] + "…",
                "url": str(item.get("url", "")),
            })
        return rows

    def clear_history(self):
        history.clear(self._base_dir)
        return True

    def open_target(self, target):
        """歷史紀錄的標題連結：網址用瀏覽器開，file: 開本機檔案；其他一律不處理。"""
        target = str(target or "").strip()
        if target.lower().startswith(("http://", "https://")):
            webbrowser.open(target)
            return True
        if target.lower().startswith("file:"):
            return bool(utils.open_path(target[5:]))
        return False

    def get_help(self):
        return {"text": t("help.text"), "credit": t("credit")}

    def run_diagnostics(self):
        def work():
            try:
                self._emit(type="diag", text=self._diagnostics_text())
            except Exception as exc:  # noqa: BLE001 — 診斷本身出錯也要讓按鈕恢復
                LOG.exception("Diagnostics failed")
                self._emit(type="diag", text=t("diag.fail", error=exc))

        threading.Thread(target=work, daemon=True).start()
        return True

    def _diagnostics_text(self):
        eng = engine_mod.resolve_engine(self._base_dir)
        version = engine_mod.get_version(eng) if eng else None
        js = engine_mod.find_js_runtimes()
        try:
            webview_version = importlib.metadata.version("pywebview")
        except importlib.metadata.PackageNotFoundError:
            webview_version = t("diag.unknown")
        return t("diag.report",
                 platform=utils.describe_platform(),
                 python=sys.version.split()[0],
                 ytdlp_version=version or t("diag.not_installed"),
                 ytdlp_source=engine_mod.describe(eng) if eng else t("diag.not_found"),
                 ffmpeg=downloader.find_ffmpeg(self._base_dir) or t("diag.not_found"),
                 webview=webview_version,
                 js=", ".join(js) if js else t("diag.js_none"),
                 outdir=self._resolve_dir(self._cfg.get("download_path")),
                 config=self._config_path)

    def check_update(self):
        if self._controller is not None:
            return {"ok": False, "error": t("err.update_busy")}

        def work():
            try:
                eng = engine_mod.resolve_engine(self._base_dir)
                results = updater.check_all(eng, log=lambda text: self._emit(type="log", text=text),
                                            base_dir=self._base_dir)
            except Exception as exc:  # noqa: BLE001
                LOG.exception("Update check failed")
                failed = {"status": "error", "message": str(exc)}
                results = {"ytdlp": failed, "python": failed, "ffmpeg": failed}
            text, problem, suggest_python = summarize_update(results)
            ffmpeg_missing = (results.get("ffmpeg") or {}).get("status") == "missing"
            command = None  # (按鈕文字, 命令, 種類)
            if suggest_python:
                winget = (results.get("python") or {}).get("winget_command")  # 只有 Windows 且 winget 可用才有
                if winget:
                    label = "update.copy_upgrade" if "upgrade" in winget[1] else "update.copy_install"
                    command = {"label": t(label), "text": winget[1], "kind": "python"}
            if command:
                title_key, note_key = _COMMAND_NOTES[command["kind"]]
                command["title"], command["note"] = t(title_key), t(note_key)
            self._emit(type="update_done", text=text, problem=problem,
                       python_url=updater.PYTHON_DOWNLOADS_URL if suggest_python else "",
                       ffmpeg_url=downloader.FFMPEG_DOWNLOAD_URL if ffmpeg_missing else "",
                       command=command)

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def open_python_page(self):
        webbrowser.open(updater.PYTHON_DOWNLOADS_URL)
        return True

    # ----------------------------------------------------------- 視窗事件
    def on_closing(self):
        downloader.terminate_all_downloads()


def _ensure_ytdlp(base_dir):
    """找不到 yt-dlp 時自動 pip 安裝；失敗就算了，下載時會顯示中文錯誤訊息。"""
    if engine_mod.resolve_engine(base_dir) is not None:
        return
    python = utils.get_console_python()
    if not python:
        return
    LOG.info("yt-dlp not found, installing")
    try:
        subprocess.run([python, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                       capture_output=True, timeout=300, **utils.hidden_subprocess_kwargs())
    except (OSError, subprocess.SubprocessError) as exc:
        LOG.warning("pip install yt-dlp failed: %s", exc)


def main(base_dir):
    import webview

    os.makedirs(os.path.join(base_dir, "Download"), exist_ok=True)
    logger.setup_logging(os.path.join(base_dir, "logs"))
    config_path = os.path.join(base_dir, "config", "settings.json")
    cfg = config_manager.load_or_init(config_path)
    config_manager.ensure_example(base_dir, config_path, cfg)
    _ensure_ytdlp(base_dir)

    i18n.init(base_dir)
    i18n.set_language(i18n.resolve(cfg.get("language")))
    api = Api(base_dir, cfg, config_path)
    window = webview.create_window(
        f"{APP_NAME} v{APP_VERSION}", os.path.join(base_dir, "web", "index.html"),
        js_api=api, width=1120, height=800, min_size=(900, 680),
        frameless=True, easy_drag=False, resizable=True)  # 標題列由網頁自己畫；拖曳移動由 win_start_drag 處理
    api._window = window
    window.events.closing += api.on_closing
    for name in ("shown", "moved"):  # 視窗顯示與移到別的螢幕（或工作列位置變了）時，更新最大化範圍
        event = getattr(window.events, name, None)
        if event is not None:
            event += (lambda *_: api._apply_work_area_throttled())
    shown = getattr(window.events, "shown", None)
    if shown is not None:  # 視窗建立後補上最小化框，Win+M 才會處理這個無邊框視窗
        shown += (lambda *_: (api._enable_minimize_box(), api._install_minimize_hook(), api._start_minimize_watcher()))
    for name, value in (("maximized", True), ("restored", False)):
        event = getattr(window.events, name, None)
        if event is not None:
            event += (lambda *_, v=value: api._set_maximized(v))
    webview.start()
    downloader.terminate_all_downloads()
