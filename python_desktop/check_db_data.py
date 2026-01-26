import asyncio
import asyncpg
from datetime import datetime

PG_DSN = "postgresql://postgres:lab529@localhost:5432/postgres"

async def check_data():
    pool = await asyncpg.create_pool(PG_DSN)
    
    async with pool.acquire() as conn:
        # 檢查總數
        total = await conn.fetchval("SELECT COUNT(*) FROM yahoo_stock_news")
        stocks = await conn.fetchval("SELECT COUNT(DISTINCT stock_id) FROM yahoo_stock_news")
        print(f"[DATA] Total News: {total}")
        print(f"[DATA] Total Stocks: {stocks}\n")
        
        # 取樣資料
        print("=" * 80)
        print("[SAMPLE] News Data:")
        print("=" * 80)
        rows = await conn.fetch("""
            SELECT stock_id, title, publisher, sentiment_score, published_text, fetched_date
            FROM yahoo_stock_news 
            ORDER BY fetched_at DESC 
            LIMIT 5
        """)
        
        for row in rows:
            print(f"\n股票: {row['stock_id']}")
            print(f"標題: {row['title'][:50]}...")
            print(f"媒體: {row['publisher']}")
            print(f"情緒: {row['sentiment_score']}")
            print(f"發布: {row['published_text']}")
            print(f"抓取: {row['fetched_date']}")
        
        # 情緒分數統計
        print("\n" + "=" * 80)
        print("[SENTIMENT] Score Distribution:")
        print("=" * 80)
        sentiment_dist = await conn.fetch("""
            SELECT sentiment_score, COUNT(*) as count
            FROM yahoo_stock_news
            WHERE sentiment_score IS NOT NULL
            GROUP BY sentiment_score
            ORDER BY sentiment_score
        """)
        for row in sentiment_dist:
            print(f"分數 {row['sentiment_score']}: {row['count']} 則")
        
        # 最活躍股票
        print("\n" + "=" * 80)
        print("[TOP] Most Active Stocks:")
        print("=" * 80)
        top_stocks = await conn.fetch("""
            SELECT stock_id, COUNT(*) as news_count
            FROM yahoo_stock_news
            GROUP BY stock_id
            ORDER BY news_count DESC
            LIMIT 10
        """)
        for row in top_stocks:
            print(f"{row['stock_id']}: {row['news_count']} 則")
    
    await pool.close()

if __name__ == "__main__":
    asyncio.run(check_data())
