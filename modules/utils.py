# modules/utils.py
import os, random
from PIL import Image, ImageTk  # pillow usually installed as dependency if needed
import tkinter as tk

IMG_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif")



def load_photoimage_for_label(path, width, height):
    """載入且等比縮放成指定寬高內的 PhotoImage (PIL -> ImageTk)"""
    try:
        img = Image.open(path)
        img.thumbnail((width, height), Image.LANCZOS)
        return ImageTk.PhotoImage(img)
    except Exception as e:
        return None

def find_app_icon(base_dir):
    cand = os.path.join(base_dir, "icon", "app_icon.ico")
    if os.path.exists(cand):
        return cand
    return None

def find_title_banner(base_dir):
    """
    尋找標題橫幅圖片
    """
    for ext in ["png", "jpg", "jpeg", "gif"]:
        p = os.path.join(base_dir, "icon", f"banner.{ext}")
        if os.path.exists(p):
            return p
    return None
