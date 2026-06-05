"""
一鍵遷移本機 PostgreSQL → Supabase
自動建 table + 複製資料
"""
import psycopg2
import psycopg2.extras

LOCAL_DSN = "postgresql://postgres:lab529@localhost:5432/postgres"
SUPABASE_DSN = "postgresql://postgres:QXnb3oWIiZrb9abV@db.lxtzmpdqzdvfwsdzrlje.supabase.co:5432/postgres"
SUPABASE_POOLER_DSN = "postgresql://postgres.lxtzmpdqzdvfwsdzrlje:QXnb3oWIiZrb9abV@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"

BATCH_SIZE = 500


def get_tables(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
        return [r[0] for r in cur.fetchall()]


def build_create_table(conn, table):
    """用 information_schema 重建 CREATE TABLE 語法"""
    with conn.cursor() as cur:
        # 取欄位定義
        cur.execute(
            """
            SELECT column_name, data_type, character_maximum_length,
                   numeric_precision, numeric_scale, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        cols = cur.fetchall()

        # 取約束 (PK / UNIQUE)
        cur.execute(
            """
            SELECT conname, pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = %s::regclass AND contype IN ('p', 'u')
            """,
            (f"public.{table}",),
        )
        constraints = cur.fetchall()

    col_defs = []
    for col_name, data_type, char_max, num_prec, num_scale, nullable, default in cols:
        # 判斷 serial
        if default and "nextval" in str(default):
            if data_type == "bigint":
                col_defs.append(f'  "{col_name}" BIGSERIAL')
            else:
                col_defs.append(f'  "{col_name}" SERIAL')
            continue

        # 對應型別
        type_map = {
            "character varying": f"VARCHAR({char_max})" if char_max else "TEXT",
            "text": "TEXT",
            "integer": "INTEGER",
            "bigint": "BIGINT",
            "boolean": "BOOLEAN",
            "timestamp with time zone": "TIMESTAMPTZ",
            "timestamp without time zone": "TIMESTAMP",
            "date": "DATE",
            "time without time zone": "TIME",
            "double precision": "DOUBLE PRECISION",
            "numeric": f"NUMERIC({num_prec},{num_scale})" if num_prec else "NUMERIC",
        }
        type_str = type_map.get(data_type, data_type.upper())

        col_def = f'  "{col_name}" {type_str}'
        if nullable == "NO":
            col_def += " NOT NULL"
        if default and default != "NULL" and "nextval" not in default:
            col_def += f" DEFAULT {default}"
        col_defs.append(col_def)

    constraint_defs = [f'  CONSTRAINT "{name}" {defn}' for name, defn in constraints]
    all_defs = col_defs + constraint_defs

    return (
        f'CREATE TABLE IF NOT EXISTS public."{table}" (\n'
        + ",\n".join(all_defs)
        + "\n);"
    )


def migrate_data(local_conn, supa_conn, table):
    with local_conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
        total = cur.fetchone()[0]

        if total == 0:
            print(f"  ⏭  空 table，跳過")
            return 0

        cur.execute(f'SELECT * FROM "{table}"')
        col_names = [desc[0] for desc in cur.description]
        cols_str = ",".join([f'"{c}"' for c in col_names])
        placeholders = ",".join(["%s"] * len(col_names))
        insert_sql = (
            f'INSERT INTO public."{table}" ({cols_str}) '
            f"VALUES ({placeholders}) ON CONFLICT DO NOTHING"
        )

        inserted = 0
        while True:
            rows = cur.fetchmany(BATCH_SIZE)
            if not rows:
                break
            with supa_conn.cursor() as sc:
                psycopg2.extras.execute_batch(sc, insert_sql, rows)
                supa_conn.commit()
            inserted += len(rows)
            print(f"  📦 {inserted}/{total} 筆")

        return inserted


def main():
    print("🔌 連線本機 PostgreSQL...")
    local = psycopg2.connect(LOCAL_DSN)

    print("🔌 連線 Supabase...")
    supa = None
    for dsn, label in [(SUPABASE_DSN, "direct"), (SUPABASE_POOLER_DSN, "pooler")]:
        try:
            supa = psycopg2.connect(dsn, connect_timeout=15)
            print(f"✅ Supabase 連線成功（{label}）！\n")
            break
        except Exception as e:
            print(f"  ⚠️  {label} 失敗：{e}")
    
    if not supa:
        print("❌ 所有連線方式都失敗")
        local.close()
        return

    tables = get_tables(local)
    print(f"📋 發現 {len(tables)} 個 tables：{tables}\n")

    for table in tables:
        print(f"{'='*50}")
        print(f"🚀 遷移：{table}")

        # 建 table
        try:
            create_sql = build_create_table(local, table)
            with supa.cursor() as sc:
                sc.execute(create_sql)
                supa.commit()
            print("  ✅ Table 建立成功")
        except Exception as e:
            print(f"  ⚠️  Table 建立警告（可能已存在）：{e}")
            supa.rollback()

        # 複製資料
        try:
            count = migrate_data(local, supa, table)
            print(f"  ✅ 完成，共 {count} 筆")
        except Exception as e:
            print(f"  ❌ 資料遷移失敗：{e}")
            supa.rollback()

    local.close()
    supa.close()
    print("\n🎉 全部遷移完成！")


if __name__ == "__main__":
    main()
