# modules/logger.py
import logging
import logging.handlers
import os

def setup_logging(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    logger = logging.getLogger("ytgui")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")

    fh = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.setLevel(logging.INFO)

    if not logger.handlers:
        logger.addHandler(fh)
        logger.addHandler(sh)
    logger.info("Logger initialized")
    global log
    log = logger

# convenience
def get():
    return logging.getLogger("ytgui")
