@echo off
chcp 65001 >nul
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\工作列硬體儀表.lnk" 2>nul
echo 已移除開機啟動捷徑
pause
