import asyncio
import json
import os
import random
import time
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from curl_cffi.requests import AsyncSession
import asyncpg
import pandas as pd
from bs4 import BeautifulSoup

# ================= 爬蟲等級配置 =================
CRAWLER_TIER = "中型股"
STOCK_FILE = "../../stocks_category/股票代號_中間_v2.csv"
PROGRESS_FILE = "progress_nstock_mid.txt"
DAYS_FILTER = 7  # 只抓 7 天內新聞
MAX_CONCURRENCY = 5
STOCK_DELAY_RANGE = (2.5, 5.0)

# ================= 通用設定區 =================
PG_DSN = "postgresql://postgres:lab529@localhost:5432/postgres"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br, zstd",
}

# 使用相同的 SQL 和函式 (從 crawler_hot.py 導入)
from crawler_hot import (
    CREATE_TABLE_SQL, UPSERT_SQL,
    extract_nuxt_data, parse_news_from_nuxt,
    fetch_stock_news, save_news_to_db,
    load_progress, save_progress
)

async def main():
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    print(f"\n{'='*60}")
    print(f"NStock 股市新聞爬蟲 - {CRAWLER_TIER}")
    print(f"{'='*60}\n")
    
    try:
        df = pd.read_csv(STOCK_FILE, dtype=str)
        stock_ids = df.iloc[:, 0].tolist()
        print(f"[INFO] 讀取股票清單: {len(stock_ids)} 支股票\n")
    except Exception as e:
        print(f"[ERROR] 讀取股票清單失敗: {e}")
        return
    
    processed = load_progress(PROGRESS_FILE)
    remaining = [s for s in stock_ids if s not in processed]
    
    if not remaining:
        print("[OK] 所有股票已處理完畢")
        return
    
    print(f"[INFO] 已處理: {len(processed)} 支")
    print(f"[INFO] 待處理: {len(remaining)} 支\n")
    
    try:
        pool = await asyncpg.create_pool(PG_DSN, min_size=2, max_size=10)
        async with pool.acquire() as conn:
            await conn.execute(CREATE_TABLE_SQL)
        print("[OK] 資料庫連線成功\n")
    except Exception as e:
        print(f"[ERROR] 資料庫連線失敗: {e}")
        return
    
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    total_news = 0
    failed_count = 0
    MAX_CONSECUTIVE_FAIL = 10
    start_time = time.time()
    
    async with AsyncSession() as session:
        for i, stock_id in enumerate(remaining, 1):
            print(f"[{i}/{len(remaining)}] 處理 {stock_id}...")
            news_list = await fetch_stock_news(session, stock_id, semaphore, DAYS_FILTER)
            
            if news_list is None:
                failed_count += 1
                print(f"  [WARN] 連線失敗，跳過不記錄進度 (連續失敗: {failed_count})")
                if failed_count >= MAX_CONSECUTIVE_FAIL:
                    print(f"\n[ABORT] 連續失敗 {failed_count} 次，疑似網站無法連線，提前結束")
                    break
                await asyncio.sleep(5)
                continue
            
            failed_count = 0
            if news_list:
                saved = await save_news_to_db(pool, news_list)
                total_news += saved
            save_progress(stock_id, PROGRESS_FILE)
            if i < len(remaining):
                await asyncio.sleep(random.uniform(*STOCK_DELAY_RANGE))
    
    await pool.close()
    
    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"[OK] 爬取完成!")
    print(f"{'='*60}")
    print(f"處理股票: {len(remaining)} 支")
    print(f"抓取新聞: {total_news} 筆")
    print(f"耗時: {elapsed:.1f} 秒")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    asyncio.run(main())
