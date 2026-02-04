import asyncio
import json
import re
import os
import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from curl_cffi.requests import AsyncSession
import asyncpg
import pandas as pd
from bs4 import BeautifulSoup
from openai import AsyncOpenAI

# ================= 爬蟲等級配置 =================
CRAWLER_TIER = "中間股"  # 識別標記
STOCK_FILE = "../../stocks_category/股票代號_中間_v2.csv"
PROGRESS_FILE = "progress_cnyes_mid.txt"
DAYS_FILTER = 7  # 過濾天數：只抓 7 天內新聞
MAX_CONCURRENCY = 5  # 並發數

# 延遲範圍 (秒)
STOCK_DELAY_RANGE = (2.5, 5.0)  # 股票間延遲
NEWS_DELAY_RANGE = (1.2, 2.8)   # 新聞間延遲

# ================= 通用設定區 =================

# NVIDIA API Key
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")

# Postgres 設定
PG_DSN = "postgresql://postgres:lab529@localhost:5432/postgres"

# 模擬真實瀏覽器的完整 Headers
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
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

# SQL 語法
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.cnyes_stock_news (
  id BIGSERIAL PRIMARY KEY,
  news_id BIGINT NOT NULL,
  stock_id TEXT NOT NULL,
  title TEXT NOT NULL,
  category_name TEXT,
  category_id INTEGER,
  published_at TIMESTAMPTZ,
  content TEXT,
  sentiment_score INTEGER,
  url TEXT NOT NULL,
  fetched_at TIMESTAMPTZ,
  fetched_date DATE,
  fetched_time TIME,
  UNIQUE (stock_id, news_id)
);
"""

UPSERT_SQL = """
INSERT INTO public.cnyes_stock_news
(news_id, stock_id, title, category_name, category_id, published_at, content, sentiment_score, url, fetched_at, fetched_date, fetched_time)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
ON CONFLICT (stock_id, news_id) DO UPDATE SET
  title = EXCLUDED.title,
  category_name = EXCLUDED.category_name,
  category_id = EXCLUDED.category_id,
  published_at = EXCLUDED.published_at,
  content = EXCLUDED.content,
  sentiment_score = EXCLUDED.sentiment_score,
  url = EXCLUDED.url,
  fetched_at = EXCLUDED.fetched_at,
  fetched_date = EXCLUDED.fetched_date,
  fetched_time = EXCLUDED.fetched_time;
"""

# ================= 輔助函式區 =================

def extract_next_data(html: str):
    """從 HTML 提取 __NEXT_DATA__ JSON"""
    if not html:
        return None
    
    soup = BeautifulSoup(html, "lxml")
    
    for script in soup.find_all("script"):
        if script.string and "__NEXT_DATA__" in script.string:
            content = script.string.strip()
            
            # 使用正則提取 JSON
            match = re.search(r'__NEXT_DATA__\s*=\s*({.*?})\s*(?:;|$)', content, re.DOTALL)
            if not match:
                continue
            
            json_str = match.group(1)
            
            try:
                data = json.loads(json_str)
                return data
            except json.JSONDecodeError:
                continue
    
    return None

def mark_stock_done(stock_code: str):
    """記錄已完成的股票"""
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{stock_code}\n")

def load_processed_stocks():
    """載入已處理的股票清單"""
    if not os.path.exists(PROGRESS_FILE):
        return set()
    with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

# ================= 非同步核心區 =================

async def get_html_async(session: AsyncSession, url: str, referer: str = None) -> str:
    """非同步取得 HTML，使用 curl_cffi 模擬真實瀏覽器"""
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
                    print(f"[WARN] Status {response.status_code}, waiting {delay:.1f}s... ({url})")
                    await asyncio.sleep(delay)
                else:
                    print(f"[ERROR] Request denied ({url}): Status {response.status_code}")
                    return None
            else:
                response.raise_for_status()
                return response.text
                
        except Exception as e:
            if i == retries - 1:
                print(f"[WARN] Request failed ({url}): {e}")
                return None
            delay = base_delay * (2 ** i) + random.uniform(0, 1)
            await asyncio.sleep(delay)
    
    return None

async def get_json_async(session: AsyncSession, url: str) -> dict:
    """非同步取得 JSON API 回應"""
    retries = 3
    base_delay = 2.0
    
    for i in range(retries):
        try:
            headers = {**HEADERS, "Accept": "application/json"}
            
            response = await session.get(
                url,
                headers=headers,
                timeout=20,
                impersonate="chrome131",
                allow_redirects=True
            )
            
            if response.status_code == 200:
                return json.loads(response.text)
            elif response.status_code in [403, 429]:
                if i < retries - 1:
                    delay = base_delay * (2 ** i) + random.uniform(0, 2)
                    print(f"[WARN] API Status {response.status_code}, waiting {delay:.1f}s...")
                    await asyncio.sleep(delay)
                else:
                    print(f"[ERROR] API request denied: Status {response.status_code}")
                    return None
            else:
                return None
                
        except Exception as e:
            if i == retries - 1:
                print(f"[WARN] API request failed: {e}")
                return None
            delay = base_delay * (2 ** i) + random.uniform(0, 1)
            await asyncio.sleep(delay)
    
    return None

async def get_nvidia_sentiment_score_async(client: AsyncOpenAI, text: str) -> int:
    """非同步呼叫 NVIDIA/OpenAI API 進行情緒分析"""
    if not text or len(text) < 10:
        return None
    if not NVIDIA_API_KEY or "你的_NVIDIA" in NVIDIA_API_KEY:
        return None

    prompt = f"""
    你是一個專業的金融情緒分析師。請閱讀以下新聞文章內容，並給出一個 1 到 9 的情緒分數。
    評分標準：1分(極度負面) ~ 5分(中性) ~ 9分(極度正面)。
    請只回答一個數字 (1-9)，不要有任何解釋。
    文章內容：{text[:2000]}
    """
    try:
        completion = await client.chat.completions.create(
            model="meta/llama-3.1-8b-instruct",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=10, top_p=1
        )
        result = completion.choices[0].message.content.strip()
        match = re.search(r"(\d+)", result)
        if match:
            score = int(match.group(1))
            return max(1, min(9, score))
        return None
    except Exception as e:
        print(f"[WARN] Sentiment analysis failed: {e}")
        return None

async def process_stock(sem: asyncio.Semaphore, session: AsyncSession, db_pool: asyncpg.Pool, ai_client: AsyncOpenAI, stock_code: str):
    """處理單一股票的完整流程"""
    
    async with sem:
        # 決定 URL
        url = f"https://www.cnyes.com/twstock/{stock_code}/news/stock"
        
        await asyncio.sleep(random.uniform(*STOCK_DELAY_RANGE))
        
        # 1. 抓取第一頁
        html = await get_html_async(session, url)
        if not html:
            print(f"[SKIP] ({stock_code}) Failed to fetch page")
            mark_stock_done(stock_code)
            return
        
        # 2. 提取 __NEXT_DATA__
        next_data = extract_next_data(html)
        if not next_data:
            print(f"[SKIP] ({stock_code}) No __NEXT_DATA__ found")
            mark_stock_done(stock_code)
            return
        
        # 3. 解析新聞列表
        try:
            page_props = next_data.get("props", {}).get("pageProps", {})
            symbol_news = page_props.get("symbolNews", {})
            
            news_list = symbol_news.get("data", [])
            total = symbol_news.get("total", 0)
            last_page = symbol_news.get("last_page", 1)
            
            if not news_list:
                print(f"[SKIP] ({stock_code}) No news found")
                mark_stock_done(stock_code)
                return
            
            print(f"[START] ({stock_code}) Found {total} total news, {last_page} pages")
            
        except Exception as e:
            print(f"[ERROR] ({stock_code}) Failed to parse news data: {e}")
            mark_stock_done(stock_code)
            return
        
        # 4. 處理所有頁面的新聞
        all_news = news_list.copy()
        
        # 抓取後續頁面
        for page in range(2, last_page + 1):
            await asyncio.sleep(random.uniform(*NEWS_DELAY_RANGE))
            
            api_url = f"https://api.cnyes.com/media/api/v1/newslist/TWS:{stock_code}:STOCK/symbolNews?page={page}&limit=25"
            api_data = await get_json_async(session, api_url)
            
            if api_data and "items" in api_data:
                page_news = api_data["items"].get("data", [])
                all_news.extend(page_news)
                print(f"   Page {page}/{last_page}: +{len(page_news)} news")
        
        # 5. 過濾並儲存新聞
        today_date = datetime.now(ZoneInfo("Asia/Taipei")).date()
        processed_count = 0
        
        for news in all_news:
            try:
                news_id = news.get("newsId")
                title = news.get("title", "")
                category_name = news.get("categoryName")
                category_id = news.get("categoryId")
                publish_at_ts = news.get("publishAt")
                
                if not news_id or not title:
                    continue
                
                # 轉換時間戳
                if publish_at_ts:
                    published_at = datetime.fromtimestamp(publish_at_ts, tz=ZoneInfo("Asia/Taipei"))
                    days_diff = (today_date - published_at.date()).days
                    
                    # 日期過濾
                    if days_diff > DAYS_FILTER:
                        continue
                else:
                    published_at = None
                
                # 建立 URL
                news_url = f"https://news.cnyes.com/news/id/{news_id}"
                
                # 情緒分析先不做，之後用 analyze_sentiment.py 統一處理
                sentiment_score = None
                
                # 寫入資料庫
                now_dt = datetime.now(ZoneInfo("Asia/Taipei"))
                async with db_pool.acquire() as conn:
                    await conn.execute(
                        UPSERT_SQL,
                        news_id, stock_code, title, category_name, category_id,
                        published_at, None,  # content 暫時為 None
                        sentiment_score, news_url,
                        now_dt, now_dt.date(), now_dt.time()
                    )
                
                processed_count += 1
                
            except Exception as e:
                print(f"   [ERROR] ({stock_code}) Failed to process news {news.get('newsId')}: {e}")
        
        if processed_count > 0:
            print(f"[DONE] ({stock_code}) Saved {processed_count} news items")
        
        mark_stock_done(stock_code)

async def main():
    # 1. 讀取 CSV
    try:
        df = pd.read_csv(STOCK_FILE, dtype=str)
        all_stocks = df.iloc[:, 0].dropna().tolist()
        
        processed = load_processed_stocks()
        stock_list = [s for s in all_stocks if s not in processed]
        
        print(f"[{CRAWLER_TIER}] Crawler started")
        print(f"Total stocks: {len(all_stocks)}")
        print(f"Completed: {len(processed)}")
        print(f"Remaining: {len(stock_list)}")
        print(f"Config: concurrency={MAX_CONCURRENCY}, days_filter={DAYS_FILTER}")
        
        if len(stock_list) == 0:
            print("[DONE] All stocks processed!")
            return
    except Exception as e:
        print(f"[ERROR] Failed to read CSV: {e}")
        return

    # 2. 初始化資源
    try:
        pool = await asyncpg.create_pool(PG_DSN)
        async with pool.acquire() as conn:
            await conn.execute(CREATE_TABLE_SQL)
    except Exception as e:
        print(f"[ERROR] Database connection failed: {e}")
        return

    ai_client = AsyncOpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=NVIDIA_API_KEY)

    # 3. 建立並發控制與 Session
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    
    async with AsyncSession() as session:
        tasks = []
        random.shuffle(stock_list)
        
        for stock_code in stock_list:
            stock_code = str(stock_code).strip()
            if not stock_code:
                continue
            
            task = process_stock(sem, session, pool, ai_client, stock_code)
            tasks.append(task)
        
        print(f"\n[START] Processing {len(tasks)} tasks (max concurrency: {MAX_CONCURRENCY})...\n")
        await asyncio.gather(*tasks)

    await pool.close()
    print(f"\n[DONE] [{CRAWLER_TIER}] All processing complete!")

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    start_time = time.time()
    asyncio.run(main())
    print(f"[TIME] Total: {time.time() - start_time:.2f} seconds")
