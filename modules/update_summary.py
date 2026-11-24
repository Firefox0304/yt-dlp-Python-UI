# modules/update_summary.py
"""把檢查更新的結果整理成給網頁顯示的文字。"""
from modules.i18n import t

# 升級／安裝命令旁的說明（標題, 提醒）所用的文字代號
COMMAND_NOTES = {
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
    elif y.get("status") == "update_available":  # v7.0：只提示，由安裝彈窗讓使用者確認後才更新
        lines.append(t("update.ytdlp.available", current=y["current"], latest=y["latest"]))
    else:
        problem = True
        lines.append(t("update.fail", name="yt-dlp", message=y.get("message", unknown)))

    ej = results.get("ejs")  # yt-dlp-ejs（只有 yt-dlp 走 pip 模組時才會有這一項）
    if ej is not None:
        if ej.get("status") == "ok":
            lines.append(t("update.ejs.ok", version=ej["current"]))
        elif ej.get("status") == "update_available":
            lines.append(t("update.ejs.available", current=ej["current"], latest=ej["latest"]))
        elif ej.get("status") == "missing":
            lines.append(t("update.ejs.missing"))
        else:
            problem = True
            lines.append(t("update.fail", name="yt-dlp-ejs", message=ej.get("message", unknown)))

    ui = results.get("ui")  # UI（本程式）：有新版顯示「v目前 → v最新」，沒有就只顯示目前版本
    if ui is not None:
        if ui.get("status") == "update_available":
            lines.append(t("update.ui.available", current=ui["current"], latest=ui["latest"]))
        elif ui.get("status") == "ok":
            lines.append(t("update.ui.ok", current=ui["current"]))
        else:
            lines.append(t("update.ui.unknown", current=ui.get("current", "")))

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

    ff = results.get("ffmpeg")  # 顯示版本與位置；winget 裝的 FFmpeg 有新版時另外提示
    if ff is not None:
        if ff.get("status") == "ok":
            where = t("update.ffmpeg.where.program") if ff.get("source") == "program" else t("update.ffmpeg.where.path")
            lines.append(t("update.ffmpeg.ok", version=ff.get("version") or t("update.ffmpeg.noversion"),
                           where=where, path=ff.get("path", "")))
            if ff.get("update"):
                lines[-1] += "\n  • " + t("update.ffmpeg.update")
        elif ff.get("status") == "missing":
            problem = True
            lines.append(t("update.ffmpeg.missing"))
        else:
            problem = True
            lines.append(t("update.fail", name="FFmpeg", message=ff.get("message", unknown)))

    dn = results.get("deno")  # Deno：只顯示版本與位置；winget 裝的有新版時另外提示
    if dn is not None:
        if dn.get("status") == "ok":
            lines.append(t("update.deno.ok", version=dn.get("version") or t("update.ffmpeg.noversion"),
                           path=dn.get("path", "")))
            if dn.get("update"):
                lines[-1] += "\n  • " + t("update.deno.update")
        elif dn.get("status") == "missing":
            lines.append(t("update.deno.missing"))
        else:
            problem = True
            lines.append(t("update.fail", name="Deno", message=dn.get("message", unknown)))
    return "\n\n".join(lines), problem, suggest_python
