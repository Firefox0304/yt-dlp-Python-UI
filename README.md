# yt-dlp-Python-UI（網頁介面版）

以 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 為核心的下載工具圖形介面。v6.0 的介面用 HTML／CSS／JavaScript 繪製，透過 [pywebview](https://pywebview.flowrl.com/) 顯示；後端仍是 Python。

> **v6.0 以上僅只支援 Windows（建議 Windows 11）。**
> 需要跨平台（Linux／macOS）版請使用 [v5.6](https://github.com/Firefox0304/yt-dlp-Python-UI/releases/tag/UI-v5.6) 的TK版。

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
- 網址自動整理：貼上整段文字也沒關係，只會讀取 `https://`（或 `http://`）開頭的網址，前面的文字會被忽略；沒有開頭時，像 `youtube.com/watch?v=…` 這樣的網址會自動補上 `https://`
- 格式與畫質選擇、瀏覽器 Cookie、Cookies.txt 匯入
- 播放清單偵測與詢問（可自訂資料夾名稱、自動編號）
- 進階選項：字幕、嵌入字幕、縮圖、影片資訊
- 下載加速（`-N` 切片數 4／8／16；只對 HLS／m3u8、DASH 清單等分片式串流有效，一般 YouTube 影片不會變快）
- 讀取影片資訊、取消下載、下載歷史（最近 50 筆）
- 右側「快速操作」：載入範例、說明、下載歷史、開啟檔案位置（在檔案總管選取最近下載的檔案）、檢查更新（yt-dlp、Python、FFmpeg）、系統診斷（可匯出診斷包，方便回報問題）
- 淺色／系統／深色主題；自訂標題列與無邊框視窗
- 介面語言：繁體中文、简体中文、English、日本語（標題列右上方可切換；預設「自動」依 Windows 顯示語言決定）

## 資料夾結構
| 路徑 | 說明 |
| --- | --- |
| `yt-dlp-Python-UI.pyw` | 啟動器 |
| `modules/` | Python 後端（`webui.py` 為網頁與 Python 之間的橋接；`winshell.py` 為自訂標題列的視窗控制；`progress.py` 為進度行解析；`diagnostics.py` 為診斷包匯出） |
| `web/` | 介面：`index.html`（結構）、`style.css`（樣式）、`app.js`（邏輯） |
| `locales/` | 介面文字：每個語言一個資料夾（`zh-TW/`、`zh-CN/`、`en/`、`ja/`），裡面的 `strings.json`（介面文字）、`errors.json`（錯誤訊息 `err.*`）、`logs.json`（日誌訊息 `log.*`）會合併載入；資料夾內新增其他 `.json` 也會被讀取 |
| `fonts/` | 隨附字型 Noto Sans TC（OFL 授權） |
| `tests/`、`tools/` | 單元測試與開發用小工具（執行程式不需要） |
| `config/`、`logs/`、`Download/`、`example/` | 執行時自動產生 |

## 開發與測試
在專案根目錄執行（只用標準函式庫，不需額外安裝）：

> 單元測試（進度解析、下載命令、網址整理、安全檢查、版本與語言檔一致性）
```
python -m unittest                 
```
>檢查四個語言資料夾是否缺字、多字、重複、放錯檔案、占位符不一致
```
python tools/check_locales.py
```

版本號只在 `modules/version.py` 的 `APP_VERSION` 修改一處；標題列與視窗標題都會自動帶入，
改版時記得在 `CHANGELOG.md` 最上方新增同版本號的標題（測試會檢查兩者一致）。

## 更新紀錄
見 [CHANGELOG.md](CHANGELOG.md)。
