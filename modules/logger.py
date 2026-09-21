# modules/logger.py
"""PrismLauncher-style session logging.

The active session is always written to logs/latest.log. When the application
starts again, the previous latest.log is moved to a timestamped archive. This
keeps the most recent log easy to find while preserving past sessions.
"""
import logging
import os
import shutil
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
        # If another process still owns the file, keep latest.log and continue.
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
    """Initialize logging with latest.log plus timestamped session archives.

    ``path_or_dir`` may be a log directory or the legacy ``.../app.log`` path.
    """
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

    # Avoid duplicate entries if the UI is restarted in the same interpreter.
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)

    latest_path = os.path.join(log_dir, "latest.log")
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")

    fh = logging.FileHandler(latest_path, mode="w", encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.setLevel(logging.INFO)

    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.info("Logger initialized; active log: %s", latest_path)
    global log
    log = logger
    return log_dir


def get():
    return logging.getLogger(_LOGGER_NAME)
