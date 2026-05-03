@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

REM 等待網路連線（最多等 120 秒）
set /a WAIT_COUNT=0
:WAIT_NETWORK
ping -n 1 8.8.8.8 >nul 2>&1
if %errorlevel%==0 goto NETWORK_OK
set /a WAIT_COUNT+=1
if %WAIT_COUNT% GEQ 24 (
    echo [%date% %time%] 等待網路逾時，跳過本次執行 >> "%LOGFILE%"
    exit /b 1
)
timeout /t 5 /nobreak >nul
goto WAIT_NETWORK
:NETWORK_OK
REM 從 .env 讀取環境變數（跳過 # 註解和空行）
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("d:\StockPulse\.env") do (
    set "%%A=%%B"
)
REM ============================================
REM  熱門股爬蟲 - 建議每 3-4 小時跑一次
REM ============================================
set LOGDIR=d:\StockPulse\stocks_news\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set LOGFILE=%LOGDIR%\crawler_hot_%date:~0,4%%date:~5,2%%date:~8,2%.log

REM 清除 hot progress，讓每次都重新抓
del /q "d:\StockPulse\stocks_news\nstock\progress_nstock_hot.txt" 2>nul
del /q "d:\StockPulse\stocks_news\yahoo\progress_hot.txt" 2>nul
del /q "d:\StockPulse\stocks_news\cnyes\progress_cnyes_hot.txt" 2>nul

echo ============================================ >> "%LOGFILE%"
echo [%date% %time%] Hot crawlers starting >> "%LOGFILE%"
echo ============================================ >> "%LOGFILE%"

cd /d d:\StockPulse\stocks_news\nstock
echo [%date% %time%] Running nstock hot... >> "%LOGFILE%"
python crawler_hot.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\yahoo
echo [%date% %time%] Running yahoo hot... >> "%LOGFILE%"
python crawler_hot.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\cnyes
echo [%date% %time%] Running cnyes hot... >> "%LOGFILE%"
python crawler_hot.py >> "%LOGFILE%" 2>&1

echo [%date% %time%] Hot crawlers done. >> "%LOGFILE%"

REM ============================================
REM  Phase 2: 補抓內文
REM ============================================
echo [%date% %time%] Fetching content... >> "%LOGFILE%"

cd /d d:\StockPulse\stocks_news\nstock
echo [%date% %time%] Running nstock fetch_content... >> "%LOGFILE%"
python fetch_content.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\yahoo
echo [%date% %time%] Running yahoo fetch_content... >> "%LOGFILE%"
python fetch_content.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\cnyes
echo [%date% %time%] Running cnyes fetch_content... >> "%LOGFILE%"
python fetch_content.py >> "%LOGFILE%" 2>&1

REM ============================================
REM  Phase 3: 情緒分析
REM ============================================
echo [%date% %time%] Analyzing sentiment... >> "%LOGFILE%"

cd /d d:\StockPulse\stocks_news\nstock
echo [%date% %time%] Running nstock analyze_sentiment... >> "%LOGFILE%"
python analyze_sentiment.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\yahoo
echo [%date% %time%] Running yahoo analyze_sentiment... >> "%LOGFILE%"
python analyze_sentiment.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\cnyes
echo [%date% %time%] Running cnyes analyze_sentiment... >> "%LOGFILE%"
python analyze_sentiment.py >> "%LOGFILE%" 2>&1

echo [%date% %time%] Hot pipeline complete. >> "%LOGFILE%"
