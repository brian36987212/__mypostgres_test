@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

REM ============================================
REM  Weekly crawlers: LOWER + RARE tiers
REM  低活躍股票不需每天重掃，改每週一次（視窗 10 天）。
REM  排到 Windows 工作排程器：每週一次即可。
REM ============================================

set LOGDIR=d:\StockPulse\stocks_news\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f %%D in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set TODAY=%%D
set LOGFILE=%LOGDIR%\crawler_weekly_%TODAY%.log

REM Wait for network (up to 120 seconds)
set /a WAIT_COUNT=0
:WAIT_NETWORK
ping -n 1 8.8.8.8 >nul 2>&1
if %errorlevel%==0 goto NETWORK_OK
set /a WAIT_COUNT+=1
if %WAIT_COUNT% GEQ 24 (
    echo [%date% %time%] Network timeout, skipping >> "%LOGFILE%"
    exit /b 1
)
timeout /t 5 /nobreak >nul
goto WAIT_NETWORK
:NETWORK_OK

REM Disable sleep during execution
powercfg /change standby-timeout-ac 0

REM Load environment variables from .env (skip # comments and blank lines)
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("d:\StockPulse\.env") do (
    set "%%A=%%B"
)

REM 週跑的新聞常大於 3 天，放寬情緒分析視窗以補上 lower/rare 的情緒分數
set SENTIMENT_DAYS_LIMIT=10

REM Clear lower + rare progress so each run fetches fresh
del /q "d:\StockPulse\stocks_news\nstock\progress_nstock_lower.txt" 2>nul
del /q "d:\StockPulse\stocks_news\nstock\progress_nstock_rare.txt" 2>nul
del /q "d:\StockPulse\stocks_news\yahoo\progress_lower.txt" 2>nul
del /q "d:\StockPulse\stocks_news\yahoo\progress_rare.txt" 2>nul
del /q "d:\StockPulse\stocks_news\cnyes\progress_cnyes_lower.txt" 2>nul
del /q "d:\StockPulse\stocks_news\cnyes\progress_cnyes_rare.txt" 2>nul

echo ============================================ >> "%LOGFILE%"
echo [%date% %time%] Weekly crawlers starting (SENTIMENT_DAYS_LIMIT=%SENTIMENT_DAYS_LIMIT%) >> "%LOGFILE%"
echo ============================================ >> "%LOGFILE%"

REM --- Lower tier ---
cd /d d:\StockPulse\stocks_news\nstock
echo [%date% %time%] Running nstock lower... >> "%LOGFILE%"
python -u crawler_lower.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\yahoo
echo [%date% %time%] Running yahoo lower... >> "%LOGFILE%"
python -u crawler_lower.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\cnyes
echo [%date% %time%] Running cnyes lower... >> "%LOGFILE%"
python -u crawler_lower.py >> "%LOGFILE%" 2>&1

REM --- Rare tier ---
cd /d d:\StockPulse\stocks_news\nstock
echo [%date% %time%] Running nstock rare... >> "%LOGFILE%"
python -u crawler_rare.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\yahoo
echo [%date% %time%] Running yahoo rare... >> "%LOGFILE%"
python -u crawler_rare.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\cnyes
echo [%date% %time%] Running cnyes rare... >> "%LOGFILE%"
python -u crawler_rare.py >> "%LOGFILE%" 2>&1

echo [%date% %time%] Weekly crawlers done. >> "%LOGFILE%"

REM ============================================
REM  Phase 2: Fetch article content
REM ============================================
echo [%date% %time%] Fetching content... >> "%LOGFILE%"

cd /d d:\StockPulse\stocks_news\nstock
python -u fetch_content.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\yahoo
python -u fetch_content.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\cnyes
python -u fetch_content.py >> "%LOGFILE%" 2>&1

REM ============================================
REM  Phase 3: Sentiment analysis (widened window)
REM ============================================
echo [%date% %time%] Analyzing sentiment (DAYS_LIMIT=%SENTIMENT_DAYS_LIMIT%)... >> "%LOGFILE%"

cd /d d:\StockPulse\stocks_news\nstock
python -u analyze_sentiment.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\yahoo
python -u analyze_sentiment.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\cnyes
python -u analyze_sentiment.py >> "%LOGFILE%" 2>&1

echo [%date% %time%] Weekly pipeline complete. >> "%LOGFILE%"

REM Restore sleep timeout (30 minutes)
powercfg /change standby-timeout-ac 30
