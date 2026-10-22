# modules/logger.py
"""PrismLauncher 風格的工作階段日誌。

目前這次執行永遠寫入 logs/latest.log；下次啟動時，上一份 latest.log 會被改名成
帶時間戳記的封存檔，並只保留最近 _MAX_ARCHIVES 份。
"""
import logging
import os
import shutil
import sys
from datetime import datetime

_LOGGER_NAME = "ytgui"
_MAX_ARCHIVES = 10


def _archive_previous_latest(log_dir):
    latest = os.path.join(log_dir, "latest.log")
    if not os.path.isfile(latest) or os.path.getsize(latest) == 0:
        return

    stamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    archive = os.path.join(log_dir, f"{stamp}.log")
    counter = 1
    while os.path.exists(archive):
        archive = os.path.join(log_dir, f"{stamp}-{counter}.log")
        counter += 1
    try:
        shutil.move(latest, archive)
    except OSError:
        # 若另一個行程還握著檔案，就保留 latest.log 繼續執行。
        pass

    archives = []
    for name in os.listdir(log_dir):
        if name.endswith(".log") and name != "latest.log":
            path = os.path.join(log_dir, name)
            if os.path.isfile(path):
                archives.append((os.path.getmtime(path), path))
    archives.sort(reverse=True)
    for _, path in archives[_MAX_ARCHIVES:]:
        try:
            os.remove(path)
        except OSError:
            pass


def setup_logging(path_or_dir):
    """初始化日誌。path_or_dir 可以是資料夾，或舊版的 .../app.log 路徑。"""
    if os.path.splitext(os.path.basename(path_or_dir))[1].lower() == ".log":
        log_dir = os.path.dirname(path_or_dir)
    else:
        log_dir = path_or_dir
    log_dir = os.path.abspath(log_dir)
    os.makedirs(log_dir, exist_ok=True)
    _archive_previous_latest(log_dir)

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # 同一個直譯器重啟 UI 時，避免重複輸出。
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)

    latest_path = os.path.join(log_dir, "latest.log")
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")

    fh = logging.FileHandler(latest_path, mode="w", encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    logger.addHandler(fh)

    # pythonw.exe 沒有 stderr，這時不要掛 StreamHandler。
    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.setLevel(logging.INFO)
        logger.addHandler(sh)

    logger.info("Logger initialized; active log: %s", latest_path)
    return log_dir


def get():
    return logging.getLogger(_LOGGER_NAME)
