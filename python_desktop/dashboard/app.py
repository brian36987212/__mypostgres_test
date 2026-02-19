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

load_dotenv()

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

PG_DSN = "postgresql://postgres:lab529@localhost:5432/postgres"

# LINE Bot 設定
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', '')
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', '')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN) if LINE_CHANNEL_ACCESS_TOKEN else None
handler = WebhookHandler(LINE_CHANNEL_SECRET) if LINE_CHANNEL_SECRET else None

async def get_db_pool():
    return await asyncpg.create_pool(PG_DSN)

@app.route('/')
def index():
    return render_template('index.html')

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
                SELECT id FROM nstock_stock_news WHERE DATE(fetched_at) = $1
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
                SELECT DATE(fetched_at) as fetched_date, COUNT(*) as count
                FROM nstock_stock_news
                WHERE fetched_at >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY DATE(fetched_at)
            ) AS combined
            GROUP BY fetched_date
            ORDER BY fetched_date
        """)
    await pool.close()
    
    return {
        'labels': [row['fetched_date'].strftime('%m/%d') for row in rows],
        'data': [row['count'] for row in rows]
    }

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
                SELECT id FROM nstock_stock_news WHERE DATE(fetched_at) >= $1
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
                WHERE sentiment_score IS NOT NULL AND DATE(fetched_at) >= $1
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
                SELECT stock_id, COUNT(*) as news_count FROM nstock_stock_news WHERE DATE(fetched_at) >= $1 GROUP BY stock_id
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
                SELECT id FROM nstock_stock_news WHERE sentiment_score >= 7 AND DATE(fetched_at) >= $1
            ) AS combined
        """, week_ago)
        
        negative_count = await conn.fetchval("""
            SELECT COUNT(*) FROM (
                SELECT id FROM yahoo_stock_news WHERE sentiment_score <= 3 AND fetched_date >= $1
                UNION ALL
                SELECT id FROM cnyes_stock_news WHERE sentiment_score <= 3 AND fetched_date >= $1
                UNION ALL
                SELECT id FROM nstock_stock_news WHERE sentiment_score <= 3 AND DATE(fetched_at) >= $1
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
                SELECT DATE(fetched_at) as fetched_date, sentiment_score FROM nstock_stock_news
                WHERE sentiment_score IS NOT NULL AND fetched_at >= CURRENT_DATE - INTERVAL '7 days'
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

# ================= LINE Bot 查詢函式 =================

async def query_stock_news(stock_name):
    """查詢特定股票的新聞"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT DISTINCT ON (title, stock_id)
                title, sentiment_score, published_text, publisher, source, fetched_at
            FROM (
                SELECT n.title, n.sentiment_score, n.published_text, n.publisher, 'Yahoo' as source, n.fetched_at
                FROM yahoo_stock_news n
                LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
                WHERE m.stock_name LIKE $1 OR n.stock_id LIKE $1
                UNION ALL
                SELECT n.title, n.sentiment_score, 
                       TO_CHAR(n.published_at, 'YYYY年MM月DD日 HH24:MI') as published_text,
                       n.category_name as publisher, '鉅亨網' as source, n.fetched_at
                FROM cnyes_stock_news n
                LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
                WHERE m.stock_name LIKE $1 OR n.stock_id LIKE $1
                UNION ALL
                SELECT n.title, n.sentiment_score,
                       TO_CHAR(n.published_at, 'YYYY年MM月DD日 HH24:MI') as published_text,
                       n.category as publisher, 'NStock' as source, n.fetched_at
                FROM nstock_stock_news n
                LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
                WHERE m.stock_name LIKE $1 OR n.stock_id LIKE $1
            ) AS combined
            ORDER BY title, stock_id, fetched_at DESC
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
            SELECT DISTINCT ON (title, stock_name)
                stock_name, title, sentiment_score, source
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
                SELECT id FROM nstock_stock_news WHERE DATE(fetched_at) >= $1
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
                WHERE sentiment_score IS NOT NULL AND DATE(fetched_at) >= $1
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
                SELECT stock_id, COUNT(*) as news_count FROM nstock_stock_news WHERE DATE(fetched_at) >= $1 GROUP BY stock_id
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
                SELECT id FROM nstock_stock_news WHERE sentiment_score >= 7 AND DATE(fetched_at) >= $1
            ) AS combined
        """, week_ago)
        
        negative_count = await conn.fetchval("""
            SELECT COUNT(*) FROM (
                SELECT id FROM yahoo_stock_news WHERE sentiment_score <= 3 AND fetched_date >= $1
                UNION ALL
                SELECT id FROM cnyes_stock_news WHERE sentiment_score <= 3 AND fetched_date >= $1
                UNION ALL
                SELECT id FROM nstock_stock_news WHERE sentiment_score <= 3 AND DATE(fetched_at) >= $1
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
                  AND DATE(n.fetched_at) >= $2
            ) AS combined
            ORDER BY title, stock_id, fetched_at DESC
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


