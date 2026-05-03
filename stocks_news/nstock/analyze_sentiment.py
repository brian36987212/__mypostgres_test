import asyncio
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
MAX_CONCURRENCY = 2  # API 並發數 (1 避免 429)
BATCH_SIZE = None
DAYS_LIMIT = 3  # 只分析 3 天內的新聞

UPDATE_SENTIMENT_SQL = """
UPDATE public.nstock_stock_news
SET sentiment_score = $1
WHERE id = $2;
"""

async def get_nvidia_sentiment_score_async(client: AsyncOpenAI, text: str) -> int:
    """非同步呼叫 NVIDIA/OpenAI API 進行情緒分析"""
    if not text or len(text) < 10:
        return None
    if not NVIDIA_API_KEY:
        return None

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
        print(f"  [WARN] 情緒分析失敗: {e}")
        return None


async def analyze_and_update_sentiment(sem: asyncio.Semaphore, client: AsyncOpenAI, db_pool: asyncpg.Pool, record: dict, progress: dict):
    """分析並更新單篇新聞的情緒分數"""
    async with sem:
        news_id = record['id']
        stock_id = record['stock_id']
        title = record['title']
        content = record['content']
        
        text = content if content else title
        
        await asyncio.sleep(random.uniform(15.0, 20.0))
        
        sentiment_score = await get_nvidia_sentiment_score_async(client, text)
        
        if sentiment_score is None:
            print(f"  [X] ({stock_id}) 分析失敗")
            progress['failed'] += 1
            return
        
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(UPDATE_SENTIMENT_SQL, sentiment_score, news_id)
            
            progress['success'] += 1
            if progress['success'] % 10 == 0:
                print(f"  [INFO] 進度: {progress['success']}/{progress['total']} (失敗: {progress['failed']})")
            
        except Exception as e:
            print(f"  [X] ({stock_id}) 更新失敗: {e}")
            progress['failed'] += 1


async def main():
    print(f"\n{'='*60}")
    print("NStock 新聞情緒分析")
    print(f"{'='*60}\n")
    
    if not NVIDIA_API_KEY:
        print("[ERROR] 未設定 NVIDIA_API_KEY 環境變數!")
        print("請執行: $env:NVIDIA_API_KEY='your_api_key'")
        return
    
    try:
        pool = await asyncpg.create_pool(PG_DSN)
        print("[OK] 資料庫連線成功\n")
    except Exception as e:
        print(f"[ERROR] 資料庫連線失敗: {e}")
        return
    
    query = """
        SELECT id, stock_id, title, content
        FROM nstock_stock_news
        WHERE sentiment_score IS NULL
          AND content IS NOT NULL
    """
    
    if DAYS_LIMIT:
        cutoff_date = datetime.now(ZoneInfo("Asia/Taipei")) - timedelta(days=DAYS_LIMIT)
        query += f" AND published_at >= '{cutoff_date.isoformat()}'"
    
    query += " ORDER BY fetched_at DESC"
    
    if BATCH_SIZE:
        query += f" LIMIT {BATCH_SIZE}"
    
    async with pool.acquire() as conn:
        records = await conn.fetch(query)
    
    total = len(records)
    print(f"[INFO] 找到 {total} 筆需要分析的新聞\n")
    
    if total == 0:
        print("[OK] 沒有需要處理的新聞")
        await pool.close()
        return
    
    ai_client = AsyncOpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=NVIDIA_API_KEY
    )
    
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    progress = {'success': 0, 'failed': 0, 'total': total}
    start_time = time.time()
    
    tasks = []
    for record in records:
        task = analyze_and_update_sentiment(sem, ai_client, pool, record, progress)
        tasks.append(task)
    
    print(f"開始處理 {len(tasks)} 筆新聞 (並發數: {MAX_CONCURRENCY})...\n")
    await asyncio.gather(*tasks)
    
    await pool.close()
    
    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"[OK] 情緒分析完成!")
    print(f"{'='*60}")
    print(f"成功: {progress['success']} 筆")
    print(f"失敗: {progress['failed']} 筆")
    print(f"總計: {progress['total']} 筆")
    print(f"耗時: {elapsed:.1f} 秒")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    import os
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(main())
