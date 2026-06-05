import asyncio
import json
import re
import os
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))
import time
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
from openai import AsyncOpenAI

# ================= 配置 =================
PG_DSN = "postgresql://postgres:lab529@localhost:5432/postgres"
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
MAX_CONCURRENCY = 2  # API 並發數 (降低以避免 429 錯誤)
BATCH_SIZE = None  # 設為 None 表示一次處理全部，或設為數字如 100 表示分批執行

# 可選：只分析最近 N 天的新聞
DAYS_LIMIT = 3  # 設為 None 表示全部分析，或設為數字如 3 表示只分析 3 天內的

UPDATE_SQL = """
UPDATE public.cnyes_stock_news
SET sentiment_score = $1, themes = $2
WHERE id = $3;
"""

# ================= 核心函式 =================

async def analyze_news_async(client: AsyncOpenAI, text: str) -> tuple:
    """非同步呼叫 NVIDIA API 進行情緒分析 + 主題標記"""
    if not text or len(text) < 10:
        return None, None
    if not NVIDIA_API_KEY or "你的_NVIDIA" in NVIDIA_API_KEY:
        return None, None

    content = text[:2000] if len(text) > 2000 else text

    prompt = f"""你是台股新聞分析師。分析以下新聞，回傳 JSON（不要加 markdown 格式）：
{{"sentiment": 數字1-9, "themes": ["主題1", "主題2"]}}

規則：
- sentiment: 1(極度負面) ~ 5(中性) ~ 9(極度正面)
- themes: 1~3 個產業/概念主題，用繁體中文
- 不要用公司名稱當主題，用產業概念（例：台積電→半導體）
- 主題範例：AI伺服器、半導體、先進封裝、記憶體、電動車、綠能、生技、航運、金融、軍工、網通、雲端、消費電子

新聞：{content}"""

    try:
        completion = await client.chat.completions.create(
            model="meta/llama-3.1-8b-instruct",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=100,
            top_p=1
        )
        raw = completion.choices[0].message.content.strip()
        m = re.search(r'\{[^{}]*\}', raw)
        if not m:
            num = re.search(r'(\d+)', raw)
            return (max(1, min(9, int(num.group(1)))), None) if num else (None, None)

        data = json.loads(m.group())
        score = max(1, min(9, int(data.get("sentiment", 5))))
        themes = [str(t).strip()[:20] for t in data.get("themes", []) if t][:3]
        return score, themes if themes else None

    except Exception as e:
        print(f"  [WARN] 分析失敗: {e}")
        return None, None

async def analyze_and_update(sem: asyncio.Semaphore, client: AsyncOpenAI, db_pool: asyncpg.Pool, record: dict, progress: dict):
    """分析並更新單篇新聞的情緒分數 + 主題標記"""
    async with sem:
        news_id = record['id']
        stock_id = record['stock_id']
        title = record['title']
        content = record['content']

        text = content if content else title

        await asyncio.sleep(random.uniform(15.0, 20.0))

        sentiment_score, themes = await analyze_news_async(client, text)

        if sentiment_score is None:
            print(f"  [SKIP] ({stock_id}) 分析失敗")
            progress['failed'] += 1
            return

        try:
            async with db_pool.acquire() as conn:
                await conn.execute(UPDATE_SQL, sentiment_score, themes, news_id)

            progress['success'] += 1
            if progress['success'] % 10 == 0:
                print(f"  [PROGRESS] {progress['success']}/{progress['total']} completed ({progress['failed']} failed)")

        except Exception as e:
            print(f"  [ERROR] ({stock_id}) Failed to update DB: {e}")
            progress['failed'] += 1

async def main():
    print("[START] Analyzing sentiment scores...")
    
    # 檢查 API Key
    if not NVIDIA_API_KEY:
        print("[ERROR] NVIDIA_API_KEY environment variable not set!")
        print("Please set it with: $env:NVIDIA_API_KEY='your_api_key'")
        return
    
    # 1. 連接資料庫
    try:
        pool = await asyncpg.create_pool(PG_DSN)
    except Exception as e:
        print(f"[ERROR] Database connection failed: {e}")
        return
    
    # 2. 查詢需要分析的新聞
    async with pool.acquire() as conn:
        # 確保 themes 欄位存在
        await conn.execute("""
            ALTER TABLE public.cnyes_stock_news
            ADD COLUMN IF NOT EXISTS themes TEXT[];
        """)

    query = """
        SELECT id, stock_id, title, content
        FROM cnyes_stock_news
        WHERE (sentiment_score IS NULL OR themes IS NULL)
    """
    
    # 如果有日期限制
    if DAYS_LIMIT:
        cutoff_date = datetime.now(ZoneInfo("Asia/Taipei")) - timedelta(days=DAYS_LIMIT)
        query += f" AND published_at >= '{cutoff_date.isoformat()}'"
    
    query += " ORDER BY fetched_at DESC"
    
    # 如果有批次限制
    if BATCH_SIZE:
        query += f" LIMIT {BATCH_SIZE}"
    
    async with pool.acquire() as conn:
        records = await conn.fetch(query)
    
    total = len(records)
    print(f"Found {total} news items to analyze")
    
    if total == 0:
        print("[DONE] No news to process!")
        await pool.close()
        return
    
    # 3. 初始化 AI Client
    ai_client = AsyncOpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=NVIDIA_API_KEY
    )
    
    # 4. 建立並發控制
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    progress = {'success': 0, 'failed': 0, 'total': total}
    
    tasks = []
    for record in records:
        task = analyze_and_update(sem, ai_client, pool, record, progress)
        tasks.append(task)
    
    print(f"\n[START] Processing {len(tasks)} tasks (max concurrency: {MAX_CONCURRENCY})...\n")
    await asyncio.gather(*tasks)
    
    await pool.close()
    
    print(f"\n[DONE] Sentiment analysis complete!")
    print(f"Success: {progress['success']}")
    print(f"Failed: {progress['failed']}")
    print(f"Total: {progress['total']}")

if __name__ == "__main__":
    import os
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    start_time = time.time()
    asyncio.run(main())
    print(f"[TIME] Total: {time.time() - start_time:.2f} seconds")
