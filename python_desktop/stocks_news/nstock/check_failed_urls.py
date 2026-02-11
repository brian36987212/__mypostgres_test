import asyncio
import asyncpg
from collections import Counter

PG_DSN = "postgresql://postgres:lab529@localhost:5432/postgres"

async def check_failed_urls():
    pool = await asyncpg.create_pool(PG_DSN)
    
    # 查詢沒有內文的新聞
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT url 
            FROM nstock_stock_news 
            WHERE content IS NULL
            LIMIT 20
        """)
    
    urls = [row['url'] for row in rows]
    
    print(f"找到 {len(urls)} 個沒有內文的 URL:\n")
    
    # 分析 URL 類型
    article_c = [u for u in urls if 'article_c' in u]
    article_m = [u for u in urls if 'article_m' in u]
    
    print(f"article_c (外部新聞): {len(article_c)}")
    print(f"article_m (內部新聞): {len(article_m)}\n")
    
    print("範例 URL:")
    for url in urls[:5]:
        print(f"  {url}")
    
    await pool.close()
    
    return urls

if __name__ == "__main__":
    import os
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    urls = asyncio.run(check_failed_urls())
