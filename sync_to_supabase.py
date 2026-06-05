"""
sync_to_supabase.py
每天執行一次：
1. 把本機 DB 最近 30 天的新聞同步到 Supabase
2. 清理 Supabase 中超過 30 天的舊資料
確保 Supabase 永遠只存近 30 天，不超免費上限
"""
import os
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

LOCAL_DSN = "postgresql://postgres:lab529@localhost:5432/postgres"
SUPABASE_DSN = os.getenv("DATABASE_URL", "")

KEEP_DAYS = 30
BATCH_SIZE = 500

# 各 table 的日期欄位
TABLES = {
    "yahoo_stock_news":  "fetched_date",
    "cnyes_stock_news":  "fetched_date",
    "nstock_stock_news": "fetched_date",
    "stock_mapping":     None,  # 靜態資料，全量同步
}


def sync_table(local: psycopg2.extensions.connection,
               supa: psycopg2.extensions.connection,
               table: str,
               date_col: str | None,
               since_date):
    with local.cursor() as cur:
        if date_col:
            cur.execute(f'SELECT * FROM "{table}" WHERE "{date_col}" >= %s', (since_date,))
        else:
            cur.execute(f'SELECT * FROM "{table}"')

        cols = [desc[0] for desc in cur.description]
        rows = cur.fetchall()

    if not rows:
        print(f"  ⏭  {table}：無新資料")
        return 0

    cols_str = ",".join([f'"{c}"' for c in cols])
    placeholders = ",".join(["%s"] * len(cols))
    insert_sql = (
        f'INSERT INTO public."{table}" ({cols_str}) '
        f"VALUES ({placeholders}) ON CONFLICT DO NOTHING"
    )

    inserted = 0
    with supa.cursor() as sc:
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            psycopg2.extras.execute_batch(sc, insert_sql, batch)
            inserted += len(batch)
        supa.commit()

    return inserted


def cleanup_table(supa: psycopg2.extensions.connection,
                  table: str,
                  date_col: str,
                  cutoff_date):
    with supa.cursor() as sc:
        sc.execute(f'DELETE FROM public."{table}" WHERE "{date_col}" < %s', (cutoff_date,))
        deleted = sc.rowcount
        supa.commit()
    return deleted


def main():
    if not SUPABASE_DSN:
        print("❌ DATABASE_URL 未設定，請在 .env 加入 Supabase 的連線字串")
        return

    since_date = (datetime.now() - timedelta(days=KEEP_DAYS)).date()
    cutoff_date = since_date  # 相同日期：同步 30 天內，刪除 30 天外

    print(f"📅 同步範圍：{since_date} 至今（最近 {KEEP_DAYS} 天）")
    print(f"🗑  清理範圍：{cutoff_date} 以前的資料\n")

    print("🔌 連線本機 DB...")
    local = psycopg2.connect(LOCAL_DSN)

    print("🔌 連線 Supabase...")
    try:
        supa = psycopg2.connect(SUPABASE_DSN, connect_timeout=15)
        print("✅ 連線成功\n")
    except Exception as e:
        print(f"❌ Supabase 連線失敗：{e}")
        local.close()
        return

    total_synced = 0
    total_deleted = 0

    for table, date_col in TABLES.items():
        print(f"{'='*50}")
        print(f"📤 同步：{table}")

        # 1. 同步新資料
        try:
            n = sync_table(local, supa, table, date_col, since_date)
            print(f"  ✅ 同步 {n} 筆")
            total_synced += n
        except Exception as e:
            print(f"  ❌ 同步失敗：{e}")
            supa.rollback()

        # 2. 清理舊資料
        if date_col:
            try:
                d = cleanup_table(supa, table, date_col, cutoff_date)
                print(f"  🗑  清理 {d} 筆舊資料")
                total_deleted += d
            except Exception as e:
                print(f"  ❌ 清理失敗：{e}")
                supa.rollback()

    local.close()
    supa.close()

    print(f"\n{'='*50}")
    print(f"✅ 完成！同步 {total_synced} 筆，清理 {total_deleted} 筆")
    print(f"   Supabase 現在只保留最近 {KEEP_DAYS} 天的資料")


if __name__ == "__main__":
    main()
