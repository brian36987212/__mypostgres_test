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
CRAWLER_TIER = "熱門股"
STOCK_FILE = "../../stocks_category/股票代號_熱門_v2.csv"
PROGRESS_FILE = "progress_nstock_hot.txt"
DAYS_FILTER = 3  # 只抓 3 天內新聞
MAX_CONCURRENCY = 5

# 延遲範圍 (秒)
STOCK_DELAY_RANGE = (2.5, 5.0)

# ================= 通用設定區 =================

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
CREATE TABLE IF NOT EXISTS public.nstock_stock_news (
  id BIGSERIAL PRIMARY KEY,
  news_id TEXT NOT NULL,
  stock_id TEXT NOT NULL,
  title TEXT NOT NULL,
  category TEXT,
  published_at TIMESTAMPTZ,
  content TEXT,
  sentiment_score INTEGER,
  url TEXT NOT NULL,
  stock_tier TEXT,
  related_stocks TEXT,
  fetched_at TIMESTAMPTZ,
  fetched_date DATE,
  fetched_time TIME,
  UNIQUE (stock_id, news_id)
);

CREATE INDEX IF NOT EXISTS idx_nstock_stock_id ON nstock_stock_news(stock_id);
CREATE INDEX IF NOT EXISTS idx_nstock_published_at ON nstock_stock_news(published_at);
CREATE INDEX IF NOT EXISTS idx_nstock_content_null ON nstock_stock_news(stock_id) WHERE content IS NULL;
CREATE INDEX IF NOT EXISTS idx_nstock_sentiment_null ON nstock_stock_news(stock_id) WHERE sentiment_score IS NULL AND content IS NOT NULL;
"""

UPSERT_SQL = """
INSERT INTO public.nstock_stock_news
(news_id, stock_id, title, category, published_at, url, stock_tier, related_stocks, fetched_at, fetched_date, fetched_time)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
ON CONFLICT (stock_id, news_id) DO UPDATE SET
  title = EXCLUDED.title,
  category = EXCLUDED.category,
  published_at = EXCLUDED.published_at,
  url = EXCLUDED.url,
  stock_tier = EXCLUDED.stock_tier,
  related_stocks = EXCLUDED.related_stocks,
  fetched_at = EXCLUDED.fetched_at,
  fetched_date = EXCLUDED.fetched_date,
  fetched_time = EXCLUDED.fetched_time;
"""

# ================= 輔助函式區 =================

def extract_nuxt_data(html: str):
    """從 HTML 提取 window.__NUXT__ 資料"""
    if not html:
        return None
    
    soup = BeautifulSoup(html, "lxml")
    
    for script in soup.find_all('script'):
        script_content = script.string
        if script_content and 'window.__NUXT__' in script_content:
            return script_content
    
    return None


def parse_news_from_nuxt(script_content: str, stock_id: str, days_filter: int):
    """從 NUXT script 內容中提取新聞資料"""
    if not script_content:
        return []
    
    news_list = []
    cutoff_date = datetime.now(ZoneInfo("Asia/Taipei")) - timedelta(days=days_filter)
    
    # 找到 news:[ 開始的區塊
    news_match = re.search(r'news:\[(.*?)\]', script_content, re.DOTALL)
    if not news_match:
        return []
    
    news_block = news_match.group(1)
    
    # 由於 NUXT 資料中某些屬性（如 date）可能會被壓縮為變數（例如 date:u），
    # 舊的貪婪/正則寫法會導致解析邊界失效並把整串物件都吃進 title 裡。
    # 更安全的做法是將字串依照 '},{' 拆分成單獨的新聞物件，然後再針對每個物件抓取屬性。
    items = re.split(r'\},\{', news_block)
    
    for item in items:
        try:
            id_match = re.search(r'id:"([^"]+)"', item)
            # 針對 title，非貪婪匹配直到遇到下一個屬性或引號結束
            title_match = re.search(r'title:"(.*?)"(?:,link:|,source:|,image:|,date:|,stocks:|,category:)', item)
            if not title_match:
                title_match = re.search(r'title:"(.*?)"', item)
                
            link_match = re.search(r'link:"([^"]+)"', item)
            
            if not (id_match and title_match and link_match):
                continue
                
            news_id = id_match.group(1)
            title = title_match.group(1)
            # 如果因為拆分或正則的問題導致結尾多包含了一個雙引號，把它清掉
            if title.endswith('"'):
                title = title[:-1]
                
            # 將 JSON 轉義的斜線 \u002F 還原為 /
            title = title.replace('\\u002F', '/')
                
            link = link_match.group(1).replace('\\u002F', '/')
            
            category_match = re.search(r'category:(?:"([^"]*)"|([a-z]))', item)
            category = ""
            if category_match:
                category = category_match.group(1) if category_match.group(1) is not None else f"VAR_{category_match.group(2)}"
                
            stocks_match = re.search(r'stocks:(?:"([^"]+)"|([a-z]))', item)
            related_stocks = ""
            if stocks_match:
                related_stocks = stocks_match.group(1) if stocks_match.group(1) else f"{stock_id}(TW)"
            else:
                related_stocks = f"{stock_id}(TW)"
                
            # 解析發布時間
            published_at = None
            date_match = re.search(r'date:"([^"]+)"', item)
            if date_match:
                try:
                    published_at = datetime.strptime(date_match.group(1), "%Y-%m-%d %H:%M:%S")
                    published_at = published_at.replace(tzinfo=ZoneInfo("Asia/Taipei"))
                except:
                    pass
            
            # 如果無法解析出確切時間 (例如 date:u 變數)，暫時給予當下時間作為 fallback
            if not published_at:
                published_at = datetime.now(ZoneInfo("Asia/Taipei"))
                
            # 過濾日期
            if published_at < cutoff_date:
                continue
            
            news_list.append({
                "news_id": news_id,
                "stock_id": stock_id,
                "title": title,
                "category": category,
                "published_at": published_at,
                "url": link,
                "related_stocks": related_stocks,
            })
            
        except Exception as e:
            print(f"  [WARN] 解析新聞項目失敗: {e}")
            continue
    
    return news_list


async def fetch_stock_news(session: AsyncSession, stock_id: str, semaphore: asyncio.Semaphore, days_filter: int = 3):
    """抓取單支股票的新聞"""
    async with semaphore:
        url = f"https://www.nstock.tw/stock_info?stock_id={stock_id}"
        
        try:
            response = await session.get(
                url,
                headers=HEADERS,
                timeout=20,
                impersonate="chrome131",
                allow_redirects=True
            )
            
            if response.status_code != 200:
                print(f"  [X] {stock_id}: HTTP {response.status_code}")
                return []
            
            # 提取 NUXT 資料
            nuxt_script = extract_nuxt_data(response.text)
            if not nuxt_script:
                print(f"  [WARN] {stock_id}: 未找到 NUXT 資料")
                return []
            
            # 解析新聞
            news_list = parse_news_from_nuxt(nuxt_script, stock_id, days_filter)
            
            if news_list:
                print(f"  [OK] {stock_id}: 找到 {len(news_list)} 筆新聞")
            else:
                print(f"  [-] {stock_id}: 無符合條件的新聞")
            
            return news_list
            
        except Exception as e:
            print(f"  [X] {stock_id}: {e}")
            return []


async def save_news_to_db(pool: asyncpg.Pool, news_list: list):
    """儲存新聞到資料庫"""
    if not news_list:
        return 0
    
    async with pool.acquire() as conn:
        saved_count = 0
        for news in news_list:
            try:
                now = datetime.now(ZoneInfo("Asia/Taipei"))
                
                await conn.execute(
                    UPSERT_SQL,
                    news["news_id"],
                    news["stock_id"],
                    news["title"],
                    news["category"],
                    news["published_at"],
                    news["url"],
                    CRAWLER_TIER,
                    news["related_stocks"],
                    now,
                    now.date(),
                    now.time(),
                )
                saved_count += 1
            except Exception as e:
                print(f"    [WARN] 儲存失敗: {e}")
                continue
        
        return saved_count


def load_progress(progress_file: str = None):
    """載入進度"""
    if progress_file is None:
        progress_file = PROGRESS_FILE
    
    if os.path.exists(progress_file):
        with open(progress_file, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def save_progress(stock_id: str, progress_file: str = None):
    """儲存進度"""
    if progress_file is None:
        progress_file = PROGRESS_FILE
    
    with open(progress_file, "a", encoding="utf-8") as f:
        f.write(f"{stock_id}\n")


# ================= 主程式區 =================

async def main():
    # Windows 系統設定
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    print(f"\n{'='*60}")
    print(f"NStock 股市新聞爬蟲 - {CRAWLER_TIER}")
    print(f"{'='*60}\n")
    
    # 讀取股票清單
    try:
        df = pd.read_csv(STOCK_FILE, dtype=str)
        stock_ids = df.iloc[:, 0].tolist()
        print(f"[INFO] 讀取股票清單: {len(stock_ids)} 支股票\n")
    except Exception as e:
        print(f"[ERROR] 讀取股票清單失敗: {e}")
        return
    
    # 載入進度
    processed = load_progress()
    remaining = [s for s in stock_ids if s not in processed]
    
    if not remaining:
        print("[OK] 所有股票已處理完畢")
        return
    
    print(f"[INFO] 已處理: {len(processed)} 支")
    print(f"[INFO] 待處理: {len(remaining)} 支\n")
    
    # 建立資料庫連線池
    try:
        pool = await asyncpg.create_pool(PG_DSN, min_size=2, max_size=10)
        async with pool.acquire() as conn:
            await conn.execute(CREATE_TABLE_SQL)
        print("[OK] 資料庫連線成功\n")
    except Exception as e:
        print(f"[ERROR] 資料庫連線失敗: {e}")
        return
    
    # 開始爬取
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    total_news = 0
    start_time = time.time()
    
    async with AsyncSession() as session:
        for i, stock_id in enumerate(remaining, 1):
            print(f"[{i}/{len(remaining)}] 處理 {stock_id}...")
            
            # 抓取新聞
            news_list = await fetch_stock_news(session, stock_id, semaphore, DAYS_FILTER)
            
            # 儲存到資料庫
            if news_list:
                saved = await save_news_to_db(pool, news_list)
                total_news += saved
            
            # 記錄進度
            save_progress(stock_id)
            
            # 延遲
            if i < len(remaining):
                delay = random.uniform(*STOCK_DELAY_RANGE)
                await asyncio.sleep(delay)
    
    # 關閉連線池
    await pool.close()
    
    # 統計
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
