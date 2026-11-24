# modules/channel.py
"""這份程式是不是「搶先版（Beta）」：本地版本比 GitHub 上最新的 tag 還高（見 updater.check_ui）。

只存在記憶體裡，不寫進設定檔：GitHub 上發佈對應的 tag 之後，下次檢查就會自動取消。
所有要顯示版本號的地方（頭標題、視窗標題、檢查更新、診斷報告）都從這裡取，才不會有的標了、有的沒標。
"""
from modules.version import APP_NAME, APP_VERSION

_beta = False


def set_beta(flag):
    global _beta
    _beta = bool(flag)


def is_beta():
    return _beta


def version_label():
    """'v7.1.2'；Beta 時是 'v7.1.2 Beta'。"""
    return f"v{APP_VERSION}" + (" Beta" if _beta else "")


def title():
    """視窗標題、診斷報告標頭用：'yt-dlp-python-UI v7.1.2 Beta'。"""
    return f"{APP_NAME} {version_label()}"
