import asyncio
import asyncpg

PG_DSN = "postgresql://postgres:lab529@localhost:5432/postgres"

async def clear_nstock_news():
    print("連線到資料庫...")
    pool = await asyncpg.create_pool(PG_DSN)
    
    async with pool.acquire() as conn:
        # 查詢當前資料量
        count_before = await conn.fetchval("SELECT COUNT(*) FROM nstock_stock_news")
        print(f"\n目前資料庫中有 {count_before} 筆 NStock 新聞")
        
        confirm = input("\n確定要清空所有 NStock 新聞嗎? (yes/no): ")
        
        if confirm.lower() == 'yes':
            print("\n正在清空...")
            await conn.execute("DELETE FROM nstock_stock_news")
            count_after = await conn.fetchval("SELECT COUNT(*) FROM nstock_stock_news")
            print(f"✓ 已清空! 剩餘 {count_after} 筆")
        else:
            print("已取消")
    
    await pool.close()

if __name__ == "__main__":
    import os
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(clear_nstock_news())
