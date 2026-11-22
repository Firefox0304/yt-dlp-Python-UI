# modules/winshell.py
"""自訂標題列所需的 Windows 視窗控制（最小化／最大化／拖曳／邊緣縮放／淡出動畫）。

由 webui.Api 以 mixin 方式繼承，所以網頁呼叫的 win_* 方法名稱不變。
Mixin 依賴 Api 提供：self._window、self._emit()。
"""
import ctypes
import os
import threading
import time

from modules import downloader, logger

LOG = logger.get()

CLOSE_ANIMATION_SECONDS = 0.5
MINIMIZE_ANIMATION_SECONDS = 0.15  # 最小化淡出／從工作列還原淡入的秒數

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


class WindowMixin:
    """視窗控制：公開方法是給網頁呼叫的 win_*，其餘都是內部用。"""

    def _init_window_state(self):
        """由 Api.__init__ 呼叫：初始化視窗控制用的內部狀態（一律底線開頭，pywebview 才不會曝露給網頁）。"""
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
