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

PG_DSN = os.getenv('DATABASE_URL', 'postgresql://postgres:lab529@localhost:5432/postgres')

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
        # 總新聞數
        total_news = await conn.fetchval("SELECT COUNT(*) FROM yahoo_stock_news")
        
        # 總股票數
        total_stocks = await conn.fetchval("SELECT COUNT(DISTINCT stock_id) FROM yahoo_stock_news")
        
        # 今日新聞數
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        today_news = await conn.fetchval(
            "SELECT COUNT(*) FROM yahoo_stock_news WHERE fetched_date = $1", today
        )
        
        # 平均情緒分數
        avg_sentiment = await conn.fetchval(
            "SELECT AVG(sentiment_score) FROM yahoo_stock_news WHERE sentiment_score IS NOT NULL"
        )
    
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
            FROM yahoo_stock_news
            WHERE sentiment_score IS NOT NULL
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
            SELECT n.stock_id, COALESCE(m.stock_name, n.stock_id) as stock_name, COUNT(*) as news_count
            FROM yahoo_stock_news n
            LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
            GROUP BY n.stock_id, m.stock_name
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
            SELECT n.stock_id, COALESCE(m.stock_name, n.stock_id) as stock_name,
                   n.title, n.publisher, n.sentiment_score, n.published_text, n.fetched_at
            FROM yahoo_stock_news n
            LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
            ORDER BY n.fetched_at DESC
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
        'fetched_at': row['fetched_at'].isoformat() if row['fetched_at'] else None
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
            SELECT fetched_date, COUNT(*) as count
            FROM yahoo_stock_news
            WHERE fetched_date >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY fetched_date
            ORDER BY fetched_date
        """)
    await pool.close()
    
    return {
        'labels': [row['fetched_date'].strftime('%m/%d') for row in rows],
        'data': [row['count'] for row in rows]
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
        if user_text.startswith("查詢"):
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
        else:
            reply = """📊 股市新聞 Bot 指令說明：

查詢 [股票名稱] - 查詢特定股票新聞
熱門 - 最活躍的10檔股票
最新 - 最新5則新聞
正面 - 正面情緒新聞
負面 - 負面情緒新聞

範例：查詢 台積電"""
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
            SELECT n.title, n.sentiment_score, n.published_text, n.publisher
            FROM yahoo_stock_news n
            LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
            WHERE m.stock_name LIKE $1 OR n.stock_id LIKE $1
            ORDER BY n.fetched_at DESC
            LIMIT 5
        """, f'%{stock_name}%')
    await pool.close()
    
    if not rows:
        return f"找不到「{stock_name}」的相關新聞"
    
    result = f"📰 {stock_name} 最新新聞：\n\n"
    for row in rows:
        emoji = "📈" if row['sentiment_score'] and row['sentiment_score'] >= 7 else "📉" if row['sentiment_score'] and row['sentiment_score'] <= 3 else "➡️"
        score = f"[{row['sentiment_score']}]" if row['sentiment_score'] else ""
        result += f"{emoji} {score} {row['title']}\n"
        result += f"   {row['publisher']} | {row['published_text']}\n\n"
    
    return result

async def get_top_stocks_text():
    """取得最活躍股票"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT COALESCE(m.stock_name, n.stock_id) as stock_name, COUNT(*) as news_count
            FROM yahoo_stock_news n
            LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
            GROUP BY n.stock_id, m.stock_name
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
            SELECT COALESCE(m.stock_name, n.stock_id) as stock_name,
                   n.title, n.sentiment_score, n.published_text
            FROM yahoo_stock_news n
            LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
            ORDER BY n.fetched_at DESC
            LIMIT 5
        """)
    await pool.close()
    
    result = "📰 最新股市新聞：\n\n"
    for row in rows:
        emoji = "📈" if row['sentiment_score'] and row['sentiment_score'] >= 7 else "📉" if row['sentiment_score'] and row['sentiment_score'] <= 3 else "➡️"
        score = f"[{row['sentiment_score']}]" if row['sentiment_score'] else ""
        result += f"{emoji} {row['stock_name']} {score}\n{row['title']}\n\n"
    
    return result

async def get_sentiment_news_text(min_score, max_score):
    """取得特定情緒分數的新聞"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT COALESCE(m.stock_name, n.stock_id) as stock_name,
                   n.title, n.sentiment_score
            FROM yahoo_stock_news n
            LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
            WHERE n.sentiment_score BETWEEN $1 AND $2
            ORDER BY n.fetched_at DESC
            LIMIT 5
        """, min_score, max_score)
    await pool.close()
    
    sentiment_type = "正面" if min_score >= 7 else "負面" if max_score <= 3 else "中性"
    result = f"📊 {sentiment_type}情緒新聞：\n\n"
    
    if not rows:
        return f"目前沒有{sentiment_type}情緒的新聞"
    
    for row in rows:
        result += f"[{row['sentiment_score']}] {row['stock_name']}\n{row['title']}\n\n"
    
    return result

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)

