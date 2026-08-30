"""
cleanup_old_news.py
刪除本機 DB 中超過 90 天（3 個月）的舊新聞
"""
import asyncio
import asyncpg
from datetime import datetime, timedelta

PG_DSN = "postgresql://postgres:lab529@localhost:5432/postgres"
KEEP_DAYS = 90

TABLES = [
    ("yahoo_stock_news",  "fetched_date"),
    ("cnyes_stock_news",  "fetched_date"),
    ("nstock_stock_news", "fetched_date"),
]


async def cleanup():
    cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).date()
    print(f"清理 {cutoff} 以前的舊新聞（保留最近 {KEEP_DAYS} 天）\n")

    pool = await asyncpg.create_pool(PG_DSN)
    total = 0

    async with pool.acquire() as conn:
        for table, date_col in TABLES:
            count_before = await conn.fetchval(
                f'SELECT COUNT(*) FROM "{table}" WHERE "{date_col}" < $1', cutoff
            )
            if count_before == 0:
                print(f"  {table}: 無舊資料")
                continue

            await conn.execute(
                f'DELETE FROM "{table}" WHERE "{date_col}" < $1', cutoff
            )
            print(f"  {table}: 刪除 {count_before} 筆")
            total += count_before

    await pool.close()
    print(f"\n完成，共刪除 {total} 筆舊新聞")


if __name__ == "__main__":
    import os
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(cleanup())
