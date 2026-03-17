@echo off
chcp 65001 > nul
echo [1/2] 正在嘗試使用 yt-dlp.exe -U 更新...
yt-dlp.exe -U
if %errorlevel% neq 0 (
    echo [2/2] yt-dlp.exe 更新失敗，正在嘗試使用 pip 更新...
    python -m pip install -U yt-dlp
)
echo.
echo 更新完成！請重新啟動程式並嘗試下載。
pause
