"""
第三階段：計算 Dashboard 用的三大分數指標 + 熱門議題 Top N

- 市場情緒分數：近 SENTIMENT_WINDOW_DAYS 天平均情緒分數（1-9 → 1-10）
- 題材熱度分數：熱門題材（來自 stage2 的詞頻趨勢統計）的平均熱度分
- 新聞動能分數：今日新聞量 / 近 MOMENTUM_BASE_DAYS 天日均量
- 熱門議題 Top N：重用 stage2 的統計結果，取前 N 名寫入 DB 供 Dashboard 顯示

前置條件：先執行 stage1_keyword_extract.py（題材熱度/熱門議題需要 keywords 欄位）
執行：python stage3_compute_dashboard_metrics.py
"""

import asyncio
from datetime import date, timedelta

import asyncpg

from stage2_theme_trends import (
    PG_DSN,
    get_data_max_date,
    fetch_keyword_rows,
    build_stats,
    classify,
)

SENTIMENT_WINDOW_DAYS = 3   # 市場情緒分數的近期視窗
MOMENTUM_BASE_DAYS    = 7   # 新聞動能分數的基準視窗（不含今日）
TOP_N_TOPICS          = 5   # 寫入 DB 的熱門議題數（Dashboard 目前只顯示前 3）


# ═══════════════════════════════════════════════════════════════
# 資料表
# ═══════════════════════════════════════════════════════════════
async def ensure_tables(conn: asyncpg.Connection) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_indicators (
            id                      SERIAL PRIMARY KEY,
            computed_date           DATE NOT NULL,
            market_sentiment_score  NUMERIC(4,1),
            market_sentiment_label  VARCHAR(20),
            topic_heat_score        NUMERIC(4,1),
            topic_heat_label        VARCHAR(20),
            news_momentum_score     NUMERIC(4,1),
            news_momentum_label     VARCHAR(20),
            updated_at              TIMESTAMPTZ DEFAULT now()
        );
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS theme_daily_summary (
            id             SERIAL PRIMARY KEY,
            computed_date  DATE NOT NULL,
            rank           INTEGER,
            keyword        VARCHAR(50),
            category       VARCHAR(30),
            recent_count   INTEGER,
            trend_ratio    NUMERIC(6,2),
            avg_sentiment  NUMERIC(4,1),
            hotness        NUMERIC(6,1),
            sources        INTEGER
        );
    """)


async def get_max_fetched_date(conn: asyncpg.Connection) -> date:
    """取三個新聞表中最新的 fetched_date，作為「今天」的基準（避免爬蟲停跑期間空跑）"""
    row = await conn.fetchrow("""
        SELECT MAX(d) AS max_date FROM (
            SELECT MAX(fetched_date) AS d FROM yahoo_stock_news
            UNION ALL
            SELECT MAX(fetched_date)        FROM cnyes_stock_news
            UNION ALL
            SELECT MAX(fetched_date)        FROM nstock_stock_news
        ) t
    """)
    return row["max_date"]


def clamp(v: float, lo: float = 1.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, v))


# ═══════════════════════════════════════════════════════════════
# 指標計算
# ═══════════════════════════════════════════════════════════════
async def compute_market_sentiment(conn: asyncpg.Connection, ref_date: date) -> tuple[float, str]:
    since = ref_date - timedelta(days=SENTIMENT_WINDOW_DAYS - 1)
    avg_sent = await conn.fetchval("""
        SELECT AVG(sentiment_score) FROM (
            SELECT sentiment_score FROM yahoo_stock_news
            WHERE sentiment_score IS NOT NULL AND fetched_date BETWEEN $1 AND $2
            UNION ALL
            SELECT sentiment_score FROM cnyes_stock_news
            WHERE sentiment_score IS NOT NULL AND fetched_date BETWEEN $1 AND $2
            UNION ALL
            SELECT sentiment_score FROM nstock_stock_news
            WHERE sentiment_score IS NOT NULL AND fetched_date BETWEEN $1 AND $2
        ) t
    """, since, ref_date)

    if avg_sent is None:
        return 5.0, "資料不足"

    score = round(clamp(float(avg_sent) / 9 * 10), 1)
    if score >= 7:
        label = "偏樂觀"
    elif score >= 4:
        label = "中性"
    else:
        label = "偏保守"
    return score, label


async def compute_news_momentum(conn: asyncpg.Connection, ref_date: date) -> tuple[float, str]:
    today_count = await conn.fetchval("""
        SELECT COUNT(*) FROM (
            SELECT id FROM yahoo_stock_news WHERE fetched_date = $1
            UNION ALL
            SELECT id FROM cnyes_stock_news WHERE fetched_date = $1
            UNION ALL
            SELECT id FROM nstock_stock_news WHERE fetched_date = $1
        ) t
    """, ref_date)

    base_since = ref_date - timedelta(days=MOMENTUM_BASE_DAYS)
    base_until = ref_date - timedelta(days=1)
    base_total = await conn.fetchval("""
        SELECT COUNT(*) FROM (
            SELECT id FROM yahoo_stock_news WHERE fetched_date BETWEEN $1 AND $2
            UNION ALL
            SELECT id FROM cnyes_stock_news WHERE fetched_date BETWEEN $1 AND $2
            UNION ALL
            SELECT id FROM nstock_stock_news WHERE fetched_date BETWEEN $1 AND $2
        ) t
    """, base_since, base_until)

    base_days = (base_until - base_since).days + 1
    avg_baseline = (base_total or 0) / base_days if base_days > 0 else 0

    ratio = (today_count / avg_baseline) if avg_baseline > 0 else 1.0
    score = round(clamp(5 * ratio), 1)
    if score >= 7:
        label = "動能增強"
    elif score >= 4:
        label = "動能平穩"
    else:
        label = "動能減弱"
    return score, label


def compute_topic_heat(results: list[dict], top_n: int = 5) -> tuple[float, str]:
    if not results:
        return 5.0, "資料不足"
    top = results[:top_n]
    avg_hotness = sum(r["hotness"] for r in top) / len(top)
    score = round(clamp(avg_hotness / 10), 1)
    if score >= 7:
        label = "熱度上升"
    elif score >= 4:
        label = "溫和"
    else:
        label = "熱度平淡"
    return score, label


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════
async def main() -> None:
    print("=" * 52)
    print("  第三階段：計算 Dashboard 指標")
    print("=" * 52)

    conn = await asyncpg.connect(PG_DSN)
    try:
        await ensure_tables(conn)

        ref_date = await get_max_fetched_date(conn)
        if ref_date is None:
            print("[ERROR] 新聞表沒有任何資料")
            return
        print(f"  資料基準日期：{ref_date}")

        print("\n[1] 計算市場情緒分數...")
        sent_score, sent_label = await compute_market_sentiment(conn, ref_date)
        print(f"   {sent_score} / 10（{sent_label}）")

        print("\n[2] 計算新聞動能分數...")
        momentum_score, momentum_label = await compute_news_momentum(conn, ref_date)
        print(f"   {momentum_score} / 10（{momentum_label}）")

        print("\n[3] 計算題材熱度分數 + 熱門議題...")
        kw_sample = await conn.fetchval(
            "SELECT COUNT(*) FROM yahoo_stock_news WHERE keywords IS NOT NULL"
        )
        if kw_sample == 0:
            print("   [WARN] keywords 欄位無資料，請先執行 stage1_keyword_extract.py")
            heat_score, heat_label = 5.0, "資料不足"
            top_topics: list[dict] = []
        else:
            rows = await fetch_keyword_rows(conn, ref_date)
            results = build_stats(rows, ref_date)
            for r in results:
                r["cat"] = classify(r)
            heat_score, heat_label = compute_topic_heat(results, TOP_N_TOPICS)
            top_topics = results[:TOP_N_TOPICS]
        print(f"   {heat_score} / 10（{heat_label}）")

        print("\n[4] 寫入資料庫...")
        await conn.execute("DELETE FROM daily_indicators WHERE computed_date = $1", ref_date)
        await conn.execute("""
            INSERT INTO daily_indicators (
                computed_date,
                market_sentiment_score, market_sentiment_label,
                topic_heat_score, topic_heat_label,
                news_momentum_score, news_momentum_label
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, ref_date, sent_score, sent_label, heat_score, heat_label, momentum_score, momentum_label)

        await conn.execute("DELETE FROM theme_daily_summary WHERE computed_date = $1", ref_date)
        if top_topics:
            await conn.executemany("""
                INSERT INTO theme_daily_summary (
                    computed_date, rank, keyword, category,
                    recent_count, trend_ratio, avg_sentiment, hotness, sources
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """, [
                (ref_date, i + 1, r["keyword"], r["cat"], r["recent_count"],
                 r["trend_ratio"], r["avg_sentiment"], r["hotness"], r["sources"])
                for i, r in enumerate(top_topics)
            ])

        print(f"   完成，寫入 1 筆指標 + {len(top_topics)} 筆熱門議題")
    finally:
        await conn.close()

    print("\n" + "=" * 52)
    print("  完成！執行 sync_to_supabase.py 可將結果同步到雲端 Dashboard")
    print("=" * 52)


if __name__ == "__main__":
    asyncio.run(main())
