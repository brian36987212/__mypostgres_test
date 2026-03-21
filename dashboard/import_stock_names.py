import asyncio
import asyncpg
import pandas as pd
import os

# 設定
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STOCK_FILE = os.path.join(os.path.dirname(SCRIPT_DIR), "股票代號.xlsx")
PG_DSN = "postgresql://postgres:lab529@localhost:5432/postgres"

# 建立 stock_mapping 資料表的 SQL
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stock_mapping (
  stock_id TEXT PRIMARY KEY,
  stock_name TEXT NOT NULL
);
"""

# UPSERT SQL
UPSERT_SQL = """
INSERT INTO stock_mapping (stock_id, stock_name)
VALUES ($1, $2)
ON CONFLICT (stock_id) DO UPDATE SET
  stock_name = EXCLUDED.stock_name;
"""

async def main():
    print("讀取股票代號檔案...")
    try:
        df = pd.read_excel(STOCK_FILE, dtype=str)
        print(f"成功讀取到 {len(df)} 檔股票")
        
        # 檢查欄位名稱
        if len(df.columns) < 2:
            print("錯誤: Excel 檔案格式錯誤，需要至少兩欄（股票代號、股票名稱）")
            return
        
        # 取得第一欄和第二欄
        stock_id_col = df.columns[0]
        stock_name_col = df.columns[1]
        
        print(f"欄位: [{stock_id_col}] 和 [{stock_name_col}]")
        
    except Exception as e:
        print(f"錯誤: 讀取檔案失敗: {e}")
        return
    
    print("\n連接資料庫...")
    try:
        pool = await asyncpg.create_pool(PG_DSN)
        print("資料庫連接成功")
        
        # 建立資料表
        print("\n建立 stock_mapping 資料表...")
        async with pool.acquire() as conn:
            await conn.execute(CREATE_TABLE_SQL)
        print("資料表建立完成")
        
        # 匯入資料
        print(f"\n開始匯入 {len(df)} 筆資料...")
        imported_count = 0
        
        async with pool.acquire() as conn:
            for idx, row in df.iterrows():
                stock_id = str(row[stock_id_col]).strip()
                stock_name = str(row[stock_name_col]).strip()
                
                if not stock_id or stock_id == 'nan':
                    continue
                
                try:
                    await conn.execute(UPSERT_SQL, stock_id, stock_name)
                    imported_count += 1
                    
                    if imported_count % 100 == 0:
                        print(f"   進度: {imported_count}/{len(df)}")
                        
                except Exception as e:
                    print(f"   警告: 匯入失敗 ({stock_id}): {e}")
        
        print(f"\n匯入完成！共匯入 {imported_count} 筆資料")
        
        # 驗證
        async with pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM stock_mapping")
            print(f"資料表總筆數: {total}")
            
            # 顯示前 5 筆
            rows = await conn.fetch("SELECT * FROM stock_mapping LIMIT 5")
            print("\n前 5 筆資料:")
            for row in rows:
                print(f"  {row['stock_id']}: {row['stock_name']}")
        
        await pool.close()
        
    except Exception as e:
        print(f"錯誤: 資料庫操作失敗: {e}")
        return

if __name__ == "__main__":
    asyncio.run(main())

