# yt-dlp-Python-UI v6.0（網頁介面）

以 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 為核心的下載工具圖形介面。v6.0 的介面用 HTML／CSS／JavaScript 繪製，透過 [pywebview](https://pywebview.flowrl.com/) 顯示；後端仍是 Python。

> **v6.0 只支援 Windows（建議 Windows 11）。**
> 需要跨平台（Linux／macOS）請使用 [v5.6](https://github.com/Firefox0304/yt-dlp-Python-UI/releases/tag/UI-v5.6) 的 Tk 版 。

作者：Firefox_0304　協助 Bot：ChatGPT、Manus、Claude

## 需求
- Windows 11（內建 WebView2 執行環境；Windows 10 若沒有請先安裝 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)）
- Python 3（建議使用較新的版本）
- 依賴：`pywebview`、`yt-dlp`（見 `requirements.txt`）
- 合併影音、轉檔等功能需要 [FFmpeg](https://www.ffmpeg.org/download.html)

## 啟動
1. 解壓縮整個資料夾。
2. 雙擊 `yt-dlp-Python-UI.pyw`。

第一次啟動若缺少 `pywebview`，程式會自動用 pip 安裝（沒有進度視窗，請稍候）；自動安裝失敗時會跳出提示，改在命令提示字元執行：

```
pip install -r requirements.txt
```

## 主要功能
- 單一網址或批次清單（網址欄填 `file:路徑`），可載入範例清單
- 格式與畫質選擇、瀏覽器 Cookie、Cookies.txt 匯入，建議使用Chrome的[Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/cclelndahbckbenkjhflpdbgdldlbecc?utm_source=item-share-cb)擴充功能
- 播放清單偵測與詢問（可自訂資料夾名稱、自動編號）
- 進階選項：字幕、嵌入字幕、縮圖、影片資訊
- 下載加速（`-N` 切片數 4／8／16；只對 HLS／m3u8、DASH 清單等分片式串流有效，一般 YouTube 影片不會變快）
- 讀取影片資訊、取消下載、下載歷史（最近 50 筆）
- 右側「快速操作」：檢查更新（yt-dlp、Python、FFmpeg）、說明、系統診斷
- 淺色／系統／深色主題；自訂標題列與無邊框視窗

## 資料夾結構
| 路徑 | 說明 |
| --- | --- |
| `yt-dlp-Python-UI.pyw` | 啟動器 |
| `modules/` | Python 後端（`webui.py` 為網頁與 Python 之間的橋接） |
| `web/index.html` | 介面（CSS 與 JS 內嵌） |
| `fonts/` | 隨附字型 Noto Sans TC（OFL 授權） |
| `config/`、`logs/`、`Download/`、`example/` | 執行時自動產生 |                              

## 更新紀錄
見 [CHANGELOG.md](CHANGELOG.md)。
