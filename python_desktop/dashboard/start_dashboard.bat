@echo off
chcp 65001 >nul
echo.
echo ========================================
echo   股市新聞 Dashboard 啟動器
echo ========================================
echo.

cd /d %~dp0

echo [1/3] 檢查 Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 錯誤: 找不到 Python！
    echo 請確保 Python 已安裝並加入 PATH
    pause
    exit /b 1
)
echo ✅ Python 已安裝

echo.
echo [2/3] 檢查必要套件...
python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo ⚠️  警告: 缺少 Flask 套件
    echo 正在安裝必要套件...
    pip install flask asyncpg python-dotenv line-bot-sdk requests
)
echo ✅ 套件檢查完成

echo.
echo [3/3] 啟動 Dashboard...
echo.
echo ========================================
echo   服務器運行中
echo ========================================
echo.
echo 📊 Dashboard: http://localhost:5000
echo 🔄 自動刷新: 每30秒
echo 📅 一周分析: 已啟用
echo.
echo 按 Ctrl+C 停止服務器
echo ========================================
echo.

python app.py

pause
