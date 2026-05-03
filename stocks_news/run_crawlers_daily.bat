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
REM  全部爬蟲 (mid + lower + rare) - 每天跑一次
REM ============================================
set LOGDIR=d:\StockPulse\stocks_news\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set LOGFILE=%LOGDIR%\crawler_daily_%date:~0,4%%date:~5,2%%date:~8,2%.log

REM 清除 mid/lower/rare progress，讓每次都重新抓
del /q "d:\StockPulse\stocks_news\nstock\progress_nstock_mid.txt" 2>nul
del /q "d:\StockPulse\stocks_news\nstock\progress_nstock_lower.txt" 2>nul
del /q "d:\StockPulse\stocks_news\nstock\progress_nstock_rare.txt" 2>nul
del /q "d:\StockPulse\stocks_news\yahoo\progress_mid.txt" 2>nul
del /q "d:\StockPulse\stocks_news\yahoo\progress_lower.txt" 2>nul
del /q "d:\StockPulse\stocks_news\yahoo\progress_rare.txt" 2>nul
del /q "d:\StockPulse\stocks_news\cnyes\progress_cnyes_mid.txt" 2>nul
del /q "d:\StockPulse\stocks_news\cnyes\progress_cnyes_lower.txt" 2>nul
del /q "d:\StockPulse\stocks_news\cnyes\progress_cnyes_rare.txt" 2>nul

echo ============================================ >> "%LOGFILE%"
echo [%date% %time%] Daily crawlers starting >> "%LOGFILE%"
echo ============================================ >> "%LOGFILE%"

REM --- Mid tier ---
cd /d d:\StockPulse\stocks_news\nstock
echo [%date% %time%] Running nstock mid... >> "%LOGFILE%"
python crawler_mid.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\yahoo
echo [%date% %time%] Running yahoo mid... >> "%LOGFILE%"
python crawler_mid.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\cnyes
echo [%date% %time%] Running cnyes mid... >> "%LOGFILE%"
python crawler_mid.py >> "%LOGFILE%" 2>&1

REM --- Lower tier ---
cd /d d:\StockPulse\stocks_news\nstock
echo [%date% %time%] Running nstock lower... >> "%LOGFILE%"
python crawler_lower.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\yahoo
echo [%date% %time%] Running yahoo lower... >> "%LOGFILE%"
python crawler_lower.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\cnyes
echo [%date% %time%] Running cnyes lower... >> "%LOGFILE%"
python crawler_lower.py >> "%LOGFILE%" 2>&1

REM --- Rare tier ---
cd /d d:\StockPulse\stocks_news\nstock
echo [%date% %time%] Running nstock rare... >> "%LOGFILE%"
python crawler_rare.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\yahoo
echo [%date% %time%] Running yahoo rare... >> "%LOGFILE%"
python crawler_rare.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\cnyes
echo [%date% %time%] Running cnyes rare... >> "%LOGFILE%"
python crawler_rare.py >> "%LOGFILE%" 2>&1

echo [%date% %time%] Daily crawlers done. >> "%LOGFILE%"

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

echo [%date% %time%] Daily pipeline complete. >> "%LOGFILE%"
