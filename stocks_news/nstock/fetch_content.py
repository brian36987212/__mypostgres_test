import asyncio
import os
import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from curl_cffi.requests import AsyncSession
import asyncpg
from bs4 import BeautifulSoup

# ================= 配置 =================
PG_DSN = "postgresql://postgres:lab529@localhost:5432/postgres"
MAX_CONCURRENCY = 5
DELAY_RANGE = (2.5, 5.0)

# 股票分級對應天數過濾
TIER_DAYS_MAP = {
    '熱門股': 3,
    '中型股': 7,
    '低流動性股': 30,
    '冷門股': 30
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}

UPDATE_CONTENT_SQL = """
UPDATE public.nstock_stock_news
SET content = $1
WHERE id = $2;
"""

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
                    print(f"  [WARN] HTTP {response.status_code}, 等待 {delay:.1f}s...")
                    await asyncio.sleep(delay)
            else:
                return None
                
        except Exception as e:
            if i == retries - 1:
                print(f"  [X] 請求失敗: {e}")
                return None
            await asyncio.sleep(base_delay * (2 ** i))
    
    return None


def parse_detail_page(html: str):
    """解析新聞詳情頁"""
    if not html:
        return None
    
    soup = BeautifulSoup(html, "lxml")
    
    # NStock 新聞內容在 class 包含 'nstock-content' 的 div 中
    content_div = soup.find("div", class_=lambda x: x and 'nstock-content' in x)
    
    if not content_div:
        # 備用: 嘗試找 article 標籤
        content_div = soup.find("article")
    
    if not content_div:
        # 最後備用: 收集所有 p 標籤
        paragraphs = soup.find_all("p")
        if paragraphs:
            content = "\n".join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
            if len(content) > 100:
                return content
        return None
    
    # 移除不相關元素
    for tag in content_div.find_all(["script", "style", "iframe", "ins", "nav", "header", "footer"]):
        tag.decompose()
    
    # 移除特定 class (廣告、導航等)
    for class_name in ['multiselect__content-wrapper', 'ad', 'advertisement']:
        for tag in content_div.find_all(class_=lambda x: x and class_name in str(x).lower()):
            tag.decompose()
    
    # 提取文字內容
    paragraphs = content_div.find_all("p")
    if paragraphs:
        content = "\n".join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
    else:
        content = content_div.get_text(separator="\n", strip=True)
    
    # 檢查內容長度 (降低門檻,有些營收新聞很短)
    if len(content) > 10:
        return content
    
    # 如果完全沒內容,返回特殊標記
    return None


async def fetch_and_update_content(sem: asyncio.Semaphore, session: AsyncSession, db_pool: asyncpg.Pool, record: dict):
    """抓取並更新單篇新聞內文"""
    async with sem:
        news_id = record['id']
        url = record['url']
        stock_id = record['stock_id']
        stock_tier = record.get('stock_tier', '熱門股')
        published_at = record.get('published_at')
        
        days_filter = TIER_DAYS_MAP.get(stock_tier, 3)
        
        await asyncio.sleep(random.uniform(*DELAY_RANGE))
        
        # 日期過濾
        if published_at:
            today_date = datetime.now(ZoneInfo("Asia/Taipei")).date()
            days_diff = (today_date - published_at.date()).days
            if days_diff > days_filter:
                print(f"  [DEL] ({stock_id}|{stock_tier}) 太舊: {days_diff} 天")
                try:
                    async with db_pool.acquire() as conn:
                        await conn.execute("DELETE FROM nstock_stock_news WHERE id = $1", news_id)
                except Exception as e:
                    print(f"  [X] ({stock_id}) 刪除失敗: {e}")
                return
        
        # 抓取 HTML
        html = await get_html_async(session, url)
        if not html:
            print(f"  [X] ({stock_id}) 抓取失敗")
            return
        
        # 提取內文
        content = parse_detail_page(html)
        if not content:
            # 某些新聞可能沒有內文(如 article_m),標記為空字串
            async with db_pool.acquire() as conn:
                await conn.execute(UPDATE_CONTENT_SQL, "", news_id)
            print(f"  [SKIP] ({stock_id}) 無實際內文,已標記")
            return
        
        # 更新資料庫
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(UPDATE_CONTENT_SQL, content, news_id)
            print(f"  [OK] ({stock_id}) 更新成功 ({len(content)} 字)")
        except Exception as e:
            print(f"  [X] ({stock_id}) 更新失敗: {e}")


async def main():
    print(f"\n{'='*60}")
    print("NStock 新聞內文補抓")
    print(f"{'='*60}\n")
    
    try:
        pool = await asyncpg.create_pool(PG_DSN)
        print("[OK] 資料庫連線成功\n")
    except Exception as e:
        print(f"[ERROR] 資料庫連線失敗: {e}")
        return
    
    async with pool.acquire() as conn:
        records = await conn.fetch("""
            SELECT id, stock_id, url, stock_tier, published_at
            FROM nstock_stock_news
            WHERE content IS NULL
              AND fetched_at > NOW() - INTERVAL '30 days'
            ORDER BY published_at DESC
        """)
    
    total = len(records)
    print(f"[INFO] 找到 {total} 筆需要補抓的新聞\n")
    
    if total == 0:
        print("[OK] 沒有需要處理的新聞")
        await pool.close()
        return
    
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    start_time = time.time()
    
    async with AsyncSession() as session:
        tasks = []
        for record in records:
            task = fetch_and_update_content(sem, session, pool, record)
            tasks.append(task)
        
        print(f"開始處理 {len(tasks)} 筆新聞 (並發數: {MAX_CONCURRENCY})...\n")
        await asyncio.gather(*tasks)
    
    await pool.close()
    
    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"[OK] 內文補抓完成!")
    print(f"{'='*60}")
    print(f"處理新聞: {total} 筆")
    print(f"耗時: {elapsed:.1f} 秒")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    import os
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(main())
