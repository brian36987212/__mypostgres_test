import asyncio
import json
import re
import os
import random
import time
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urljoin

from curl_cffi.requests import AsyncSession
import asyncpg
import pandas as pd
from bs4 import BeautifulSoup

# ================= 爬蟲等級配置 =================
CRAWLER_TIER = "稀少股"  # 🐢 識別標記
STOCK_TIER = "rare"  # 🐢 股票分級標記
STOCK_FILE = "../../stocks_category/股票代號_稀少_v2.csv"
PROGRESS_FILE = "progress_rare.txt"
MAX_CONCURRENCY = 5  # 🔥 並發數（保守模式）

# 延遲範圍 (秒)
STOCK_DELAY_RANGE = (4.0, 7.0)  # 股票間延遲
NEWS_DELAY_RANGE = (2.0, 4.0)   # 新聞間延遲

# ================= 通用設定區 =================

BASE_URL = "https://tw.stock.yahoo.com"
PG_DSN = "postgresql://postgres:lab529@localhost:5432/postgres"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "cache-control": "max-age=0",
}

WEEKDAY_ZH = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.yahoo_stock_news (
  id BIGSERIAL PRIMARY KEY,
  stock_id TEXT NOT NULL,
  title TEXT NOT NULL,
  publisher TEXT,
  reporter TEXT,
  published_text TEXT,
  content TEXT,
  sentiment_score INTEGER,
  url TEXT NOT NULL,
  stock_tier TEXT,
  fetched_at TIMESTAMPTZ,
  fetched_date DATE,
  fetched_time TIME,
  UNIQUE (stock_id, url)
);
"""

UPSERT_SQL = """
INSERT INTO public.yahoo_stock_news
(stock_id, title, publisher, reporter, published_text, content, sentiment_score, url, stock_tier, fetched_at, fetched_date, fetched_time)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
ON CONFLICT (stock_id, url) DO UPDATE SET
  title = EXCLUDED.title,
  publisher = EXCLUDED.publisher,
  reporter = EXCLUDED.reporter,
  published_text = EXCLUDED.published_text,
  content = EXCLUDED.content,
  sentiment_score = EXCLUDED.sentiment_score,
  stock_tier = EXCLUDED.stock_tier,
  fetched_at = EXCLUDED.fetched_at,
  fetched_date = EXCLUDED.fetched_date,
  fetched_time = EXCLUDED.fetched_time;
"""

def format_time_yahoo_tw(iso_z: str):
    if not iso_z: return None, None
    try:
        s = iso_z.strip()
        if s.endswith("Z"): s = s.replace("Z", "+00:00")
        dt_utc = datetime.fromisoformat(s)
        dt = dt_utc.astimezone(ZoneInfo("Asia/Taipei"))
        ampm = "上午" if dt.hour < 12 else "下午"
        hour_12 = dt.hour % 12 or 12
        weekday = WEEKDAY_ZH[dt.weekday()]
        fmt_str = f"{dt.year}年{dt.month}月{dt.day}日 {weekday} {ampm}{hour_12}:{dt.minute:02d}"
        return fmt_str, dt
    except Exception:
        return None, None

def strip_publisher_from_reporter(reporter: str | None, publisher: str | None) -> str | None:
    if not reporter: return None
    r = reporter.strip()
    if not r: return None
    known_publishers = ["工商時報", "經濟日報", "時報資訊", "中央社", "Yahoo財經", "Yahoo", 
                        "中時新聞網", "中時", "旺報", "財訊快報", "財訊", 
                        "東森財經", "東森新聞", "東森", "鉅亨網"]
    for pub in known_publishers:
        r = r.replace(pub, "")
    if publisher:
        p_full = publisher.strip()
        if p_full:
            r = r.replace(p_full, "")
            r = re.sub(r"(新聞網|新聞|電子報|財經|報|網)$", "", r)
    r = re.split(r"(台北|新北|台中|高雄|台南|桃園|新竹|綜合外電|外電|報導|電\s*[）)])", r)[0]
    r = re.sub(r"(記者|特派|派駐|採訪|整理|編輯|專欄)", "", r)
    r = re.sub(r"[／/｜|：:\[\]【】\(\)\s]", " ", r)
    r = re.sub(r"\s+", " ", r).strip()
    if len(r) > 10 or len(r) < 2: return None
    return r

async def get_html_async(session: AsyncSession, url: str, referer: str = None) -> str:
    retries = 3
    base_delay = 2.0
    
    for i in range(retries):
        try:
            headers = HEADERS.copy()
            if referer:
                headers["referer"] = referer
            
            response = await session.get(
                url, 
                headers=headers, 
                timeout=20,
                impersonate="chrome131",
                allow_redirects=True
            )
            
            if response.status_code == 200:
                return response.text
            elif response.status_code in [403, 429]:
                if i < retries - 1:
                    delay = base_delay * (2 ** i) + random.uniform(0, 2)
                    print(f"⚠️ 狀態碼 {response.status_code}，等待 {delay:.1f}s 後重試... ({url})")
                    await asyncio.sleep(delay)
                else:
                    print(f"❌ 請求被拒絕 ({url}): Status {response.status_code}")
                    return None
            else:
                response.raise_for_status()
                return response.text
                
        except Exception as e:
            if i == retries - 1:
                print(f"⚠️ 請求失敗 ({url}): {e}")
                return None
            delay = base_delay * (2 ** i) + random.uniform(0, 1)
            await asyncio.sleep(delay)
    
    return None


def parse_list_page(html: str, limit: int = 5):
    if not html: return []
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if "/news/" not in href: continue
        list_title = a.get_text(strip=True)
        if not list_title or list_title == "新聞" or list_title.isdigit(): continue
        
        url = urljoin(BASE_URL, href).split("?")[0]
        if url in seen: continue
        seen.add(url)
        results.append((list_title, url))
        
        if len(results) >= limit:
            break
    return results


def mark_stock_done(stock_code: str):
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{stock_code}\n")

def load_processed_stocks():
    if not os.path.exists(PROGRESS_FILE):
        return set()
    with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

async def process_stock(sem: asyncio.Semaphore, session: AsyncSession, db_pool: asyncpg.Pool, stock_code: str):
    async with sem:
        if ".TW" not in stock_code and ".TWO" not in stock_code:
            target_url = f"https://tw.stock.yahoo.com/quote/{stock_code}.TW/news"
        else:
            target_url = f"https://tw.stock.yahoo.com/quote/{stock_code}/news"

        await asyncio.sleep(random.uniform(*STOCK_DELAY_RANGE))
        
        list_html = await get_html_async(session, target_url, referer=BASE_URL)
        news_list = parse_list_page(list_html)
        
        if not news_list:
            print(f"💤 ({stock_code}) 無新聞列表")
            mark_stock_done(stock_code)
            return

        print(f"🚀 ({stock_code}) 掃描到 {len(news_list)} 則新聞，開始儲存...")
        processed_count = 0
        for list_title, url in news_list:
            try:
                now_dt = datetime.now(ZoneInfo("Asia/Taipei"))
                async with db_pool.acquire() as conn:
                    await conn.execute(UPSERT_SQL, 
                        stock_code, list_title, None, None, None,
                        None, None, url, STOCK_TIER, now_dt, now_dt.date(), now_dt.time()
                    )
                processed_count += 1
                print(f"   ✅ ({stock_code}) | {list_title[:10]}...")
            except Exception as e:
                print(f"   ❌ ({stock_code}) DB 錯誤: {e}")

        if processed_count > 0:
            print(f"💾 ({stock_code}) 完成，共存入 {processed_count} 則新聞")
        
        mark_stock_done(stock_code)

async def main():
    try:
        df = pd.read_csv(STOCK_FILE, dtype=str)
        all_stocks = df.iloc[:, 0].dropna().tolist()
        
        processed = load_processed_stocks()
        stock_list = [s for s in all_stocks if s not in processed]
        
        print(f"🐢 [{CRAWLER_TIER}] 爬蟲啟動（保守模式）")
        print(f"📄 總共 {len(all_stocks)} 支股票")
        print(f"✅ 已完成 {len(processed)} 支")
        print(f"🔄 待處理 {len(stock_list)} 支")
        print(f"⚙️  配置：並發={MAX_CONCURRENCY}")
        
        if len(stock_list) == 0:
            print("🎉 全部股票已處理完成！")
            return
    except Exception as e:
        print(f"❌ 讀取檔案失敗: {e}")
        return

    try:
        pool = await asyncpg.create_pool(PG_DSN)
        async with pool.acquire() as conn:
            await conn.execute(CREATE_TABLE_SQL)
    except Exception as e:
        print(f"❌ 資料庫連線失敗: {e}")
        return



    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    
    async with AsyncSession() as session:
        tasks = []
        random.shuffle(stock_list)
        
        for stock_code in stock_list:
            stock_code = str(stock_code).strip()
            if not stock_code: continue
            
            task = process_stock(sem, session, pool, stock_code)
            tasks.append(task)
        
        print(f"🐢 開始並行處理 {len(tasks)} 個任務 (最大並發: {MAX_CONCURRENCY})...\n")
        await asyncio.gather(*tasks)

    await pool.close()
    print(f"\n🎉 [{CRAWLER_TIER}] 全部處理完成！")

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    start_time = time.time()
    asyncio.run(main())
    print(f"⏱️ 總耗時: {time.time() - start_time:.2f} 秒")
