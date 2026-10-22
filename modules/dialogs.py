# modules/dialogs.py
"""需要自訂按鈕或輸入欄的對話框（tkinter 內建的 messagebox 做不到）。"""
import webbrowser
from dataclasses import dataclass

import customtkinter as ctk



@dataclass
class PlaylistChoice:
    mode: str  # "single"（只下載單一影片）或 "playlist"（下載整個播放清單）
    name: str = ""  # 使用者輸入的資料夾名稱；空字串代表使用自動命名


class _ChoiceDialog(ctk.CTkToplevel):
    """通用的「訊息 + 可選輸入欄 + 多個按鈕」模態對話框。

    buttons 是 [(key, 文字, 樣式)]，樣式為 "primary" / "normal" / "cancel"。
    關閉視窗、按 Esc 都視為取消。結果存在 self.result = (key, 輸入欄文字) 或 None。
    """

    def __init__(self, parent, title, message, buttons, name_placeholder=None):
        super().__init__(parent)
        self.result = None
        self._entry = None
        self._default_key = next((k for k, _t, style in buttons if style == "primary"), None)

        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=22, pady=(20, 8))
        ctk.CTkLabel(body, text=message, justify="left", anchor="w", wraplength=440).pack(fill="x")

        if name_placeholder is not None:
            ctk.CTkLabel(body, text="播放清單資料夾名稱（留空則自動命名）：", anchor="w").pack(
                fill="x", pady=(14, 4))
            self._entry = ctk.CTkEntry(body, placeholder_text=name_placeholder)
            self._entry.pack(fill="x")
            ctk.CTkLabel(body, text="只有選擇「下載整個播放清單」時才會建立並使用這個資料夾。",
                         anchor="w", justify="left", wraplength=440,
                         text_color=("#777777", "#aaaaaa"), font=ctk.CTkFont(size=11)).pack(fill="x", pady=(4, 0))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(pady=(10, 18))
        for key, text, style in buttons:
            kwargs = {}
            if style == "cancel":
                kwargs = {"fg_color": ("#9a9a9a", "#555555"), "hover_color": ("#808080", "#444444")}
            ctk.CTkButton(row, text=text, width=130, command=lambda k=key: self._finish(k),
                          **kwargs).pack(side="left", padx=6)

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._finish(self._default_key) if self._default_key else None)
        self._center_on(parent)
        self.after(100, self._grab)

    def _center_on(self, parent):
        try:
            self.update_idletasks()
            x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 3
            self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        except Exception:  # noqa: BLE001
            pass

    def _grab(self):
        # 視窗尚未可見時 grab_set 會失敗，所以延遲到顯示之後再做
        try:
            self.grab_set()
            self.focus_force()
        except Exception:  # noqa: BLE001
            pass

    def _finish(self, key):
        if key == "cancel":
            self.result = None
        else:
            self.result = (key, self._entry.get().strip() if self._entry is not None else "")
        self.destroy()

    def _cancel(self):
        self._finish("cancel")


def ask_playlist_choice(parent, kind, default_name):
    """單一網址含播放清單時詢問。kind 為 "mixed"（影片加清單）或 "playlist"（純清單）。

    回傳 PlaylistChoice；使用者取消則回傳 None。
    """
    if kind == "mixed":
        message = "這個網址同時包含「單一影片」與「所屬的播放清單」。\n請選擇要下載的內容："
        buttons = [("single", "只下載這支影片", "primary"),
                   ("playlist", "下載整個播放清單", "normal"),
                   ("cancel", "取消", "cancel")]
    else:
        message = "這是播放清單網址，會下載清單內的所有影片（數量可能很多）。\n要繼續嗎？"
        buttons = [("playlist", "下載整個播放清單", "primary"),
                   ("cancel", "取消", "cancel")]
    dialog = _ChoiceDialog(parent, "偵測到播放清單", message, buttons, name_placeholder=default_name)
    parent.wait_window(dialog)
    if dialog.result is None:
        return None
    return PlaylistChoice(mode=dialog.result[0], name=dialog.result[1])


def ask_batch_playlist_choice(parent, total, pure):
    """批次清單中含有播放清單網址時統一詢問一次。

    回傳 "playlist"（含清單的網址都下載整個清單）、"single"（只下載單一影片，純清單會略過）
    或 None（取消）。
    """
    message = (f"批次清單中有 {total} 筆網址含有播放清單（其中 {pure} 筆是純播放清單）。\n\n"
               "每個播放清單會自動以「播放清單N」命名資料夾。\n請選擇處理方式：")
    buttons = [("single", "只下載單一影片\n（純播放清單會略過）", "primary"),
               ("playlist", "下載整個播放清單", "normal"),
               ("cancel", "取消", "cancel")]
    dialog = _ChoiceDialog(parent, "偵測到播放清單", message, buttons)
    parent.wait_window(dialog)
    return None if dialog.result is None else dialog.result[0]


def ask_open_ffmpeg_page(parent, message, url, command=None):
    """找不到 FFmpeg 時提示，並可一鍵開啟官網下載頁。command 是 (按鈕文字, 命令)，有的話顯示並提供複製按鈕。

    回傳是否有按下「開啟 FFmpeg 下載頁」。
    """
    dialog = show_update_result(parent, message, ffmpeg_url=url, command=command,
                                command_kind="ffmpeg", title="缺少 FFmpeg")
    return dialog.opened_ffmpeg


# 命令區塊的標題與提醒（Python 升級要先關閉本程式；安裝 FFmpeg 不用）
COMMAND_TEXTS = {
    "python": ("升級命令（按「複製」會放進剪貼簿）：",
               "請先關閉本程式，再到「命令提示字元」或 PowerShell 貼上執行。"),
    "ffmpeg": ("安裝命令（按「複製」會放進剪貼簿）：",
               "請在終端機貼上執行（需要輸入密碼）。裝好後回到本程式重新操作即可，不需要關閉本程式。"),
}


class _UpdateResultDialog(ctk.CTkToplevel):
    """顯示檢查更新的結果，並依情況附上操作按鈕（都在同一排，彼此相鄰）：

    開啟 Python 下載頁、複製 winget 命令（只有 Windows 且 winget 可用）、開啟 FFmpeg 下載頁（找不到 FFmpeg 時）。
    """

    def __init__(self, parent, text, python_url, command, ffmpeg_url, title="檢查更新", command_kind="python"):
        super().__init__(parent)
        self._parent = parent
        self._python_url = python_url
        self._ffmpeg_url = ffmpeg_url
        self._label, self._command = command if command else (None, None)
        self.opened_ffmpeg = False

        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=22, pady=(20, 8))
        ctk.CTkLabel(body, text=text, justify="left", anchor="w", wraplength=520).pack(fill="x")

        if self._command:
            command_title, command_hint = COMMAND_TEXTS.get(command_kind, COMMAND_TEXTS["python"])
            ctk.CTkLabel(body, text=command_title, anchor="w").pack(fill="x", pady=(14, 4))
            ctk.CTkLabel(body, text=self._command, anchor="w", justify="left", wraplength=500,
                         fg_color=("#e8e8e8", "#2b2b2b"), corner_radius=6, padx=10, pady=6,
                         font=ctk.CTkFont(family="Consolas", size=12)).pack(fill="x")
            ctk.CTkLabel(body, text=command_hint,
                         anchor="w", justify="left", wraplength=520, text_color=("#777777", "#aaaaaa"),
                         font=ctk.CTkFont(size=11)).pack(fill="x", pady=(6, 0))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(pady=(10, 18))
        self.copy_btn = None

        def add_copy_button():  # 複製按鈕緊跟在它所屬的「開啟下載頁」按鈕旁邊
            self.copy_btn = ctk.CTkButton(row, text=self._label, width=150, command=self._copy)
            self.copy_btn.pack(side="left", padx=6)

        if python_url:
            ctk.CTkButton(row, text="開啟 Python 下載頁", width=150, command=self._open_python).pack(side="left", padx=6)
        if self._command and command_kind == "python":
            add_copy_button()
        if ffmpeg_url:
            ctk.CTkButton(row, text="開啟 FFmpeg 下載頁", width=160, command=self._open_ffmpeg).pack(side="left", padx=6)
        if self._command and command_kind == "ffmpeg":
            add_copy_button()
        ctk.CTkButton(row, text="關閉", width=90, fg_color=("#9a9a9a", "#555555"),
                      hover_color=("#808080", "#444444"), command=self.destroy).pack(side="left", padx=6)

        self.bind("<Escape>", lambda _e: self.destroy())
        try:
            self.update_idletasks()
            x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 3
            self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        except Exception:  # noqa: BLE001
            pass
        self.after(100, self._grab)

    def _grab(self):
        try:
            self.grab_set()
            self.focus_force()
        except Exception:  # noqa: BLE001
            pass

    def _open_python(self):
        webbrowser.open(self._python_url)

    def _open_ffmpeg(self):
        self.opened_ffmpeg = True
        webbrowser.open(self._ffmpeg_url)

    def _copy(self):
        try:
            self._parent.clipboard_clear()
            self._parent.clipboard_append(self._command)
            self._parent.update()  # 讓剪貼簿內容立即生效
            self.copy_btn.configure(text="已複製 ✓")
        except Exception:  # noqa: BLE001
            self.copy_btn.configure(text="複製失敗")
        self.after(2000, self._restore_copy_label)

    def _restore_copy_label(self):
        try:
            self.copy_btn.configure(text=self._label)
        except Exception:  # noqa: BLE001 — 對話框可能已經關閉
            pass


def show_update_result(parent, text, python_url=None, command=None, ffmpeg_url=None,
                       command_kind="python", title="檢查更新"):
    """顯示結果對話框。沒有給的網址／命令就不會出現對應的按鈕；回傳對話框（可查詢使用者按了什麼）。

    command_kind 決定命令區塊的文字與複製按鈕的位置："python"（接在 Python 下載頁旁）或 "ffmpeg"（接在 FFmpeg 下載頁旁）。
    """
    dialog = _UpdateResultDialog(parent, text, python_url, command, ffmpeg_url, title=title, command_kind=command_kind)
    parent.wait_window(dialog)
    return dialog
