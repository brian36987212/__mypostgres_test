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
MAX_CONCURRENCY = 2  # API 並發數 (1 避免 429)
BATCH_SIZE = None
DAYS_LIMIT = int(os.getenv("SENTIMENT_DAYS_LIMIT", "3"))  # 只分析 N 天內新聞；weekly 批次會設較大值補上 lower/rare

UPDATE_SQL = """
UPDATE public.nstock_stock_news
SET sentiment_score = $1, themes = $2
WHERE id = $3;
"""

async def analyze_news_async(client: AsyncOpenAI, text: str) -> tuple:
    """非同步呼叫 NVIDIA API 進行情緒分析 + 主題標記
    回傳 (sentiment_score, themes_list)"""
    if not text or len(text) < 10:
        return None, None
    if not NVIDIA_API_KEY:
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
            # fallback: 嘗試只抓數字當情緒
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
            print(f"  [X] ({stock_id}) 分析失敗")
            progress['failed'] += 1
            return

        try:
            async with db_pool.acquire() as conn:
                await conn.execute(UPDATE_SQL, sentiment_score, themes, news_id)

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
    
    # 清理舊的空字串 content（改回 NULL，讓 fetch_content 重新補抓）
    async with pool.acquire() as conn:
        cleaned = await conn.execute("""
            UPDATE nstock_stock_news
            SET content = NULL
            WHERE content = '' AND sentiment_score IS NULL
        """)
        if cleaned != "UPDATE 0":
            print(f"[INFO] 清理空字串 content → NULL: {cleaned}\n")

        # 確保 themes 欄位存在
        await conn.execute("""
            ALTER TABLE public.nstock_stock_news
            ADD COLUMN IF NOT EXISTS themes TEXT[];
        """)

    query = """
        SELECT id, stock_id, title, content
        FROM nstock_stock_news
        WHERE (sentiment_score IS NULL OR themes IS NULL)
          AND content IS NOT NULL
          AND content != ''
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
        task = analyze_and_update(sem, ai_client, pool, record, progress)
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
