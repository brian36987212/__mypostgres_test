from flask import Flask, render_template, jsonify, request, abort
import asyncpg
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
from dotenv import load_dotenv

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

import price_source

load_dotenv(override=False)  # 不覆蓋 Railway 注入的環境變數

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

PG_DSN = os.getenv("DATABASE_URL", "postgresql://postgres:lab529@localhost:5432/postgres")
USE_SSL = "supabase" in PG_DSN or os.getenv("DB_SSL", "") == "true"

# LINE Bot 設定
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', '')
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', '')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN) if LINE_CHANNEL_ACCESS_TOKEN else None
handler = WebhookHandler(LINE_CHANNEL_SECRET) if LINE_CHANNEL_SECRET else None

async def get_db_pool():
    # 每個 API 請求都會各自開一個新 pool（見下方 _get_* 函式），
    # refreshAll() 會同時打 9 支 API，預設 max_size=10 的話尖峰可能衝到 90 條連線，
    # 超過 pooler（本機或 Supabase）的 session 上限，回傳 EMAXCONNSESSION。
    # 每個請求內部查詢都是循序執行，1~2 條連線就夠用。
    if USE_SSL:
        return await asyncpg.create_pool(PG_DSN, ssl="require", min_size=1, max_size=2)
    return await asyncpg.create_pool(PG_DSN, min_size=1, max_size=2)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/pro')
def pro():
    return render_template('pro.html')

@app.route('/api/stats')
def get_stats():
    """取得總體統計資料"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_get_stats())
    loop.close()
    return jsonify(result)

async def _get_stats():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # 總新聞數（合併三個來源）
        total_news = await conn.fetchval("""
            SELECT COUNT(*) FROM (
                SELECT id FROM yahoo_stock_news
                UNION ALL
                SELECT id FROM cnyes_stock_news
                UNION ALL
                SELECT id FROM nstock_stock_news
            ) AS combined
        """)
        
        # 總股票數（合併三個來源）
        total_stocks = await conn.fetchval("""
            SELECT COUNT(DISTINCT stock_id) FROM (
                SELECT stock_id FROM yahoo_stock_news
                UNION
                SELECT stock_id FROM cnyes_stock_news
                UNION
                SELECT stock_id FROM nstock_stock_news
            ) AS combined
        """)
        
        # 今日新聞數（合併三個來源）
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        today_news = await conn.fetchval("""
            SELECT COUNT(*) FROM (
                SELECT id FROM yahoo_stock_news WHERE fetched_date = $1
                UNION ALL
                SELECT id FROM cnyes_stock_news WHERE fetched_date = $1
                UNION ALL
                SELECT id FROM nstock_stock_news WHERE fetched_date = $1
            ) AS combined
        """, today)
        
        # 平均情緒分數（合併三個來源）
        avg_sentiment = await conn.fetchval("""
            SELECT AVG(sentiment_score) FROM (
                SELECT sentiment_score FROM yahoo_stock_news WHERE sentiment_score IS NOT NULL
                UNION ALL
                SELECT sentiment_score FROM cnyes_stock_news WHERE sentiment_score IS NOT NULL
                UNION ALL
                SELECT sentiment_score FROM nstock_stock_news WHERE sentiment_score IS NOT NULL
            ) AS combined
        """)
    
    await pool.close()
    return {
        'total_news': total_news,
        'total_stocks': total_stocks,
        'today_news': today_news,
        'avg_sentiment': round(avg_sentiment, 2) if avg_sentiment else None
    }

@app.route('/api/sentiment_distribution')
def get_sentiment_distribution():
    """取得情緒分數分布"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_get_sentiment_distribution())
    loop.close()
    return jsonify(result)

async def _get_sentiment_distribution():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT sentiment_score, COUNT(*) as count
            FROM (
                SELECT sentiment_score FROM yahoo_stock_news WHERE sentiment_score IS NOT NULL
                UNION ALL
                SELECT sentiment_score FROM cnyes_stock_news WHERE sentiment_score IS NOT NULL
                UNION ALL
                SELECT sentiment_score FROM nstock_stock_news WHERE sentiment_score IS NOT NULL
            ) AS combined
            GROUP BY sentiment_score
            ORDER BY sentiment_score
        """)
    await pool.close()
    
    return {
        'labels': [row['sentiment_score'] for row in rows],
        'data': [row['count'] for row in rows]
    }

@app.route('/api/top_stocks')
def get_top_stocks():
    """取得最活躍股票"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_get_top_stocks())
    loop.close()
    return jsonify(result)

async def _get_top_stocks():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT combined.stock_id, COALESCE(m.stock_name, combined.stock_id) as stock_name, SUM(news_count) as news_count
            FROM (
                SELECT stock_id, COUNT(*) as news_count FROM yahoo_stock_news GROUP BY stock_id
                UNION ALL
                SELECT stock_id, COUNT(*) as news_count FROM cnyes_stock_news GROUP BY stock_id
                UNION ALL
                SELECT stock_id, COUNT(*) as news_count FROM nstock_stock_news GROUP BY stock_id
            ) AS combined
            LEFT JOIN stock_mapping m ON combined.stock_id = m.stock_id
            GROUP BY combined.stock_id, m.stock_name
            ORDER BY news_count DESC
            LIMIT 10
        """)
    await pool.close()
    
    return {
        'labels': [row['stock_name'] for row in rows],
        'data': [row['news_count'] for row in rows]
    }

@app.route('/api/recent_news')
def get_recent_news():
    """取得最新新聞"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_get_recent_news())
    loop.close()
    return jsonify(result)

async def _get_recent_news():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM (
                SELECT DISTINCT ON (title, stock_id)
                    stock_id, stock_name, title, publisher, sentiment_score, published_text, fetched_at, source, url
                FROM (
                    SELECT n.stock_id, COALESCE(m.stock_name, n.stock_id) as stock_name,
                           n.title, n.publisher, n.sentiment_score, n.published_text, n.fetched_at,
                           'Yahoo' as source, n.url
                    FROM yahoo_stock_news n
                    LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
                    UNION ALL
                    SELECT n.stock_id, COALESCE(m.stock_name, n.stock_id) as stock_name,
                           n.title, n.category_name as publisher, n.sentiment_score, 
                           TO_CHAR(n.published_at, 'YYYY年MM月DD日 HH24:MI') as published_text, 
                           n.fetched_at, '鉅亨網' as source, n.url
                    FROM cnyes_stock_news n
                    LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
                    UNION ALL
                    SELECT n.stock_id, COALESCE(m.stock_name, n.stock_id) as stock_name,
                           n.title, n.category as publisher, n.sentiment_score,
                           TO_CHAR(n.published_at, 'YYYY年MM月DD日 HH24:MI') as published_text,
                           n.fetched_at, 'NStock' as source, n.url
                    FROM nstock_stock_news n
                    LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
                ) AS combined
                ORDER BY title, stock_id, fetched_at DESC
            ) AS unique_news
            ORDER BY fetched_at DESC
            LIMIT 20
        """)
    await pool.close()
    
    return [{
        'stock_id': row['stock_id'],
        'stock_name': row['stock_name'],
        'title': row['title'],
        'publisher': row['publisher'],
        'sentiment_score': row['sentiment_score'],
        'published_text': row['published_text'],
        'fetched_at': row['fetched_at'].isoformat() if row['fetched_at'] else None,
        'source': row['source'],
        'url': row['url']
    } for row in rows]

@app.route('/api/daily_trend')
def get_daily_trend():
    """取得每日新聞趨勢"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_get_daily_trend())
    loop.close()
    return jsonify(result)

async def _get_daily_trend():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT fetched_date, SUM(count) as count
            FROM (
                SELECT fetched_date, COUNT(*) as count
                FROM yahoo_stock_news
                WHERE fetched_date >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY fetched_date
                UNION ALL
                SELECT fetched_date, COUNT(*) as count
                FROM cnyes_stock_news
                WHERE fetched_date >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY fetched_date
                UNION ALL
                SELECT fetched_date, COUNT(*) as count
                FROM nstock_stock_news
                WHERE fetched_date >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY fetched_date
            ) AS combined
            GROUP BY fetched_date
            ORDER BY fetched_date
        """)
    await pool.close()
    
    return {
        'labels': [row['fetched_date'].strftime('%m/%d') for row in rows],
        'data': [row['count'] for row in rows]
    }

@app.route('/api/pro/stock')
def get_pro_stock():
    """個股查詢：股票資訊卡 + 情緒 K 線 + 熱門題材 + 新聞明細（依日期區間）"""
    q = request.args.get('q', '').strip()
    start = request.args.get('start', '').strip()
    end = request.args.get('end', '').strip()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_get_pro_stock(q, start, end))
    loop.close()
    return jsonify(result)

def _parse_date(s, default):
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return default

def _moving_average(values, window):
    out = []
    for i in range(len(values)):
        if i + 1 < window:
            out.append(None)
        else:
            seg = values[i + 1 - window:i + 1]
            out.append(round(sum(seg) / window, 2))
    return out

async def _get_pro_stock(q, start, end):
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    end_d = _parse_date(end, today)
    start_d = _parse_date(start, end_d - timedelta(days=30))
    if start_d > end_d:
        start_d, end_d = end_d, start_d

    if not q:
        return {'found': False, 'reason': 'empty_query',
                'date_range': {'start': start_d.isoformat(), 'end': end_d.isoformat()}}

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # 解析股票（代號或名稱）
        stock = await conn.fetchrow("""
            SELECT s.stock_id, COALESCE(m.stock_name, s.stock_id) AS stock_name
            FROM (
                SELECT DISTINCT stock_id FROM yahoo_stock_news
                UNION SELECT DISTINCT stock_id FROM cnyes_stock_news
                UNION SELECT DISTINCT stock_id FROM nstock_stock_news
            ) s
            LEFT JOIN stock_mapping m ON s.stock_id = m.stock_id
            WHERE s.stock_id = $1 OR m.stock_name LIKE $2
            ORDER BY (s.stock_id = $1) DESC
            LIMIT 1
        """, q, f'%{q}%')

        if not stock:
            await pool.close()
            return {'found': False, 'reason': 'not_found',
                    'query': q,
                    'date_range': {'start': start_d.isoformat(), 'end': end_d.isoformat()}}

        stock_id = stock['stock_id']
        stock_name = stock['stock_name']

        rows = await conn.fetch("""
            SELECT title, sentiment_score, fetched_date, fetched_at, source, media, category, keywords, url
            FROM (
                SELECT n.title, n.sentiment_score, n.fetched_date, n.fetched_at,
                       'Yahoo' AS source, n.publisher AS media, NULL::text AS category, n.keywords, n.url
                FROM yahoo_stock_news n WHERE n.stock_id = $1
                UNION ALL
                SELECT n.title, n.sentiment_score, n.fetched_date, n.fetched_at,
                       '鉅亨網' AS source, n.category_name AS media, n.category_name AS category, n.keywords, n.url
                FROM cnyes_stock_news n WHERE n.stock_id = $1
                UNION ALL
                SELECT n.title, n.sentiment_score, n.fetched_date, n.fetched_at,
                       'NStock' AS source, n.category AS media, n.category AS category, n.keywords, n.url
                FROM nstock_stock_news n WHERE n.stock_id = $1
            ) t
            WHERE fetched_date BETWEEN $2 AND $3
            ORDER BY fetched_at
        """, stock_id, start_d, end_d)
    await pool.close()

    # ---- 情緒 K 線：每日 open/high/low/close（依當日新聞情緒分數）----
    by_day = {}
    for r in rows:
        s = r['sentiment_score']
        if s is None or r['fetched_date'] is None:
            continue
        by_day.setdefault(r['fetched_date'], []).append(s)
    day_keys = sorted(by_day.keys())
    labels, ohlc = [], []
    for d in day_keys:
        vals = by_day[d]  # 已依 fetched_at 排序
        labels.append(d.strftime('%m/%d'))
        ohlc.append([vals[0], max(vals), min(vals), vals[-1]])
    closes = [c[3] for c in ohlc]
    kline = {
        'labels': labels,
        'ohlc': ohlc,
        'ma3': _moving_average(closes, 3),
        'ma5': _moving_average(closes, 5),
        'ma10': _moving_average(closes, 10),
    }

    # ---- 熱門題材 Top N（該股區間內關鍵字，含趨勢）----
    mid = start_d + (end_d - start_d) / 2
    kw_stat = {}
    for r in rows:
        recent = r['fetched_date'] is not None and r['fetched_date'] >= mid
        for kw in (r['keywords'] or []):
            st = kw_stat.setdefault(kw, {'count': 0, 'sent': [], 'recent': 0, 'earlier': 0})
            st['count'] += 1
            if r['sentiment_score'] is not None:
                st['sent'].append(r['sentiment_score'])
            if recent:
                st['recent'] += 1
            else:
                st['earlier'] += 1
    hot = []
    for kw, st in kw_stat.items():
        ratio = (st['recent'] + 1) / (st['earlier'] + 1)
        trend = 'up' if ratio > 1.15 else 'down' if ratio < 0.87 else 'flat'
        hot.append({
            'keyword': kw,
            'count': st['count'],
            'avg_sentiment': round(sum(st['sent']) / len(st['sent']), 1) if st['sent'] else None,
            'trend': trend,
        })
    hot.sort(key=lambda x: x['count'], reverse=True)
    for i, h in enumerate(hot[:5], 1):
        h['rank'] = i
    hot_topics = hot[:5]

    # ---- 新聞明細 ----
    news = [{
        'date': r['fetched_date'].strftime('%Y/%m/%d') if r['fetched_date'] else None,
        'title': r['title'],
        'media': r['media'] or r['source'],
        'source': r['source'],
        'sentiment_score': r['sentiment_score'],
        'category': r['category'] or ((r['keywords'] or [None])[0]) or '—',
        'url': r['url'],
    } for r in sorted(rows, key=lambda x: (x['fetched_at'] is not None, x['fetched_at']), reverse=True)]

    # ---- 股價 / 產業別（TWSE / TPEX 官方 OpenAPI）----
    price_info = price_source.get_price(stock_id)
    industry = price_source.get_industry(stock_id)

    return {
        'found': True,
        'stock_id': stock_id,
        'stock_name': stock_name,
        'price': price_info['price'] if price_info else None,
        'price_change': price_info['change'] if price_info else None,
        'price_change_pct': price_info['change_pct'] if price_info else None,
        'price_date': price_info['trade_date'] if price_info else None,
        'industry': industry,
        'news_count': len(rows),
        'date_range': {'start': start_d.isoformat(), 'end': end_d.isoformat()},
        'kline': kline,
        'hot_topics': hot_topics,
        'news': news,
    }

@app.route('/api/pro/topics')
def get_pro_topics():
    """熱門題材完整列表（Top 20）"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_get_pro_topics())
    loop.close()
    return jsonify(result)

async def _get_pro_topics():
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            max_date = await conn.fetchval("SELECT MAX(computed_date) FROM theme_daily_summary")
            if not max_date:
                return {'computed_date': None, 'topics': []}
            rows = await conn.fetch("""
                SELECT rank, keyword, category, recent_count, trend_ratio, avg_sentiment, hotness, sources
                FROM theme_daily_summary
                WHERE computed_date = $1
                ORDER BY rank
                LIMIT 20
            """, max_date)
    except asyncpg.exceptions.UndefinedTableError:
        return {'computed_date': None, 'topics': []}
    finally:
        await pool.close()

    return {
        'computed_date': max_date.isoformat(),
        'topics': [{
            'rank': r['rank'],
            'keyword': r['keyword'],
            'category': r['category'],
            'recent_count': r['recent_count'],
            'trend_ratio': float(r['trend_ratio']) if r['trend_ratio'] is not None else None,
            'avg_sentiment': float(r['avg_sentiment']) if r['avg_sentiment'] is not None else None,
            'hotness': float(r['hotness']) if r['hotness'] is not None else None,
            'sources': r['sources'],
        } for r in rows]
    }

@app.route('/api/pro/news_search')
def get_pro_news_search():
    """新聞搜尋：關鍵字 / 來源 / 情緒區間"""
    q = request.args.get('q', '').strip()
    source = request.args.get('source', '').strip()   # '', 'Yahoo', '鉅亨網', 'NStock'
    try:
        smin = int(request.args.get('min', 1))
        smax = int(request.args.get('max', 9))
    except ValueError:
        smin, smax = 1, 9
    filtered = request.args.get('min') is not None or request.args.get('max') is not None
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_get_pro_news_search(q, source, smin, smax, not filtered))
    loop.close()
    return jsonify(result)

async def _get_pro_news_search(q, source, smin, smax, include_nulls):
    branches = {
        'Yahoo': """
            SELECT n.stock_id, COALESCE(m.stock_name, n.stock_id) AS stock_name, n.title,
                   n.publisher AS media, n.sentiment_score, n.fetched_date, n.fetched_at,
                   'Yahoo' AS source, n.url
            FROM yahoo_stock_news n LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
            WHERE (n.title LIKE $1 OR m.stock_name LIKE $1 OR n.stock_id LIKE $1)
              AND (n.sentiment_score BETWEEN $2 AND $3 OR ($4 AND n.sentiment_score IS NULL))""",
        '鉅亨網': """
            SELECT n.stock_id, COALESCE(m.stock_name, n.stock_id) AS stock_name, n.title,
                   n.category_name AS media, n.sentiment_score, n.fetched_date, n.fetched_at,
                   '鉅亨網' AS source, n.url
            FROM cnyes_stock_news n LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
            WHERE (n.title LIKE $1 OR m.stock_name LIKE $1 OR n.stock_id LIKE $1)
              AND (n.sentiment_score BETWEEN $2 AND $3 OR ($4 AND n.sentiment_score IS NULL))""",
        'NStock': """
            SELECT n.stock_id, COALESCE(m.stock_name, n.stock_id) AS stock_name, n.title,
                   n.category AS media, n.sentiment_score, n.fetched_date, n.fetched_at,
                   'NStock' AS source, n.url
            FROM nstock_stock_news n LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
            WHERE (n.title LIKE $1 OR m.stock_name LIKE $1 OR n.stock_id LIKE $1)
              AND (n.sentiment_score BETWEEN $2 AND $3 OR ($4 AND n.sentiment_score IS NULL))""",
    }
    selected = [sql for name, sql in branches.items() if source in ('', name)]
    if not selected:
        return []
    union = " UNION ALL ".join(selected)
    full = f"""
        SELECT * FROM (
            SELECT DISTINCT ON (title, stock_id) *
            FROM ({union}) AS t
            ORDER BY title, stock_id, fetched_at DESC
        ) u
        ORDER BY fetched_at DESC NULLS LAST
        LIMIT 50
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(full, f'%{q}%', smin, smax, include_nulls)
    await pool.close()

    return [{
        'stock_id': r['stock_id'],
        'stock_name': r['stock_name'],
        'title': r['title'],
        'media': r['media'] or r['source'],
        'source': r['source'],
        'sentiment_score': r['sentiment_score'],
        'date': r['fetched_date'].strftime('%Y/%m/%d') if r['fetched_date'] else None,
        'url': r['url'],
    } for r in rows]

@app.route('/api/pro/watchlist')
def get_pro_watchlist():
    """自選股批次摘要：news_count / avg_sentiment / 即時股價"""
    ids = [s.strip() for s in request.args.get('ids', '').split(',') if s.strip()]
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_get_pro_watchlist(ids))
    loop.close()
    return jsonify(result)

async def _get_pro_watchlist(ids):
    if not ids:
        return []
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT c.stock_id, COALESCE(m.stock_name, c.stock_id) AS stock_name,
                   SUM(c.cnt) AS news_count, SUM(c.scnt) AS scnt, SUM(c.ssum) AS ssum
            FROM (
                SELECT stock_id, COUNT(*) cnt, COUNT(sentiment_score) scnt, COALESCE(SUM(sentiment_score),0) ssum
                FROM yahoo_stock_news WHERE stock_id = ANY($1) GROUP BY stock_id
                UNION ALL
                SELECT stock_id, COUNT(*) cnt, COUNT(sentiment_score) scnt, COALESCE(SUM(sentiment_score),0) ssum
                FROM cnyes_stock_news WHERE stock_id = ANY($1) GROUP BY stock_id
                UNION ALL
                SELECT stock_id, COUNT(*) cnt, COUNT(sentiment_score) scnt, COALESCE(SUM(sentiment_score),0) ssum
                FROM nstock_stock_news WHERE stock_id = ANY($1) GROUP BY stock_id
            ) c
            LEFT JOIN stock_mapping m ON c.stock_id = m.stock_id
            GROUP BY c.stock_id, m.stock_name
        """, ids)
    await pool.close()

    by_id = {r['stock_id']: r for r in rows}
    result = []
    for sid in ids:
        r = by_id.get(sid)
        news_count = int(r['news_count']) if r else 0
        scnt = int(r['scnt']) if r else 0
        avg_sent = round(float(r['ssum']) / scnt, 2) if (r and scnt) else None
        stock_name = r['stock_name'] if r else sid
        price_info = price_source.get_price(sid)
        result.append({
            'stock_id': sid,
            'stock_name': stock_name,
            'news_count': news_count,
            'avg_sentiment': avg_sent,
            'price': price_info['price'] if price_info else None,
            'price_change': price_info['change'] if price_info else None,
            'price_change_pct': price_info['change_pct'] if price_info else None,
        })
    return result

@app.route('/api/indicators')
def get_indicators():
    """取得三大分數指標（市場情緒 / 題材熱度 / 新聞動能）"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_get_indicators())
    loop.close()
    return jsonify(result)

async def _get_indicators():
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM daily_indicators
                ORDER BY computed_date DESC
                LIMIT 1
            """)
    except asyncpg.exceptions.UndefinedTableError:
        row = None
    finally:
        await pool.close()

    if not row:
        return None

    return {
        'computed_date': row['computed_date'].isoformat(),
        'market_sentiment': {
            'score': float(row['market_sentiment_score']) if row['market_sentiment_score'] is not None else None,
            'label': row['market_sentiment_label']
        },
        'topic_heat': {
            'score': float(row['topic_heat_score']) if row['topic_heat_score'] is not None else None,
            'label': row['topic_heat_label']
        },
        'news_momentum': {
            'score': float(row['news_momentum_score']) if row['news_momentum_score'] is not None else None,
            'label': row['news_momentum_label']
        }
    }

@app.route('/api/hot_topics')
def get_hot_topics():
    """取得熱門議題 Top 3"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_get_hot_topics())
    loop.close()
    return jsonify(result)

async def _get_hot_topics():
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            max_date = await conn.fetchval("SELECT MAX(computed_date) FROM theme_daily_summary")
            if not max_date:
                return []
            rows = await conn.fetch("""
                SELECT rank, keyword, category, recent_count, trend_ratio, avg_sentiment, sources
                FROM theme_daily_summary
                WHERE computed_date = $1
                ORDER BY rank
                LIMIT 3
            """, max_date)
    except asyncpg.exceptions.UndefinedTableError:
        rows = []
    finally:
        await pool.close()

    return [{
        'rank': row['rank'],
        'keyword': row['keyword'],
        'category': row['category'],
        'recent_count': row['recent_count'],
        'trend_ratio': float(row['trend_ratio']) if row['trend_ratio'] is not None else None,
        'avg_sentiment': float(row['avg_sentiment']) if row['avg_sentiment'] is not None else None,
        'sources': row['sources']
    } for row in rows]

@app.route('/api/weekly_analysis')
def get_weekly_analysis():
    """取得最近一周新聞分析"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_get_weekly_analysis())
    loop.close()
    return jsonify(result)

async def _get_weekly_analysis():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # 一周前的日期
        week_ago = (datetime.now(ZoneInfo("Asia/Taipei")) - timedelta(days=7)).date()
        
        # 一周總新聞數
        total_news = await conn.fetchval("""
            SELECT COUNT(*) FROM (
                SELECT id FROM yahoo_stock_news WHERE fetched_date >= $1
                UNION ALL
                SELECT id FROM cnyes_stock_news WHERE fetched_date >= $1
                UNION ALL
                SELECT id FROM nstock_stock_news WHERE fetched_date >= $1
            ) AS combined
        """, week_ago)
        
        # 一周平均情緒
        avg_sentiment = await conn.fetchval("""
            SELECT AVG(sentiment_score) FROM (
                SELECT sentiment_score FROM yahoo_stock_news 
                WHERE sentiment_score IS NOT NULL AND fetched_date >= $1
                UNION ALL
                SELECT sentiment_score FROM cnyes_stock_news 
                WHERE sentiment_score IS NOT NULL AND fetched_date >= $1
                UNION ALL
                SELECT sentiment_score FROM nstock_stock_news
                WHERE sentiment_score IS NOT NULL AND fetched_date >= $1
            ) AS combined
        """, week_ago)
        
        # 一周熱門股票 TOP 5
        top_stocks = await conn.fetch("""
            SELECT combined.stock_id, COALESCE(m.stock_name, combined.stock_id) as stock_name, SUM(news_count) as news_count
            FROM (
                SELECT stock_id, COUNT(*) as news_count FROM yahoo_stock_news WHERE fetched_date >= $1 GROUP BY stock_id
                UNION ALL
                SELECT stock_id, COUNT(*) as news_count FROM cnyes_stock_news WHERE fetched_date >= $1 GROUP BY stock_id
                UNION ALL
                SELECT stock_id, COUNT(*) as news_count FROM nstock_stock_news WHERE fetched_date >= $1 GROUP BY stock_id
            ) AS combined
            LEFT JOIN stock_mapping m ON combined.stock_id = m.stock_id
            GROUP BY combined.stock_id, m.stock_name
            ORDER BY news_count DESC
            LIMIT 5
        """, week_ago)
        
        # 正面/負面新聞數
        positive_count = await conn.fetchval("""
            SELECT COUNT(*) FROM (
                SELECT id FROM yahoo_stock_news WHERE sentiment_score >= 7 AND fetched_date >= $1
                UNION ALL
                SELECT id FROM cnyes_stock_news WHERE sentiment_score >= 7 AND fetched_date >= $1
                UNION ALL
                SELECT id FROM nstock_stock_news WHERE sentiment_score >= 7 AND fetched_date >= $1
            ) AS combined
        """, week_ago)
        
        negative_count = await conn.fetchval("""
            SELECT COUNT(*) FROM (
                SELECT id FROM yahoo_stock_news WHERE sentiment_score <= 3 AND fetched_date >= $1
                UNION ALL
                SELECT id FROM cnyes_stock_news WHERE sentiment_score <= 3 AND fetched_date >= $1
                UNION ALL
                SELECT id FROM nstock_stock_news WHERE sentiment_score <= 3 AND fetched_date >= $1
            ) AS combined
        """, week_ago)
        
    await pool.close()
    return {
        'total_news': total_news,
        'avg_sentiment': round(avg_sentiment, 2) if avg_sentiment else None,
        'top_stocks': [{'stock_name': row['stock_name'], 'news_count': row['news_count']} for row in top_stocks],
        'positive_count': positive_count,
        'negative_count': negative_count
    }

@app.route('/api/weekly_sentiment_trend')
def get_weekly_sentiment_trend():
    """取得一周情緒趨勢"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_get_weekly_sentiment_trend())
    loop.close()
    return jsonify(result)

async def _get_weekly_sentiment_trend():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT fetched_date, AVG(sentiment_score) as avg_sentiment
            FROM (
                SELECT fetched_date, sentiment_score FROM yahoo_stock_news 
                WHERE sentiment_score IS NOT NULL AND fetched_date >= CURRENT_DATE - INTERVAL '7 days'
                UNION ALL
                SELECT fetched_date, sentiment_score FROM cnyes_stock_news 
                WHERE sentiment_score IS NOT NULL AND fetched_date >= CURRENT_DATE - INTERVAL '7 days'
                UNION ALL
                SELECT fetched_date, sentiment_score FROM nstock_stock_news
                WHERE sentiment_score IS NOT NULL AND fetched_date >= CURRENT_DATE - INTERVAL '7 days'
            ) AS combined
            GROUP BY fetched_date
            ORDER BY fetched_date
        """)
    await pool.close()
    
    return {
        'labels': [row['fetched_date'].strftime('%m/%d') for row in rows],
        'data': [round(row['avg_sentiment'], 2) if row['avg_sentiment'] else 0 for row in rows]
    }

# ================= LINE Bot Webhook =================

@app.route("/webhook", methods=['POST'])
def webhook():
    """LINE Bot Webhook endpoint"""
    if not handler:
        return 'LINE Bot not configured', 500
    
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    
    return 'OK'

def _register_line_handlers():
    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        """處理 LINE 訊息"""
        user_text = event.message.text.strip()

        # 使用 asyncio 執行非同步查詢
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            # 檢查是否為「股票名稱 一周」格式
            if " 一周" in user_text or " 本周" in user_text:
                stock_name = user_text.replace(" 一周", "").replace(" 本周", "").strip()
                if stock_name:  # 有指定股票名稱
                    reply = loop.run_until_complete(get_stock_weekly_analysis_text(stock_name))
                else:  # 只輸入"一周"或"本周"
                    reply = loop.run_until_complete(get_weekly_analysis_text())
            elif user_text.startswith("查詢"):
                stock_name = user_text.replace("查詢", "").strip()
                reply = loop.run_until_complete(query_stock_news(stock_name))
            elif user_text == "熱門":
                reply = loop.run_until_complete(get_top_stocks_text())
            elif user_text == "最新":
                reply = loop.run_until_complete(get_latest_news_text())
            elif user_text == "正面":
                reply = loop.run_until_complete(get_sentiment_news_text(7, 9))
            elif user_text == "負面":
                reply = loop.run_until_complete(get_sentiment_news_text(1, 3))
            elif user_text == "一周" or user_text == "本周":
                reply = loop.run_until_complete(get_weekly_analysis_text())
            else:
                reply = """📊 股市新聞 Bot 指令說明：

查詢 [股票名稱] - 查詢特定股票新聞
熱門 - 最活躍的10檔股票
最新 - 最新5則新聞
正面 - 正面情緒新聞
負面 - 負面情緒新聞
一周 - 最近一周新聞分析
[股票名稱] 一周 - 特定股票一周分析

範例：
查詢 台積電
台積電 一周"""
        finally:
            loop.close()

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply)
        )

if handler:
    _register_line_handlers()


# ================= LINE Bot 查詢函式 =================

async def query_stock_news(stock_name):
    """查詢特定股票的新聞"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM (
                SELECT DISTINCT ON (title, stock_id)
                    title, sentiment_score, published_text, publisher, source, fetched_at
                FROM (
                    SELECT n.title, n.sentiment_score, n.published_text, n.publisher, 'Yahoo' as source, n.fetched_at, n.stock_id
                    FROM yahoo_stock_news n
                    LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
                    WHERE m.stock_name LIKE $1 OR n.stock_id LIKE $1
                    UNION ALL
                    SELECT n.title, n.sentiment_score, 
                           TO_CHAR(n.published_at, 'YYYY年MM月DD日 HH24:MI') as published_text,
                           n.category_name as publisher, '鉅亨網' as source, n.fetched_at, n.stock_id
                    FROM cnyes_stock_news n
                    LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
                    WHERE m.stock_name LIKE $1 OR n.stock_id LIKE $1
                    UNION ALL
                    SELECT n.title, n.sentiment_score,
                           TO_CHAR(n.published_at, 'YYYY年MM月DD日 HH24:MI') as published_text,
                           n.category as publisher, 'NStock' as source, n.fetched_at, n.stock_id
                    FROM nstock_stock_news n
                    LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
                    WHERE m.stock_name LIKE $1 OR n.stock_id LIKE $1
                ) AS combined
                ORDER BY title, stock_id, fetched_at DESC
            ) AS unique_news
            ORDER BY fetched_at DESC
            LIMIT 5
        """, f'%{stock_name}%')
    await pool.close()
    
    if not rows:
        return f"找不到「{stock_name}」的相關新聞"
    
    result = f"📰 {stock_name} 最新新聞：\n\n"
    for row in rows:
        emoji = "📈" if row['sentiment_score'] and row['sentiment_score'] >= 7 else "📉" if row['sentiment_score'] and row['sentiment_score'] <= 3 else "➡️"
        score = f"[{row['sentiment_score']}]" if row['sentiment_score'] else ""
        source_tag = f"[{row['source']}]"
        result += f"{emoji} {score} {source_tag} {row['title']}\n"
        result += f"   {row['publisher']} | {row['published_text']}\n\n"
    
    return result

async def get_top_stocks_text():
    """取得最活躍股票"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT COALESCE(m.stock_name, combined.stock_id) as stock_name, SUM(news_count) as news_count
            FROM (
                SELECT stock_id, COUNT(*) as news_count FROM yahoo_stock_news GROUP BY stock_id
                UNION ALL
                SELECT stock_id, COUNT(*) as news_count FROM cnyes_stock_news GROUP BY stock_id
                UNION ALL
                SELECT stock_id, COUNT(*) as news_count FROM nstock_stock_news GROUP BY stock_id
            ) AS combined
            LEFT JOIN stock_mapping m ON combined.stock_id = m.stock_id
            GROUP BY combined.stock_id, m.stock_name
            ORDER BY news_count DESC
            LIMIT 10
        """)
    await pool.close()
    
    result = "🔥 最活躍股票 TOP 10：\n\n"
    for i, row in enumerate(rows, 1):
        result += f"{i}. {row['stock_name']} ({row['news_count']} 則新聞)\n"
    
    return result

async def get_latest_news_text():
    """取得最新新聞"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM (
                SELECT DISTINCT ON (title, stock_name)
                    stock_name, title, sentiment_score, published_text, source, fetched_at
                FROM (
                    SELECT COALESCE(m.stock_name, n.stock_id) as stock_name,
                           n.title, n.sentiment_score, n.published_text, 'Yahoo' as source, n.fetched_at
                    FROM yahoo_stock_news n
                    LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
                    UNION ALL
                    SELECT COALESCE(m.stock_name, n.stock_id) as stock_name,
                           n.title, n.sentiment_score, 
                           TO_CHAR(n.published_at, 'YYYY年MM月DD日 HH24:MI') as published_text,
                           '鉅亨網' as source, n.fetched_at
                    FROM cnyes_stock_news n
                    LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
                    UNION ALL
                    SELECT COALESCE(m.stock_name, n.stock_id) as stock_name,
                           n.title, n.sentiment_score,
                           TO_CHAR(n.published_at, 'YYYY年MM月DD日 HH24:MI') as published_text,
                           'NStock' as source, n.fetched_at
                    FROM nstock_stock_news n
                    LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
                ) AS combined
                ORDER BY title, stock_name, fetched_at DESC
            ) AS unique_news
            ORDER BY fetched_at DESC
            LIMIT 5
        """)
    await pool.close()
    
    result = "📰 最新股市新聞：\n\n"
    for row in rows:
        emoji = "📈" if row['sentiment_score'] and row['sentiment_score'] >= 7 else "📉" if row['sentiment_score'] and row['sentiment_score'] <= 3 else "➡️"
        score = f"[{row['sentiment_score']}]" if row['sentiment_score'] else ""
        source_tag = f"[{row['source']}]"
        result += f"{emoji} {row['stock_name']} {score} {source_tag}\n{row['title']}\n\n"
    
    return result

async def get_sentiment_news_text(min_score, max_score):
    """取得特定情緒分數的新聞"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT stock_name, title, sentiment_score, source FROM (
                SELECT DISTINCT ON (title, stock_name)
                    stock_name, title, sentiment_score, source, fetched_at
                FROM (
                    SELECT COALESCE(m.stock_name, n.stock_id) as stock_name,
                           n.title, n.sentiment_score, 'Yahoo' as source, n.fetched_at
                    FROM yahoo_stock_news n
                    LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
                    WHERE n.sentiment_score BETWEEN $1 AND $2
                    UNION ALL
                    SELECT COALESCE(m.stock_name, n.stock_id) as stock_name,
                           n.title, n.sentiment_score, '鉅亨網' as source, n.fetched_at
                    FROM cnyes_stock_news n
                    LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
                    WHERE n.sentiment_score BETWEEN $1 AND $2
                    UNION ALL
                    SELECT COALESCE(m.stock_name, n.stock_id) as stock_name,
                           n.title, n.sentiment_score, 'NStock' as source, n.fetched_at
                    FROM nstock_stock_news n
                    LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
                    WHERE n.sentiment_score BETWEEN $1 AND $2
                ) AS combined
                ORDER BY title, stock_name, fetched_at DESC
            ) AS unique_news
            ORDER BY fetched_at DESC
            LIMIT 5
        """, min_score, max_score)
    await pool.close()
    
    sentiment_type = "正面" if min_score >= 7 else "負面" if max_score <= 3 else "中性"
    result = f"📊 {sentiment_type}情緒新聞：\n\n"
    
    if not rows:
        return f"目前沒有{sentiment_type}情緒的新聞"
    
    for row in rows:
        source_tag = f"[{row['source']}]"
        result += f"[{row['sentiment_score']}] {source_tag} {row['stock_name']}\n{row['title']}\n\n"
    
    return result

async def get_weekly_analysis_text():
    """取得一周新聞分析"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        week_ago = (datetime.now(ZoneInfo("Asia/Taipei")) - timedelta(days=7)).date()
        
        # 總新聞數
        total_news = await conn.fetchval("""
            SELECT COUNT(*) FROM (
                SELECT id FROM yahoo_stock_news WHERE fetched_date >= $1
                UNION ALL
                SELECT id FROM cnyes_stock_news WHERE fetched_date >= $1
                UNION ALL
                SELECT id FROM nstock_stock_news WHERE fetched_date >= $1
            ) AS combined
        """, week_ago)
        
        # 平均情緒
        avg_sentiment = await conn.fetchval("""
            SELECT AVG(sentiment_score) FROM (
                SELECT sentiment_score FROM yahoo_stock_news 
                WHERE sentiment_score IS NOT NULL AND fetched_date >= $1
                UNION ALL
                SELECT sentiment_score FROM cnyes_stock_news 
                WHERE sentiment_score IS NOT NULL AND fetched_date >= $1
                UNION ALL
                SELECT sentiment_score FROM nstock_stock_news
                WHERE sentiment_score IS NOT NULL AND fetched_date >= $1
            ) AS combined
        """, week_ago)
        
        # TOP 5 股票
        top_stocks = await conn.fetch("""
            SELECT COALESCE(m.stock_name, combined.stock_id) as stock_name, SUM(news_count) as news_count
            FROM (
                SELECT stock_id, COUNT(*) as news_count FROM yahoo_stock_news WHERE fetched_date >= $1 GROUP BY stock_id
                UNION ALL
                SELECT stock_id, COUNT(*) as news_count FROM cnyes_stock_news WHERE fetched_date >= $1 GROUP BY stock_id
                UNION ALL
                SELECT stock_id, COUNT(*) as news_count FROM nstock_stock_news WHERE fetched_date >= $1 GROUP BY stock_id
            ) AS combined
            LEFT JOIN stock_mapping m ON combined.stock_id = m.stock_id
            GROUP BY combined.stock_id, m.stock_name
            ORDER BY news_count DESC
            LIMIT 5
        """, week_ago)
        
        # 正負面新聞
        positive_count = await conn.fetchval("""
            SELECT COUNT(*) FROM (
                SELECT id FROM yahoo_stock_news WHERE sentiment_score >= 7 AND fetched_date >= $1
                UNION ALL
                SELECT id FROM cnyes_stock_news WHERE sentiment_score >= 7 AND fetched_date >= $1
                UNION ALL
                SELECT id FROM nstock_stock_news WHERE sentiment_score >= 7 AND fetched_date >= $1
            ) AS combined
        """, week_ago)
        
        negative_count = await conn.fetchval("""
            SELECT COUNT(*) FROM (
                SELECT id FROM yahoo_stock_news WHERE sentiment_score <= 3 AND fetched_date >= $1
                UNION ALL
                SELECT id FROM cnyes_stock_news WHERE sentiment_score <= 3 AND fetched_date >= $1
                UNION ALL
                SELECT id FROM nstock_stock_news WHERE sentiment_score <= 3 AND fetched_date >= $1
            ) AS combined
        """, week_ago)
        
    await pool.close()
    
    sentiment_emoji = "📈" if avg_sentiment and avg_sentiment >= 6 else "📉" if avg_sentiment and avg_sentiment <= 4 else "➡️"
    
    result = f"""📊 最近一周新聞分析

📰 總新聞數：{total_news} 則
{sentiment_emoji} 平均情緒：{round(avg_sentiment, 2) if avg_sentiment else 'N/A'}
📈 正面新聞：{positive_count} 則
📉 負面新聞：{negative_count} 則

🔥 本周最熱門股票：
"""
    
    for i, row in enumerate(top_stocks, 1):
        result += f"{i}. {row['stock_name']} ({row['news_count']} 則)\n"
    
    return result

async def get_stock_weekly_analysis_text(stock_name):
    """取得特定股票的一周新聞分析"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        week_ago = (datetime.now(ZoneInfo("Asia/Taipei")) - timedelta(days=7)).date()
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        
        # 格式化日期範圍
        week_ago_str = week_ago.strftime('%m/%d')
        yesterday_str = (today - timedelta(days=1)).strftime('%m/%d')
        
        # 查詢該股票的一周新聞
        news_rows = await conn.fetch("""
            SELECT * FROM (
                SELECT DISTINCT ON (title, stock_id)
                    title, sentiment_score, published_text, publisher, source, fetched_at
                FROM (
                    SELECT n.title, n.sentiment_score, n.published_text, n.publisher, 'Yahoo' as source, n.fetched_at, n.stock_id
                    FROM yahoo_stock_news n
                    LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
                    WHERE (m.stock_name LIKE $1 OR n.stock_id LIKE $1)
                      AND n.fetched_date >= $2
                    UNION ALL
                    SELECT n.title, n.sentiment_score, 
                           TO_CHAR(n.published_at, 'YYYY年MM月DD日 HH24:MI') as published_text,
                           n.category_name as publisher, '鉅亨網' as source, n.fetched_at, n.stock_id
                    FROM cnyes_stock_news n
                    LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
                    WHERE (m.stock_name LIKE $1 OR n.stock_id LIKE $1)
                      AND n.fetched_date >= $2
                    UNION ALL
                    SELECT n.title, n.sentiment_score,
                           TO_CHAR(n.published_at, 'YYYY年MM月DD日 HH24:MI') as published_text,
                           n.category as publisher, 'NStock' as source, n.fetched_at, n.stock_id
                    FROM nstock_stock_news n
                    LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
                    WHERE (m.stock_name LIKE $1 OR n.stock_id LIKE $1)
                      AND n.fetched_date >= $2
                ) AS combined
                ORDER BY title, stock_id, fetched_at DESC
            ) AS unique_news
            ORDER BY fetched_at DESC
        """, f'%{stock_name}%', week_ago)
        
        if not news_rows:
            await pool.close()
            return f"找不到「{stock_name}」在最近一周的新聞資料"
        
        # 統計資料
        total_news = len(news_rows)
        
        # 計算平均情緒
        sentiment_scores = [row['sentiment_score'] for row in news_rows if row['sentiment_score'] is not None]
        avg_sentiment = sum(sentiment_scores) / len(sentiment_scores) if sentiment_scores else None
        
        # 正負面新聞數
        positive_count = sum(1 for score in sentiment_scores if score >= 7)
        negative_count = sum(1 for score in sentiment_scores if score <= 3)
        
    await pool.close()
    
    # 判斷整體情緒
    sentiment_emoji = "📈" if avg_sentiment and avg_sentiment >= 6 else "📉" if avg_sentiment and avg_sentiment <= 4 else "➡️"
    sentiment_desc = "偏正面" if avg_sentiment and avg_sentiment >= 6 else "偏負面" if avg_sentiment and avg_sentiment <= 4 else "中性"
    
    # 組合回覆訊息
    result = f"""📊 {stock_name} 一周新聞分析
📅 時間範圍：{week_ago_str} - {yesterday_str}

📰 總新聞數：{total_news} 則
{sentiment_emoji} 平均情緒：{round(avg_sentiment, 2) if avg_sentiment else 'N/A'} ({sentiment_desc})
📈 正面新聞：{positive_count} 則
📉 負面新聞：{negative_count} 則

📝 最新 5 則新聞：
"""
    
    # 添加最新5則新聞
    for i, row in enumerate(news_rows[:5], 1):
        emoji = "📈" if row['sentiment_score'] and row['sentiment_score'] >= 7 else "📉" if row['sentiment_score'] and row['sentiment_score'] <= 3 else "➡️"
        score = f"[{row['sentiment_score']}]" if row['sentiment_score'] else "[N/A]"
        source_tag = f"[{row['source']}]"
        result += f"\n{i}. {emoji} {score} {source_tag}\n   {row['title']}\n"
    
    return result

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)


