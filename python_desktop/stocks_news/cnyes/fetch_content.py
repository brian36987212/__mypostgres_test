import asyncio
import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from curl_cffi.requests import AsyncSession
import asyncpg
from bs4 import BeautifulSoup

# ================= 配置 =================
PG_DSN = "postgresql://postgres:lab529@localhost:5432/postgres"
MAX_CONCURRENCY = 10  # 並發數
DELAY_RANGE = (1.0, 2.5)  # 請求間延遲 (秒)

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
UPDATE public.cnyes_stock_news
SET content = $1
WHERE id = $2;
"""

# ================= 核心函式 =================

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

def extract_article_content(html: str) -> str:
    """從 HTML 提取新聞內文"""
    if not html:
        return None
    
    try:
        soup = BeautifulSoup(html, "lxml")
        
        # 鉅亨網的文章內容通常在特定的 div 中
        # 嘗試多種選擇器
        selectors = [
            "div._2E8y",  # 常見的文章容器
            "div[itemprop='articleBody']",
            "article",
            "div.article-body",
            "div.content",
        ]
        
        for selector in selectors:
            content_div = soup.select_one(selector)
            if content_div:
                # 移除 script 和 style 標籤
                for tag in content_div.find_all(['script', 'style', 'iframe']):
                    tag.decompose()
                
                # 取得純文字
                text = content_div.get_text(separator='\n', strip=True)
                
                # 清理多餘空白
                lines = [line.strip() for line in text.split('\n') if line.strip()]
                content = '\n'.join(lines)
                
                if len(content) > 50:  # 確保有實質內容
                    return content
        
        # 如果以上都找不到，嘗試取得所有段落
        paragraphs = soup.find_all('p')
        if paragraphs:
            text = '\n'.join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
            if len(text) > 50:
                return text
        
        return None
        
    except Exception as e:
        print(f"[ERROR] Failed to extract content: {e}")
        return None

async def fetch_and_update_content(sem: asyncio.Semaphore, session: AsyncSession, db_pool: asyncpg.Pool, record: dict):
    """抓取並更新單篇新聞內文"""
    async with sem:
        news_id = record['id']
        url = record['url']
        stock_id = record['stock_id']
        
        await asyncio.sleep(random.uniform(*DELAY_RANGE))
        
        # 抓取 HTML
        html = await get_html_async(session, url)
        if not html:
            print(f"[SKIP] ({stock_id}) Failed to fetch: {url}")
            return
        
        # 提取內文
        content = extract_article_content(html)
        if not content:
            print(f"[SKIP] ({stock_id}) No content found: {url}")
            return
        
        # 更新資料庫
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(UPDATE_CONTENT_SQL, content, news_id)
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
    
    # 2. 查詢需要補抓內文的新聞
    async with pool.acquire() as conn:
        records = await conn.fetch("""
            SELECT id, stock_id, url
            FROM cnyes_stock_news
            WHERE content IS NULL
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
        
        print(f"\n[START] Processing {len(tasks)} tasks (max concurrency: {MAX_CONCURRENCY})...\n")
        await asyncio.gather(*tasks)
    
    await pool.close()
    print(f"\n[DONE] Content fetching complete!")

if __name__ == "__main__":
    import os
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    start_time = time.time()
    asyncio.run(main())
    print(f"[TIME] Total: {time.time() - start_time:.2f} seconds")
