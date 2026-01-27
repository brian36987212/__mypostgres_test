from flask import Flask, render_template, jsonify
import asyncpg
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

PG_DSN = "postgresql://postgres:lab529@localhost:5432/postgres"

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

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
