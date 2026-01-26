import pandas as pd
import psycopg2
import os
from datetime import datetime, date

# ================= 設定區 =================
STOCK_FILE = "股票代號.xlsx"
PG_DSN = "host=localhost port=5432 dbname=postgres user=postgres password=lab529"
# =========================================

def main():
    today = date.today()
    print(f"🕵️‍♂️ 正在檢查 {today} 的爬蟲結果覆蓋率...")

    # 1. 讀取原本的 Excel 清單 (原本要抓的所有股票)
    try:
        if STOCK_FILE.endswith(".xlsx"):
            df = pd.read_excel(STOCK_FILE, dtype=str)
        else:
            df = pd.read_csv(STOCK_FILE, dtype=str)
        
        # 轉成 Python 的集合 (Set)，方便做數學運算
        # 假設股票代號在第一欄，去除空白
        all_stocks_set = set(df.iloc[:, 0].dropna().astype(str).str.strip().tolist())
        print(f"📄 原始清單共有: {len(all_stocks_set)} 檔股票")
        
    except Exception as e:
        print(f"❌ 讀取 Excel 失敗: {e}")
        return

    # 2. 查詢資料庫 (找出今天有成功存入新聞的股票)
    found_stocks_set = set()
    try:
        conn = psycopg2.connect(PG_DSN)
        cur = conn.cursor()
        
        # SQL 邏輯：找出 'fetched_date' 是今天，且 stock_id 不重複的清單
        # 如果你想檢查的是「過去30天有無新聞」，可以把 WHERE 條件改掉
        sql = """
            SELECT DISTINCT stock_id 
            FROM public.yahoo_stock_news 
            WHERE fetched_date >= CURRENT_DATE - INTERVAL '30 days'
        """
        cur.execute(sql, (today,))
        rows = cur.fetchall()
        
        for row in rows:
            found_stocks_set.add(row[0].strip())
            
        print(f"💾 資料庫顯示今日有新聞的股票: {len(found_stocks_set)} 檔")
        
    except Exception as e:
        print(f"❌ 資料庫查詢失敗: {e}")
        return
    finally:
        if conn: conn.close()

    # 3. 進行比對 (集合減法)
    # 沒新聞 = 全部 - 有新聞
    no_news_stocks = all_stocks_set - found_stocks_set
    
    # 排序一下比較好閱讀
    no_news_list = sorted(list(no_news_stocks))
    
    print(f"📉 今日無新聞 (或未抓取) 的股票: {len(no_news_list)} 檔")

    # 4. 輸出結果檔案
    if no_news_list:
        output_file = f"no_stocknews_company_{today}.csv"
        
        # 轉成 DataFrame 存檔
        out_df = pd.DataFrame(no_news_list, columns=["Stock_ID"])
        # 增加一欄備註，方便你後續人工檢查
        out_df["Status"] = "無今日新聞"
        
        out_df.to_csv(output_file, index=False, encoding="utf-8-sig")
        print(f"✅ 已將清單存為: {output_file}")
        
        # 顯示前 10 筆看看
        print("👀 範例 (前10筆):", no_news_list[:10])
    else:
        print("🎉 太神奇了！所有股票今天都有新聞！")

if __name__ == "__main__":
    main()