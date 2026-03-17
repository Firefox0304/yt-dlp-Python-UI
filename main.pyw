import os
import sys
from modules import config_manager, ui, logger

def get_base_dir():
    """
    取得程式執行的基礎目錄。
    如果是打包後的 EXE，則使用 sys.executable 的路徑；
    否則使用目前腳本的路徑。
    這可以避免路徑落入系統的 Temp 資料夾。
    """
    if getattr(sys, 'frozen', False):
        # 如果是 PyInstaller 打包後的環境
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def main():
    base_dir = get_base_dir()
    
    # 初始化日誌
    log_path = os.path.join(base_dir, "logs", "app.log")
    logger.setup_logging(log_path)
    
    # 載入設定
    config_path = os.path.join(base_dir, "config", "settings.json")
    cfg = config_manager.load_or_init(config_path)
    
    # 啟動 UI
    ui.launch_ui(cfg, base_dir)

if __name__ == "__main__":
    main()
