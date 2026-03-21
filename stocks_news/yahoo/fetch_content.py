import asyncio
import json
import random
import time
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from curl_cffi.requests import AsyncSession
import asyncpg
from bs4 import BeautifulSoup

# ================= 配置 =================
PG_DSN = "postgresql://postgres:lab529@localhost:5432/postgres"
MAX_CONCURRENCY = 5  # 並發數（提高速度）
DELAY_RANGE = (2.5, 5.0)  # 請求間延遲 (秒)（增加延遲）

# 股票分級對應天數過濾
TIER_DAYS_MAP = {
    'hot': 3,
    'mid': 7,
    'lower': 30,
    'rare': 30
}

WEEKDAY_ZH = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]

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

UPDATE_CONTENT_SQL = """
UPDATE public.yahoo_stock_news
SET content = $1, publisher = $2, reporter = $3, published_text = $4
WHERE id = $5;
"""

# ================= 核心函式 =================

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
    r = re.split(r"(台北|新北|台中|高雄|台南|桃園|新竹|綜合外電|外電|報導|電\\s*[）)])", r)[0]
    r = re.sub(r"(記者|特派|派駐|採訪|整理|編輯|專欄)", "", r)
    r = re.sub(r"[／/｜|：:\\[\\]【】\\(\\)\\s]", " ", r)
    r = re.sub(r"\\s+", " ", r).strip()
    if len(r) > 10 or len(r) < 2: return None
    return r

async def get_html_async(session: AsyncSession, url: str) -> str:
    """非同步取得 HTML"""
    retries = 3
    base_delay = 2.0
    
    for i in range(retries):
        try:
            response = await session.get(
                url, 
                headers=HEADERS, 
                timeout=20,
                impersonate="chrome131",
                allow_redirects=True
            )
            
            if response.status_code == 200:
                return response.text
            elif response.status_code in [403, 429]:
                if i < retries - 1:
                    delay = base_delay * (2 ** i) + random.uniform(0, 2)
                    print(f"[WARN] Status {response.status_code}, waiting {delay:.1f}s...")
                    await asyncio.sleep(delay)
                else:
                    return None
            else:
                return None
                
        except Exception as e:
            if i == retries - 1:
                print(f"[WARN] Request failed: {e}")
                return None
            delay = base_delay * (2 ** i) + random.uniform(0, 1)
            await asyncio.sleep(delay)
    
    return None

def parse_detail_page(html: str):
    """解析內頁"""
    if not html: return None
    soup = BeautifulSoup(html, "lxml")
    
    headline, publisher, reporter_jsonld, published_text, content, published_dt = None, None, None, None, None, None

    # JSON-LD
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string
        if not raw: continue
        try:
            data = json.loads(raw.strip())
            objs = data if isinstance(data, list) else [data]
            if isinstance(data, dict) and isinstance(data.get("@graph"), list): objs = data["@graph"]
            for obj in objs:
                if obj.get("@type") == "NewsArticle":
                    headline = obj.get("headline")
                    if isinstance(obj.get("provider"), dict): publisher = obj["provider"].get("name")
                    if isinstance(obj.get("author"), dict): reporter_jsonld = obj["author"].get("name")
                    iso_time = obj.get("datePublished") or obj.get("dateModified")
                    if iso_time: published_text, published_dt = format_time_yahoo_tw(iso_time)
                    break
        except: continue

    # Meta Backup
    if not reporter_jsonld:
        meta = soup.find("meta", attrs={"name": "description"})
        if meta:
            m = re.search(r"記者\\s*([^\\s／/】]+)", meta.get("content", ""))
            if m: reporter_jsonld = m.group(1).strip()

    reporter = strip_publisher_from_reporter(reporter_jsonld, publisher)
    
    content_div = soup.find("div", class_="caas-body")
    content = content_div.get_text(separator="\\n", strip=True) if content_div else ""
    if not content:
        ps = soup.find_all("p")
        if ps: content = "\\n".join([p.get_text(strip=True) for p in ps])

    return headline, publisher, reporter, published_text, content, published_dt

async def fetch_and_update_content(sem: asyncio.Semaphore, session: AsyncSession, db_pool: asyncpg.Pool, record: dict):
    """抓取並更新單篇新聞內文"""
    async with sem:
        news_id = record['id']
        url = record['url']
        stock_id = record['stock_id']
        stock_tier = record.get('stock_tier', 'hot')  # 預設為 hot
        days_filter = TIER_DAYS_MAP.get(stock_tier, 3)  # 預設 3 天
        
        await asyncio.sleep(random.uniform(*DELAY_RANGE))
        
        # 抓取 HTML
        html = await get_html_async(session, url)
        if not html:
            print(f"[SKIP] ({stock_id}) Failed to fetch: {url}")
            return
        
        # 提取內文
        detail = parse_detail_page(html)
        if not detail:
            print(f"[SKIP] ({stock_id}) No content found: {url}")
            return
        
        headline, publisher, reporter, published_text, content, pub_dt = detail
        
        # 日期過濾：根據股票分級決定天數
        if pub_dt:
            today_date = datetime.now(ZoneInfo("Asia/Taipei")).date()
            days_diff = (today_date - pub_dt.date()).days
            if days_diff > days_filter:
                print(f"[DELETE] ({stock_id}|{stock_tier}) Too old: {days_diff} days (limit: {days_filter})")
                # 刪除太舊的新聞
                try:
                    async with db_pool.acquire() as conn:
                        await conn.execute("DELETE FROM yahoo_stock_news WHERE id = $1", news_id)
                except Exception as e:
                    print(f"[ERROR] ({stock_id}) Failed to delete: {e}")
                return
        
        if not content:
            print(f"[SKIP] ({stock_id}) Empty content: {url}")
            return
        
        # 更新資料庫
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(UPDATE_CONTENT_SQL, content, publisher, reporter, published_text, news_id)
            print(f"[OK] ({stock_id}) Updated content ({len(content)} chars)")
        except Exception as e:
            print(f"[ERROR] ({stock_id}) Failed to update DB: {e}")

async def main():
    print("[START] Fetching news content...")
    
    # 1. 連接資料庫
    try:
        pool = await asyncpg.create_pool(PG_DSN)
    except Exception as e:
        print(f"[ERROR] Database connection failed: {e}")
        return
    
    # 2. 查詢需要補抓內文的新聞（包含 stock_tier）
    # 只查詢最近 30 天的新聞，避免浪費請求在太舊的新聞上
    async with pool.acquire() as conn:
        records = await conn.fetch("""
            SELECT id, stock_id, url, stock_tier
            FROM yahoo_stock_news
            WHERE content IS NULL
              AND fetched_at > NOW() - INTERVAL '30 days'
            ORDER BY fetched_at DESC
        """)
    
    total = len(records)
    print(f"Found {total} news items without content")
    
    if total == 0:
        print("[DONE] No news to process!")
        await pool.close()
        return
    
    # 3. 建立並發控制與 Session
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    
    async with AsyncSession() as session:
        tasks = []
        
        for record in records:
            task = fetch_and_update_content(sem, session, pool, record)
            tasks.append(task)
        
        print(f"\\n[START] Processing {len(tasks)} tasks (max concurrency: {MAX_CONCURRENCY})...\\n")
        await asyncio.gather(*tasks)
    
    await pool.close()
    print(f"\\n[DONE] Content fetching complete!")

if __name__ == "__main__":
    import os
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    start_time = time.time()
    asyncio.run(main())
    print(f"[TIME] Total: {time.time() - start_time:.2f} seconds")
