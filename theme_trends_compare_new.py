"""
台股題材熱門度分析 v3 — AI 主題標記版
=========================================
從 DB 三個新聞來源的 themes 欄位聚合，結合情緒分數計算熱度。
不再依賴手動 THEME_VOCAB 或 PTT 爬蟲。

前置條件：
    1. 各 analyze_sentiment.py 已改為同時標記 themes（TEXT[]）
    2. 至少跑過一次 analyze_sentiment，讓 DB 有 themes 資料

安裝依賴：
    pip install asyncpg
"""

import asyncio
import json
import os
import webbrowser
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg

# ═══════════════════════════════════════════════════════════════
# 設定
# ═══════════════════════════════════════════════════════════════
PG_DSN = "postgresql://postgres:lab529@localhost:5432/postgres"
TZ = ZoneInfo("Asia/Taipei")
DAYS_RANGE = 7  # 分析最近 N 天


# ═══════════════════════════════════════════════════════════════
# Step 1 — 從 DB 聚合主題
# ═══════════════════════════════════════════════════════════════
async def fetch_theme_stats(days: int = DAYS_RANGE) -> list[dict]:
    """
    從三個新聞表聚合 themes + sentiment_score
    回傳: [{"theme": str, "count": int, "avg_sent": float, "sentiments": [int], ...}, ...]
    """
    since = (datetime.now(TZ) - timedelta(days=days)).date()
    conn = await asyncpg.connect(PG_DSN)

    # 確保 themes 欄位存在
    for tbl in ("yahoo_stock_news", "cnyes_stock_news", "nstock_stock_news"):
        await conn.execute(f"""
            ALTER TABLE public.{tbl}
            ADD COLUMN IF NOT EXISTS themes TEXT[];
        """)

    # 用 unnest 展開 themes 陣列，每個 theme 配對一個 sentiment_score
    rows = await conn.fetch("""
        SELECT theme, sentiment_score, source FROM (
            SELECT unnest(themes) AS theme, sentiment_score, 'yahoo' AS source
                FROM yahoo_stock_news
                WHERE fetched_date >= $1 AND themes IS NOT NULL
            UNION ALL
            SELECT unnest(themes) AS theme, sentiment_score, 'cnyes' AS source
                FROM cnyes_stock_news
                WHERE fetched_date >= $1 AND themes IS NOT NULL
            UNION ALL
            SELECT unnest(themes) AS theme, sentiment_score, 'nstock' AS source
                FROM nstock_stock_news
                WHERE fetched_date >= $1 AND themes IS NOT NULL
                AND title NOT LIKE '【快訊】%'
                AND title NOT LIKE '【重訊】%'
        ) t
    """, since)
    await conn.close()

    # 聚合
    data = defaultdict(lambda: {"count": 0, "sentiments": [], "sources": set()})
    for r in rows:
        theme = r["theme"].strip()
        if not theme or len(theme) < 2:
            continue
        d = data[theme]
        d["count"] += 1
        if r["sentiment_score"] is not None:
            d["sentiments"].append(r["sentiment_score"])
        d["sources"].add(r["source"])

    # 計算統計
    results = []
    for theme, d in data.items():
        sents = d["sentiments"]
        avg_sent = sum(sents) / len(sents) if sents else 5.0
        # 情緒偏離中性程度
        deviation = abs(avg_sent - 5)
        # 情緒分歧度（標準差）
        if len(sents) >= 2:
            mean = avg_sent
            variance = sum((s - mean) ** 2 for s in sents) / len(sents)
            std_dev = variance ** 0.5
        else:
            std_dev = 0

        # 熱度公式：新聞數 * 0.5 + 情緒偏離 * 0.25 + 分歧度 * 0.25
        # 全部正規化到 0-100
        results.append({
            "theme": theme,
            "count": d["count"],
            "avg_sentiment": round(avg_sent, 1),
            "deviation": round(deviation, 1),
            "std_dev": round(std_dev, 1),
            "sources": len(d["sources"]),
        })

    # 正規化熱度分數
    max_cnt = max((r["count"] for r in results), default=1) or 1
    max_dev = max((r["deviation"] for r in results), default=1) or 1
    max_std = max((r["std_dev"] for r in results), default=1) or 1

    for r in results:
        cnt_norm = r["count"] / max_cnt * 100
        dev_norm = r["deviation"] / max_dev * 100
        std_norm = r["std_dev"] / max_std * 100
        r["hotness"] = round(cnt_norm * 0.5 + dev_norm * 0.25 + std_norm * 0.25, 1)

    results.sort(key=lambda x: x["hotness"], reverse=True)
    return results


# ═══════════════════════════════════════════════════════════════
# Step 2 — 分類
# ═══════════════════════════════════════════════════════════════
def classify(r: dict) -> str:
    """根據新聞數 + 情緒偏離分類"""
    hot_cnt = r["count"] >= 5
    hot_sent = r["deviation"] >= 1.5

    if hot_cnt and hot_sent:
        return "🔥🔥 熱門且情緒強"
    elif hot_cnt:
        return "📰 高曝光"
    elif hot_sent:
        return "⚠️ 情緒異常"
    return "─ 冷門"


# ═══════════════════════════════════════════════════════════════
# Step 3 — HTML 報告
# ═══════════════════════════════════════════════════════════════
def build_html(results: list[dict], ts: str) -> str:
    for r in results:
        r["cat"] = classify(r)

    # 統計分類數量
    cat_counts = defaultdict(int)
    for r in results:
        cat_counts[r["cat"]] += 1

    top = results[:25]

    # 圖表資料
    labels = json.dumps([r["theme"] for r in top], ensure_ascii=False)
    counts = [r["count"] for r in top]
    sents = [r["avg_sentiment"] for r in top]
    hotness = [r["hotness"] for r in top]

    # 文字雲
    wc = [[r["theme"], max(r["count"], 1)] for r in results if r["count"] > 0]
    wc_js = json.dumps(wc, ensure_ascii=False)

    max_cnt = max((r["count"] for r in results), default=1) or 1

    # 表格
    rows_html = ""
    for i, r in enumerate(results, 1):
        bar_w = min(r["count"] / max_cnt * 100, 100)
        s = r["avg_sentiment"]
        if s >= 7:
            sc, si = "sent-pos", "🟢"
        elif s <= 3:
            sc, si = "sent-neg", "🔴"
        else:
            sc, si = "sent-neu", "🟡"

        cat = r["cat"]
        if "🔥" in cat:
            bc = "badge-hot"
        elif "⚠" in cat:
            bc = "badge-warn"
        elif "📰" in cat:
            bc = "badge-news"
        else:
            bc = "badge-cold"

        rows_html += f"""
      <tr>
        <td>{i}</td>
        <td class="kw">{r['theme']}</td>
        <td><span class="badge {bc}">{cat}</span></td>
        <td>{r['count']}</td>
        <td><div class="bar-w"><div class="bar" style="width:{bar_w:.0f}%"></div></div></td>
        <td class="{sc}">{si} {r['avg_sentiment']}</td>
        <td>{r['std_dev']}</td>
        <td class="hot">{r['hotness']}</td>
      </tr>"""

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>台股 AI 題材熱門度分析</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script src="https://cdn.jsdelivr.net/npm/wordcloud@1.2.2/src/wordcloud2.min.js"></script>
<style>
:root{{--bg:#0f1117;--card:#1a1d27;--bd:#2a2d3a;--tx:#e2e8f0;--mu:#718096;--acc:#6c63ff;
  --hot:#ff6b6b;--warn:#ffa94d;--news:#51cf66;--cold:#868e96}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--tx);font-family:'Microsoft JhengHei','Segoe UI',sans-serif;padding:24px}}
h1{{font-size:1.8rem;background:linear-gradient(90deg,#6c63ff,#f093fb);
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
.kw{{font-weight:600}}
.hot{{font-weight:700;color:var(--acc)}}
.bar-w{{background:#2a2d3a;border-radius:3px;height:6px;width:110px;overflow:hidden}}
.bar{{height:100%;border-radius:3px;background:linear-gradient(90deg,#6c63ff,#f093fb)}}
.sent-pos{{color:#51cf66;font-weight:600}}
.sent-neg{{color:#ff6b6b;font-weight:600}}
.sent-neu{{color:#ffa94d}}
.badge{{font-size:.7rem;padding:2px 7px;border-radius:20px;white-space:nowrap}}
.badge-hot {{background:rgba(255,107,107,.18);color:var(--hot);border:1px solid rgba(255,107,107,.35)}}
.badge-warn{{background:rgba(255,169,77,.18);color:var(--warn);border:1px solid rgba(255,169,77,.35)}}
.badge-news{{background:rgba(81,207,102,.18);color:var(--news);border:1px solid rgba(81,207,102,.35)}}
.badge-cold{{background:rgba(134,142,150,.18);color:var(--cold);border:1px solid rgba(134,142,150,.35)}}
.sec{{font-size:1.05rem;font-weight:600;margin:20px 0 10px}}
@media(max-width:700px){{.charts{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<h1>🤖 台股 AI 題材熱門度分析</h1>
<p class="sub">AI 自動標記新聞主題 → 聚合 {DAYS_RANGE} 天統計 → 情緒加權熱度 ｜ {ts}</p>

<div class="stats">
  <div class="sc"><div class="n">{len(results)}</div><div class="l">📊 識別主題數</div></div>
  <div class="sc"><div class="n" style="color:var(--hot)">{cat_counts.get("🔥🔥 熱門且情緒強", 0)}</div><div class="l">🔥🔥 熱門且情緒強</div></div>
  <div class="sc"><div class="n" style="color:var(--news)">{cat_counts.get("📰 高曝光", 0)}</div><div class="l">📰 高曝光</div></div>
  <div class="sc"><div class="n" style="color:var(--warn)">{cat_counts.get("⚠️ 情緒異常", 0)}</div><div class="l">⚠️ 情緒異常（關注）</div></div>
</div>

<div class="card">
  <h3>☁️ 題材文字雲（字體大小 = 新聞數量）</h3>
  <canvas id="wc"></canvas>
</div>

<div class="charts">
  <div class="card"><h3>📊 Top 25 — 新聞數量 + 熱度分</h3><div class="cw"><canvas id="cBar"></canvas></div></div>
  <div class="card"><h3>💡 Top 25 — 平均情緒分數</h3><div class="cw"><canvas id="cSent"></canvas></div></div>
</div>

<p class="sec">📋 完整題材列表（共 {len(results)} 個主題）</p>
<table>
  <thead><tr><th>#</th><th>題材</th><th>分類</th><th>新聞數</th><th>熱度條</th><th>平均情緒</th><th>分歧度</th><th>熱度分</th></tr></thead>
  <tbody>{rows_html}</tbody>
</table>

<script>
const gc={{color:'#718096'}},grid={{color:'#2a2d3a'}};
const L={labels};

new Chart(document.getElementById('cBar'),{{
  type:'bar',
  data:{{labels:L,datasets:[
    {{label:'新聞數',data:{counts},backgroundColor:'rgba(108,99,255,0.7)',borderRadius:4,yAxisID:'y'}},
    {{label:'熱度分',data:{hotness},type:'line',borderColor:'#f093fb',borderWidth:2,pointRadius:3,
      pointBackgroundColor:'#f093fb',tension:0.3,yAxisID:'y1'}}
  ]}},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{labels:{{color:'#e2e8f0'}}}}}},
    scales:{{
      x:{{ticks:{{...gc,maxRotation:50}},grid}},
      y:{{position:'left',ticks:gc,grid,title:{{display:true,text:'新聞數',color:'#718096'}}}},
      y1:{{position:'right',ticks:gc,grid:{{drawOnChartArea:false}},title:{{display:true,text:'熱度分',color:'#718096'}},min:0,max:100}}
    }}
  }}
}});

new Chart(document.getElementById('cSent'),{{
  type:'bar',
  data:{{labels:L,datasets:[{{
    label:'平均情緒',data:{sents},
    backgroundColor:{json.dumps(sents)}.map(v=>v>=7?'rgba(81,207,102,0.7)':v<=3?'rgba(255,107,107,0.7)':'rgba(255,169,77,0.7)'),
    borderRadius:4
  }}]}},
  options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:false}}}},
    scales:{{
      x:{{ticks:gc,grid,min:1,max:9,title:{{display:true,text:'情緒分數 (1=極負 ~ 9=極正)',color:'#718096'}}}},
      y:{{ticks:{{...gc,color:'#e2e8f0',font:{{size:11}}}},grid}}
    }}
  }}
}});

const wcC=document.getElementById('wc');
wcC.width=wcC.parentElement.offsetWidth-40;
wcC.height=350;
const wcD={wc_js};
const mx=Math.max(...wcD.map(d=>d[1]));
const pal=['#ff6b6b','#ffa94d','#ffd43b','#51cf66','#22b8cf','#6c63ff','#cc5de8','#f093fb'];
WordCloud(wcC,{{
  list:wcD.map(d=>[d[0],d[1]/mx*55+14]),
  fontFamily:"'Microsoft JhengHei',sans-serif",
  color:()=>pal[Math.floor(Math.random()*pal.length)],
  backgroundColor:'transparent',
  rotateRatio:0.25,gridSize:8,weightFactor:1,shuffle:true
}});
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════
async def main():
    print("=" * 52)
    print("  台股 AI 題材熱門度分析")
    print("=" * 52)

    print(f"\n📂  查詢 DB 主題資料（近 {DAYS_RANGE} 天）...")
    results = await fetch_theme_stats(DAYS_RANGE)

    if not results:
        print("❌ 沒有找到任何主題資料！")
        print("   請先執行 analyze_sentiment.py 讓 DB 有 themes 資料")
        return

    print(f"✅  共找到 {len(results)} 個主題\n")

    # 排行榜
    print("🏆  Top 15 熱門題材：")
    print(f"  {'#':>3s}  {'題材':12s}  {'新聞':>4s}  {'情緒':>4s}  {'偏離':>4s}  {'分歧':>4s}  {'熱度':>5s}")
    print("  " + "-" * 55)
    for i, r in enumerate(results[:15], 1):
        print(f"  {i:3d}  {r['theme']:12s}  {r['count']:4d}  {r['avg_sentiment']:4.1f}  "
              f"{r['deviation']:4.1f}  {r['std_dev']:4.1f}  {r['hotness']:5.1f}")

    # HTML 報告
    ts = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    html = build_html(results, ts)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "theme_trends_report.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✅  報告已產生：{out}")
    webbrowser.open(f"file:///{out}")


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
