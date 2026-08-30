"""
第二階段：題材趨勢分析
- 從 Stage 1 產生的 keywords 欄位讀取資料
- 統計近 7 天 vs 近 90 天基準的詞頻差異
- 找出新興/升溫題材並產生 HTML 報告

前置條件：先執行 stage1_keyword_extract.py
執行：python stage2_theme_trends.py
"""

import asyncio
import json
import os
import webbrowser
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg

# ═══════════════════════════════════════════════════════════════
# 設定
# ═══════════════════════════════════════════════════════════════
PG_DSN       = "postgresql://postgres:lab529@localhost:5432/postgres"
TZ           = ZoneInfo("Asia/Taipei")
TOTAL_DAYS   = 90   # 基準視窗總長度（待 Stage1 補跑後趨勢分析才準確）
RECENT_DAYS  = 7    # 近期視窗（用來偵測趨勢）
MIN_RECENT   = 3    # 近期至少出現幾次才納入排行
SMOOTH       = 0.3  # Laplace 平滑，避免除以零

# 自動偵測模式：基準期資料量 >= 近期的 TREND_COVERAGE_THRESHOLD 倍才開趨勢模式
# 設為 None 可強制指定模式（True = 純頻率，False = 趨勢）
FREQ_ONLY              = None   # None = 自動判斷
TREND_COVERAGE_THRESHOLD = 0.3  # 基準期至少要有近期 30% 的資料量

# 文件頻率門檻（依來源數分級）：單來源版型詞用嚴格門檻，多來源主題詞用寬鬆門檻
DF_SINGLE_SOURCE = 0.08   # 1 個來源 → 超過 8% 文章即視為版型詞
DF_MULTI_SOURCE  = 0.25   # 2+ 個來源 → 超過 25% 文章才視為版型詞

# 趨勢倍率上限：批次爬蟲造成基準期稀疏 → 單篇公司訊息可能跑出 500x 假訊號
# cap 後所有 >= 30x 的詞「趨勢分」相同，排名改由出現次數 + 來源數決定
TREND_RATIO_CAP = 30.0

# 趨勢模式下，基準期至少出現幾次才納入（過濾 base=0 的一次性事件：犯罪/單一公司消息）
MIN_BASELINE = 1


# ═══════════════════════════════════════════════════════════════
# Step 1 — 從 DB 撈 keywords
# ═══════════════════════════════════════════════════════════════
async def get_data_max_date(conn: asyncpg.Connection) -> date:
    """取三個表中最新的 fetched_date，作為「今天」的基準"""
    row = await conn.fetchrow("""
        SELECT MAX(d) AS max_date FROM (
            SELECT MAX(fetched_date) AS d FROM yahoo_stock_news  WHERE keywords IS NOT NULL
            UNION ALL
            SELECT MAX(fetched_date)        FROM cnyes_stock_news WHERE keywords IS NOT NULL
            UNION ALL
            SELECT MAX(fetched_date)        FROM nstock_stock_news WHERE keywords IS NOT NULL
        ) t
    """)
    return row["max_date"]


async def fetch_keyword_rows(conn: asyncpg.Connection, ref_date: date) -> list[dict]:
    """
    從三個新聞表 unnest keywords，回傳每一筆 (keyword, article_id, sentiment_score, fetched_date, source)
    以 ref_date 為基準往前取 TOTAL_DAYS 天
    """
    since = ref_date - timedelta(days=TOTAL_DAYS)
    rows = await conn.fetch("""
        SELECT keyword, article_id, sentiment_score, fetched_date, source
        FROM (
            SELECT unnest(keywords) AS keyword,
                   id              AS article_id,
                   sentiment_score,
                   fetched_date,
                   'yahoo' AS source
            FROM   yahoo_stock_news
            WHERE  fetched_date >= $1
              AND  keywords IS NOT NULL

            UNION ALL

            SELECT unnest(keywords) AS keyword,
                   id              AS article_id,
                   sentiment_score,
                   fetched_date,
                   'cnyes' AS source
            FROM   cnyes_stock_news
            WHERE  fetched_date >= $1
              AND  keywords IS NOT NULL

            UNION ALL

            SELECT unnest(keywords) AS keyword,
                   id              AS article_id,
                   sentiment_score,
                   fetched_date,
                   'nstock' AS source
            FROM   nstock_stock_news
            WHERE  fetched_date >= $1
              AND  keywords IS NOT NULL
        ) t
    """, since)
    return rows


# ═══════════════════════════════════════════════════════════════
# Step 2 — 聚合 & 計算趨勢
# ═══════════════════════════════════════════════════════════════
def build_stats(rows: list, today: date) -> list[dict]:
    recent_cutoff = today - timedelta(days=RECENT_DAYS)

    data: dict[str, dict] = defaultdict(lambda: {
        "recent_count":   0,
        "baseline_count": 0,
        "sentiments":     [],
        "sources":        set(),
        "article_ids":    set(),   # 用來計算文件頻率
    })
    total_recent   = 0
    total_baseline = 0
    recent_articles:   set[int] = set()
    baseline_articles: set[int] = set()

    for row in rows:
        kw  = (row["keyword"] or "").strip()
        if not kw or len(kw) < 2:
            continue

        d     = data[kw]
        fdate: date = row["fetched_date"]
        aid   = row["article_id"]

        if fdate > recent_cutoff:
            d["recent_count"] += 1
            total_recent += 1
            recent_articles.add(aid)
        else:
            d["baseline_count"] += 1
            total_baseline += 1
            baseline_articles.add(aid)

        d["article_ids"].add(aid)

        if row["sentiment_score"] is not None:
            d["sentiments"].append(row["sentiment_score"])
        d["sources"].add(row["source"])

    total_articles = len(recent_articles) + len(baseline_articles) or 1

    total_recent   = total_recent   or 1
    total_baseline = total_baseline or 1

    # 自動判斷模式：基準期資料量不足就退回純頻率模式
    if FREQ_ONLY is None:
        coverage_ratio = total_baseline / total_recent
        use_freq_only  = coverage_ratio < TREND_COVERAGE_THRESHOLD
        if use_freq_only:
            print(f"   [AUTO] 基準期資料只有近期的 {coverage_ratio:.1%}（門檻 {TREND_COVERAGE_THRESHOLD:.0%}）"
                  f"→ 切換為純頻率模式")
            print(f"          補跑 stage1_keyword_extract.py 後可開啟趨勢模式")
        else:
            print(f"   [AUTO] 基準期/近期比例 {coverage_ratio:.1%} → 趨勢模式")
    else:
        use_freq_only = FREQ_ONLY

    df_filtered = 0
    results = []
    for kw, d in data.items():
        if d["recent_count"] < MIN_RECENT:
            continue

        # DF 過濾：依來源數調整門檻（單來源版型詞用更嚴格門檻）
        df_ratio   = len(d["article_ids"]) / total_articles
        src_count  = len(d["sources"])
        df_thresh  = DF_SINGLE_SOURCE if src_count == 1 else DF_MULTI_SOURCE
        if df_ratio > df_thresh:
            df_filtered += 1
            continue

        # 趨勢模式下：基準期出現次數不足視為一次性事件，跳過
        if not use_freq_only and d["baseline_count"] < MIN_BASELINE:
            continue

        sents     = d["sentiments"]
        avg_sent  = sum(sents) / len(sents) if sents else 5.0
        src_count = len(d["sources"])

        if use_freq_only:
            trend_ratio = 0.0
        else:
            recent_frac   = d["recent_count"]   / total_recent
            baseline_frac = d["baseline_count"] / total_baseline
            trend_ratio   = (recent_frac + SMOOTH / total_recent) / \
                            (baseline_frac + SMOOTH / total_baseline)

        results.append({
            "keyword":        kw,
            "recent_count":   d["recent_count"],
            "baseline_count": d["baseline_count"],
            "total_count":    d["recent_count"] + d["baseline_count"],
            "trend_ratio":    round(trend_ratio, 2),
            "avg_sentiment":  round(avg_sent, 1),
            "deviation":      round(abs(avg_sent - 5.0), 1),
            "sources":        src_count,
        })

    if not results:
        return []

    max_cnt = max(r["recent_count"] for r in results) or 1

    for r in results:
        cnt_norm  = r["recent_count"] / max_cnt * 100
        src_bonus = (r["sources"] - 1) * 10
        if use_freq_only:
            dev_norm     = r["deviation"] / 4.0 * 100
            r["hotness"] = round(cnt_norm * 0.70 + dev_norm * 0.20 + src_bonus * 0.10, 1)
        else:
            # 趨勢倍率 cap 後正規化：>=30x 的詞趨勢分相同，改由頻率/來源數決勝
            capped_trend = min(r["trend_ratio"], TREND_RATIO_CAP)
            trend_norm   = capped_trend / TREND_RATIO_CAP * 100
            r["hotness"] = round(trend_norm * 0.55 + cnt_norm * 0.35 + src_bonus * 0.10, 1)

    results.sort(key=lambda x: x["hotness"], reverse=True)
    print(f"   [DF] 版型詞過濾：移除 {df_filtered} 個（單來源>{DF_SINGLE_SOURCE:.0%} / 多來源>{DF_MULTI_SOURCE:.0%}）")
    return results


# ═══════════════════════════════════════════════════════════════
# Step 3 — 分類標籤
# ═══════════════════════════════════════════════════════════════
def classify(r: dict) -> str:
    is_trending   = r["trend_ratio"] >= 2.0
    is_popular    = r["recent_count"] >= 10
    is_emotional  = r["deviation"] >= 1.5
    is_multi_src  = r["sources"] >= 2

    if is_trending and is_popular:
        return "🚀 爆發性成長"
    elif is_trending and is_multi_src:
        return "📈 新興題材"
    elif is_popular and is_emotional:
        return "🔥 熱門且情緒強"
    elif is_popular:
        return "📰 高曝光"
    elif is_emotional:
        return "⚠️ 情緒異常"
    elif is_trending:
        return "↑ 升溫中"
    return "─ 穩定"


# ═══════════════════════════════════════════════════════════════
# Step 4 — HTML 報告
# ═══════════════════════════════════════════════════════════════
def build_html(results: list[dict], ts: str, today: date) -> str:
    for r in results:
        r["cat"] = classify(r)

    from collections import defaultdict as _dd
    cat_counts = _dd(int)
    for r in results:
        cat_counts[r["cat"]] += 1

    top = results[:30]

    labels      = json.dumps([r["keyword"]      for r in top], ensure_ascii=False)
    recent_data = [r["recent_count"]   for r in top]
    base_data   = [round(r["baseline_count"] / (TOTAL_DAYS - RECENT_DAYS) * RECENT_DAYS, 1)
                   for r in top]   # 換算成同等 7 天的量
    trend_data  = [r["trend_ratio"]    for r in top]
    sent_data   = [r["avg_sentiment"]  for r in top]

    wc_js = json.dumps(
        [[r["keyword"], r["recent_count"]] for r in results if r["recent_count"] > 0],
        ensure_ascii=False,
    )

    max_cnt = max((r["recent_count"] for r in results), default=1) or 1

    rows_html = ""
    for i, r in enumerate(results, 1):
        bar_w = min(r["recent_count"] / max_cnt * 100, 100)
        s = r["avg_sentiment"]
        if s >= 7:
            sc, si = "sent-pos", "🟢"
        elif s <= 3:
            sc, si = "sent-neg", "🔴"
        else:
            sc, si = "sent-neu", "🟡"

        cat = r["cat"]
        bc_map = {
            "🚀": "badge-blast",
            "📈": "badge-rise",
            "🔥": "badge-hot",
            "📰": "badge-news",
            "⚠": "badge-warn",
            "↑":  "badge-up",
        }
        bc = next((v for k, v in bc_map.items() if k in cat), "badge-cold")

        tr_color = "#51cf66" if r["trend_ratio"] >= 2 else \
                   "#ffa94d" if r["trend_ratio"] >= 1.2 else "#718096"

        rows_html += f"""
      <tr>
        <td>{i}</td>
        <td class="kw">{r['keyword']}</td>
        <td><span class="badge {bc}">{cat}</span></td>
        <td>{r['recent_count']}</td>
        <td><div class="bar-w"><div class="bar" style="width:{bar_w:.0f}%"></div></div></td>
        <td style="color:{tr_color};font-weight:600">{r['trend_ratio']}x</td>
        <td class="{sc}">{si} {r['avg_sentiment']}</td>
        <td><span class="src-dot">{r['sources']}</span></td>
        <td class="hot">{r['hotness']}</td>
      </tr>"""

    recent_label = f"近 {RECENT_DAYS} 天"
    baseline_lbl = f"基準（等比 {RECENT_DAYS} 天）"

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>台股題材趨勢分析 v2（關鍵詞版）</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script src="https://cdn.jsdelivr.net/npm/wordcloud@1.2.2/src/wordcloud2.min.js"></script>
<style>
:root{{--bg:#0f1117;--card:#1a1d27;--bd:#2a2d3a;--tx:#e2e8f0;--mu:#718096;--acc:#6c63ff;
  --blast:#ff6b6b;--rise:#22b8cf;--hot:#ffa94d;--news:#51cf66;--warn:#cc5de8;--up:#74c0fc;--cold:#868e96}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--tx);font-family:'Microsoft JhengHei','Segoe UI',sans-serif;padding:24px}}
h1{{font-size:1.8rem;background:linear-gradient(90deg,#6c63ff,#22b8cf);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:4px}}
.sub{{color:var(--mu);font-size:.85rem;margin-bottom:24px}}
.stats{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:24px}}
.sc{{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px 22px;flex:1;min-width:130px}}
.sc .n{{font-size:2rem;font-weight:700}}
.sc .l{{color:var(--mu);font-size:.78rem;margin-top:3px}}
.card{{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:20px;margin-bottom:20px}}
.card h3{{color:var(--mu);font-size:.9rem;margin-bottom:14px}}
#wc{{width:100%;height:350px}}
.charts{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}}
.cw{{position:relative;height:340px}}
table{{width:100%;border-collapse:collapse;background:var(--card);border-radius:12px;overflow:hidden;border:1px solid var(--bd)}}
thead th{{background:#12151f;color:var(--mu);font-size:.75rem;text-transform:uppercase;padding:10px 12px;text-align:left}}
tbody tr{{border-top:1px solid var(--bd);transition:background .15s}}
tbody tr:hover{{background:#1e2130}}
td{{padding:10px 12px;font-size:.85rem}}
.kw{{font-weight:600;font-size:.95rem}}
.hot{{font-weight:700;color:var(--acc)}}
.bar-w{{background:#2a2d3a;border-radius:3px;height:6px;width:110px;overflow:hidden}}
.bar{{height:100%;border-radius:3px;background:linear-gradient(90deg,#6c63ff,#22b8cf)}}
.sent-pos{{color:#51cf66;font-weight:600}}
.sent-neg{{color:#ff6b6b;font-weight:600}}
.sent-neu{{color:#ffa94d}}
.src-dot{{background:#2a2d3a;border-radius:4px;padding:1px 6px;font-size:.75rem;color:#adb5bd}}
.badge{{font-size:.7rem;padding:2px 7px;border-radius:20px;white-space:nowrap}}
.badge-blast{{background:rgba(255,107,107,.18);color:var(--blast);border:1px solid rgba(255,107,107,.35)}}
.badge-rise {{background:rgba(34,184,207,.18);color:var(--rise);border:1px solid rgba(34,184,207,.35)}}
.badge-hot  {{background:rgba(255,169,77,.18);color:var(--hot);border:1px solid rgba(255,169,77,.35)}}
.badge-news {{background:rgba(81,207,102,.18);color:var(--news);border:1px solid rgba(81,207,102,.35)}}
.badge-warn {{background:rgba(204,93,232,.18);color:var(--warn);border:1px solid rgba(204,93,232,.35)}}
.badge-up   {{background:rgba(116,192,252,.18);color:var(--up);border:1px solid rgba(116,192,252,.35)}}
.badge-cold {{background:rgba(134,142,150,.18);color:var(--cold);border:1px solid rgba(134,142,150,.35)}}
.sec{{font-size:1.05rem;font-weight:600;margin:20px 0 10px}}
.legend{{display:flex;gap:20px;flex-wrap:wrap;margin-bottom:16px;font-size:.8rem}}
.legend span{{display:flex;align-items:center;gap:6px;color:var(--mu)}}
.legend i{{width:12px;height:12px;border-radius:3px;display:inline-block}}
@media(max-width:700px){{.charts{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<h1>📊 台股題材趨勢分析 <small style="font-size:.6em;-webkit-text-fill-color:#718096">v2 · 關鍵詞版</small></h1>
<p class="sub">
  jieba 斷詞 → 移除公司/機構 → 詞頻比對（近 {RECENT_DAYS} 天 vs 近 {TOTAL_DAYS} 天基準）｜ {ts}
</p>

<div class="stats">
  <div class="sc"><div class="n">{len(results)}</div><div class="l">📊 識別關鍵詞數</div></div>
  <div class="sc"><div class="n" style="color:var(--blast)">{cat_counts.get("🚀 爆發性成長",0)}</div><div class="l">🚀 爆發性成長</div></div>
  <div class="sc"><div class="n" style="color:var(--rise)">{cat_counts.get("📈 新興題材",0)}</div><div class="l">📈 新興題材</div></div>
  <div class="sc"><div class="n" style="color:var(--hot)">{cat_counts.get("🔥 熱門且情緒強",0)}</div><div class="l">🔥 熱門且情緒強</div></div>
</div>

<div class="card">
  <h3>☁️ 近期題材文字雲（字體 = 近 {RECENT_DAYS} 天出現次數）</h3>
  <canvas id="wc"></canvas>
</div>

<div class="charts">
  <div class="card">
    <h3>📊 Top 30 — 近期 vs 基準出現量（等比 {RECENT_DAYS} 天）</h3>
    <div class="cw"><canvas id="cBar"></canvas></div>
  </div>
  <div class="card">
    <h3>🚀 Top 30 — 趨勢倍率（近期/基準 每日均量）</h3>
    <div class="cw"><canvas id="cTrend"></canvas></div>
  </div>
</div>

<div class="legend">
  <span><i style="background:rgba(255,107,107,.7)"></i> 爆發性成長（倍率≥2 且近期≥10）</span>
  <span><i style="background:rgba(34,184,207,.7)"></i> 新興題材（倍率≥2 且多來源）</span>
  <span><i style="background:rgba(255,169,77,.7)"></i> 熱門且情緒強（近期≥10 且偏離≥1.5）</span>
  <span><i style="background:#51cf66"></i> 趨勢倍率 ≥ 1.2　　<i style="background:#718096"></i> 穩定</span>
</div>

<p class="sec">📋 完整關鍵詞列表（共 {len(results)} 個）</p>
<table>
  <thead>
    <tr>
      <th>#</th><th>關鍵詞</th><th>分類</th>
      <th>近{RECENT_DAYS}天</th><th>熱度條</th>
      <th>趨勢倍率</th><th>平均情緒</th>
      <th>來源數</th><th>熱度分</th>
    </tr>
  </thead>
  <tbody>{rows_html}</tbody>
</table>

<script>
const gc={{color:'#718096'}},grid={{color:'#2a2d3a'}};
const L={labels};
const recentD={json.dumps(recent_data)};
const baseD={json.dumps(base_data)};
const trendD={json.dumps(trend_data)};
const sentD={json.dumps(sent_data)};

// ── Bar chart: 近期 vs 基準
new Chart(document.getElementById('cBar'),{{
  type:'bar',
  data:{{labels:L,datasets:[
    {{label:'{recent_label}',data:recentD,backgroundColor:'rgba(108,99,255,0.75)',borderRadius:3}},
    {{label:'{baseline_lbl}',data:baseD,backgroundColor:'rgba(134,142,150,0.45)',borderRadius:3}},
  ]}},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{labels:{{color:'#e2e8f0'}}}}}},
    scales:{{
      x:{{ticks:{{...gc,maxRotation:55,font:{{size:10}}}},grid,stacked:false}},
      y:{{ticks:gc,grid,title:{{display:true,text:'出現次數',color:'#718096'}}}}
    }}
  }}
}});

// ── Trend ratio
new Chart(document.getElementById('cTrend'),{{
  type:'bar',
  data:{{labels:L,datasets:[{{
    label:'趨勢倍率',data:trendD,borderRadius:3,
    backgroundColor:trendD.map(v=>
      v>=2?'rgba(255,107,107,0.75)':v>=1.2?'rgba(34,184,207,0.7)':'rgba(116,192,252,0.45)')
  }}]}},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:false}},
      annotation:{{annotations:{{line1:{{type:'line',yMin:1,yMax:1,borderColor:'rgba(255,255,255,.25)',borderWidth:1}}}}}}
    }},
    scales:{{
      x:{{ticks:{{...gc,maxRotation:55,font:{{size:10}}}},grid}},
      y:{{ticks:gc,grid,title:{{display:true,text:'倍率（近期/基準）',color:'#718096'}},min:0}}
    }}
  }}
}});

// ── Word cloud
const wcC=document.getElementById('wc');
wcC.width=wcC.parentElement.offsetWidth-40;
wcC.height=350;
const wcD={wc_js};
const mx=Math.max(...wcD.map(d=>d[1]));
const pal=['#ff6b6b','#ffa94d','#ffd43b','#51cf66','#22b8cf','#6c63ff','#cc5de8','#74c0fc'];
WordCloud(wcC,{{
  list:wcD.map(d=>[d[0],d[1]/mx*55+14]),
  fontFamily:"'Microsoft JhengHei',sans-serif",
  color:()=>pal[Math.floor(Math.random()*pal.length)],
  backgroundColor:'transparent',
  rotateRatio:0.2,gridSize:8,shuffle:true
}});
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════
async def main() -> None:
    print("=" * 52)
    print("  第二階段：題材趨勢分析")
    print(f"  近期視窗：{RECENT_DAYS} 天　基準視窗：{TOTAL_DAYS} 天")
    print("=" * 52)

    conn = await asyncpg.connect(PG_DSN)
    try:
        print("\n[1] 從 DB 讀取 keywords...")

        # 確認 keywords 欄位有資料
        sample = await conn.fetchval(
            "SELECT COUNT(*) FROM yahoo_stock_news WHERE keywords IS NOT NULL"
        )
        if sample == 0:
            print("❌ yahoo_stock_news.keywords 沒有資料！")
            print("   請先執行 stage1_keyword_extract.py")
            return

        # 以 DB 最新日期為基準，避免爬蟲停跑期間近期視窗落空
        ref_date = await get_data_max_date(conn)
        real_today = datetime.now(TZ).date()
        lag_days = (real_today - ref_date).days
        print(f"   資料最新日期：{ref_date}（距今 {lag_days} 天）")
        if lag_days > 0:
            print(f"   [NOTE] 以 {ref_date} 為基準進行趨勢分析")

        rows = await fetch_keyword_rows(conn, ref_date)
        print(f"   共 {len(rows)} 筆 keyword 記錄")

        if not rows:
            print("[ERROR] 沒有找到任何關鍵詞資料！")
            return

    finally:
        await conn.close()

    print("\n[2] 計算詞頻趨勢...")
    results = build_stats(rows, ref_date)
    print(f"   符合門檻關鍵詞：{len(results)} 個（近 {RECENT_DAYS} 天 >= {MIN_RECENT} 次）")

    if not results:
        print("[ERROR] 沒有符合門檻的關鍵詞，試著降低 MIN_RECENT 設定")
        return

    # 終端機排行榜
    print(f"\n[TOP 20] 熱門題材（近 {RECENT_DAYS} 天 vs 基準）：")
    print(f"  {'#':>3}  {'關鍵詞':10}  {'近期':>4}  {'倍率':>5}  {'情緒':>4}  {'來源':>3}  {'熱度':>5}")
    print("  " + "-" * 50)
    for i, r in enumerate(results[:20], 1):
        print(f"  {i:3d}  {r['keyword']:10}  {r['recent_count']:4d}  "
              f"{r['trend_ratio']:5.2f}x  {r['avg_sentiment']:4.1f}  "
              f"{r['sources']:3d}  {r['hotness']:5.1f}")

    # 新興題材特別列出
    emerging = [r for r in results if r["trend_ratio"] >= 2.0]
    if emerging:
        print(f"\n[RISING] 新興題材（趨勢倍率 >= 2.0，共 {len(emerging)} 個）：")
        for r in emerging[:10]:
            print(f"     {r['keyword']}  {r['trend_ratio']}x  近期:{r['recent_count']} 次")

    # HTML 報告
    print("\n[3] 產生 HTML 報告...")
    ts   = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    html = build_html(results, ts, ref_date)
    out  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stage2_trends_report.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n[OK] 報告：{out}")
    webbrowser.open(f"file:///{out}")


if __name__ == "__main__":
    asyncio.run(main())
