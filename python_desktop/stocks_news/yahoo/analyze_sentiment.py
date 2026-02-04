import asyncio
import re
import os
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
DAYS_LIMIT = None  # 設為 None 表示全部分析，或設為數字如 3 表示只分析 3 天內的

UPDATE_SENTIMENT_SQL = """
UPDATE public.yahoo_stock_news
SET sentiment_score = $1
WHERE id = $2;
"""

# ================= 核心函式 =================

async def get_nvidia_sentiment_score_async(client: AsyncOpenAI, text: str) -> int:
    """非同步呼叫 NVIDIA/OpenAI API 進行情緒分析"""
    if not text or len(text) < 10:
        return None
    if not NVIDIA_API_KEY or "你的_NVIDIA" in NVIDIA_API_KEY:
        return None

    # 優先使用內文，如果太長則截取前 2000 字
    content = text[:2000] if len(text) > 2000 else text
    
    prompt = f"""
    你是一個專業的金融情緒分析師。請閱讀以下新聞文章內容，並給出一個 1 到 9 的情緒分數。
    評分標準：1分(極度負面) ~ 5分(中性) ~ 9分(極度正面)。
    請只回答一個數字 (1-9)，不要有任何解釋。
    文章內容：{content}
    """
    
    try:
        completion = await client.chat.completions.create(
            model="meta/llama-3.1-8b-instruct",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=10,
            top_p=1
        )
        result = completion.choices[0].message.content.strip()
        match = re.search(r"(\d+)", result)
        if match:
            score = int(match.group(1))
            return max(1, min(9, score))
        return None
    except Exception as e:
        print(f"[WARN] Sentiment analysis failed: {e}")
        return None

async def analyze_and_update_sentiment(sem: asyncio.Semaphore, client: AsyncOpenAI, db_pool: asyncpg.Pool, record: dict, progress: dict):
    """分析並更新單篇新聞的情緒分數"""
    async with sem:
        news_id = record['id']
        stock_id = record['stock_id']
        title = record['title']
        content = record['content']
        
        # 優先使用內文，如果沒有則使用標題
        text = content if content else title
        
        # 加入延遲避免 API 速率限制
        await asyncio.sleep(random.uniform(0.5, 1.5))
        
        # 進行情緒分析
        sentiment_score = await get_nvidia_sentiment_score_async(client, text)
        
        if sentiment_score is None:
            print(f"[SKIP] ({stock_id}) Failed to analyze sentiment")
            progress['failed'] += 1
            return
        
        # 更新資料庫
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(UPDATE_SENTIMENT_SQL, sentiment_score, news_id)
            
            progress['success'] += 1
            if progress['success'] % 10 == 0:
                print(f"[PROGRESS] {progress['success']}/{progress['total']} completed ({progress['failed']} failed)")
            
        except Exception as e:
            print(f"[ERROR] ({stock_id}) Failed to update DB: {e}")
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
    query = """
        SELECT id, stock_id, title, content
        FROM yahoo_stock_news
        WHERE sentiment_score IS NULL
          AND content IS NOT NULL
    """
    
    # 如果有日期限制
    if DAYS_LIMIT:
        cutoff_date = datetime.now(ZoneInfo("Asia/Taipei")) - timedelta(days=DAYS_LIMIT)
        query += f" AND fetched_at >= '{cutoff_date.isoformat()}'"
    
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
        task = analyze_and_update_sentiment(sem, ai_client, pool, record, progress)
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
