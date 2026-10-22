# modules/splash.py
"""啟動畫面（只用標準 tkinter，所以在主程式與 customtkinter 載入之前就能顯示）。

進度數值由背景執行緒用 set_progress() 更新（只改數字與文字，不碰 Tk），
真正的繪製只在主執行緒的 refresh() 進行，因此是執行緒安全的。
畫面永遠不會讓程式啟動失敗：任何 Tk 錯誤都會被吞掉並停用畫面。
"""
import time
import tkinter as tk

from modules import fonts

BG = "#1e1f22"
FG = "#e8e8e8"
SUB = "#9aa0a6"
ACCENT = "#3b8ed0"
TRACK = "#34363b"
FALLBACK_FONT = "Microsoft JhengHei UI"


class NullSplash:
    """沒有顯示環境（或建立失敗）時的替身，介面與 Splash 相同。"""
    root = None

    def set_progress(self, value, text=None): pass
    def refresh(self): pass
    def hide(self): pass
    def show(self): pass
    def adopt(self, root): pass
    def close(self): pass


class Splash:
    def __init__(self, title, version):
        self.target = 0.0
        self.shown = 0.0
        self.text = "正在啟動"
        self._closed = False

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass

        FONT = fonts.ui_family(self.root) or FALLBACK_FONT  # 隨附的 Noto Sans TC（若已註冊）
        s = self._dpi_scale()
        self._s = s
        width, height = int(460 * s), int(180 * s)
        x = (self.root.winfo_screenwidth() - width) // 2
        y = (self.root.winfo_screenheight() - height) // 2
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.configure(bg=ACCENT)  # 外框 1px 強調色

        inner = tk.Frame(self.root, bg=BG)
        inner.place(x=1, y=1, relwidth=1, relheight=1, width=-2, height=-2)
        pad = int(22 * s)

        tk.Label(inner, text=title, bg=BG, fg=FG, font=(FONT, 15, "bold"), anchor="w").pack(
            fill="x", padx=pad, pady=(pad, 0))
        tk.Label(inner, text=f"v{version}", bg=BG, fg=SUB, font=(FONT, 9), anchor="w").pack(
            fill="x", padx=pad)

        row = tk.Frame(inner, bg=BG)
        row.pack(fill="x", padx=pad, pady=(int(18 * s), int(6 * s)))
        self._status = tk.Label(row, text=self.text, bg=BG, fg=FG, font=(FONT, 10), anchor="w")
        self._status.pack(side="left")
        self._percent = tk.Label(row, text="0%", bg=BG, fg=SUB, font=(FONT, 10), anchor="e")
        self._percent.pack(side="right")

        self._bar = tk.Canvas(inner, height=int(10 * s), bg=TRACK, highlightthickness=0, bd=0)
        self._bar.pack(fill="x", padx=pad)

        tk.Label(inner, text="正在啟動，請稍候，請勿重複開啟程式。", bg=BG, fg=SUB,
                 font=(FONT, 9), anchor="w").pack(fill="x", padx=pad, pady=(int(12 * s), 0))

        self.root.deiconify()
        self.root.update()

    def _dpi_scale(self):
        try:
            return max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            return 1.0

    # ---- 任何執行緒都可呼叫（只改數字，不碰 Tk） ----
    def set_progress(self, value, text=None):
        self.target = max(self.target, min(float(value), 100.0))
        if text is not None:
            self.text = text

    # ---- 只在主執行緒呼叫 ----
    def refresh(self):
        if self._closed:
            return
        try:
            self._tick()
            self.root.update()
        except tk.TclError:
            self._closed = True

    def _tick(self):
        if self.shown < self.target:  # 緩動追上目標
            self.shown = min(self.target, self.shown + max((self.target - self.shown) * 0.18, 0.2))
        elif self.shown < min(self.target + 5.0, 97.0):  # 到達目標後緩慢前進，讓人知道沒有當機
            self.shown += 0.04
        dots = "." * (int(time.monotonic() * 2.5) % 4)
        self._status.configure(text=f"{self.text}{dots}")
        self._percent.configure(text=f"{int(self.shown)}%")
        width = self._bar.winfo_width()
        self._bar.delete("fill")
        self._bar.create_rectangle(0, 0, width * self.shown / 100.0, int(10 * self._s),
                                   fill=ACCENT, width=0, tags="fill")

    def hide(self):
        """顯示對話框前暫時隱藏（置頂的啟動畫面會把對話框蓋住）。"""
        try:
            self.root.withdraw()
        except tk.TclError:
            pass

    def show(self):
        try:
            self.root.deiconify()
            self.root.update()
        except tk.TclError:
            pass

    def adopt(self, root):
        """主視窗建立後呼叫：讓 tkinter 的「預設根視窗」改指向主視窗。

        啟動畫面是第一個建立的 Tk，因此預設根視窗會是它；它關閉之後，不指定 master 的
        變數、對話框等才不會失效。這裡用到 tkinter 的私有變數 _default_root。
        """
        try:
            tk._default_root = root
        except Exception:  # noqa: BLE001
            pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.root.destroy()
        except tk.TclError:
            pass
