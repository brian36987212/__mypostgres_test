import asyncio
import json
import re
import os
import random
import time
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urljoin

from curl_cffi.requests import AsyncSession
import asyncpg
import pandas as pd
from bs4 import BeautifulSoup
from openai import AsyncOpenAI

# ================= 爬蟲等級配置 =================
CRAWLER_TIER = "中間股"  # ⚡ 識別標記
STOCK_FILE = "../../stocks_category/股票代號_中間_v2.csv"
PROGRESS_FILE = "progress_mid.txt"
DAYS_FILTER = 7  # 🔥 過濾天數：抓 7 天內新聞
MAX_CONCURRENCY = 3  # 🔥 並發數（平衡模式）

# 延遲範圍 (秒)
STOCK_DELAY_RANGE = (3.0, 6.0)  # 股票間延遲
NEWS_DELAY_RANGE = (1.5, 3.5)   # 新聞間延遲

# ================= 通用設定區 =================

# 建議將 Key 放在環境變數
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")

BASE_URL = "https://tw.stock.yahoo.com"

# Postgres 設定 (asyncpg 使用 dsn 字串或拆開參數皆可)
PG_DSN = "postgresql://postgres:lab529@localhost:5432/postgres"

# 模擬真實瀏覽器的完整 Headers
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "cache-control": "max-age=0",
}

WEEKDAY_ZH = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]

# SQL 語法修正：asyncpg 使用 $1, $2 佔位符，而不是 %s
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.yahoo_stock_news (
  id BIGSERIAL PRIMARY KEY,
  stock_id TEXT NOT NULL,
  title TEXT NOT NULL,
  publisher TEXT,
  reporter TEXT,
  published_text TEXT,
  content TEXT,
  sentiment_score INTEGER,
  url TEXT NOT NULL,
  fetched_at TIMESTAMPTZ,
  fetched_date DATE,
  fetched_time TIME,
  UNIQUE (stock_id, url)
);
"""

UPSERT_SQL = """
INSERT INTO public.yahoo_stock_news
(stock_id, title, publisher, reporter, published_text, content, sentiment_score, url, fetched_at, fetched_date, fetched_time)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
ON CONFLICT (stock_id, url) DO UPDATE SET
  title = EXCLUDED.title,
  publisher = EXCLUDED.publisher,
  reporter = EXCLUDED.reporter,
  published_text = EXCLUDED.published_text,
  content = EXCLUDED.content,
  sentiment_score = EXCLUDED.sentiment_score,
  fetched_at = EXCLUDED.fetched_at,
  fetched_date = EXCLUDED.fetched_date,
  fetched_time = EXCLUDED.fetched_time;
"""

# ================= 輔助函式區 (維持同步邏輯的部分) =================

def format_time_yahoo_tw(iso_z: str):
    if not iso_z: return None, None
    try:
        s = iso_z.strip()
        if s.endswith("Z"): s = s.replace("Z", "+00:00")
        dt_utc = datetime.fromisoformat(s)
        dt = dt_utc.astimezone(ZoneInfo("Asia/Taipei"))
        ampm = "上午" if dt.hour < 12 else "下午"
        hour_12 = dt.hour % 12 or 12
        weekday = WEEKDAY_ZH[dt.weekday()]
        fmt_str = f"{dt.year}年{dt.month}月{dt.day}日 {weekday} {ampm}{hour_12}:{dt.minute:02d}"
        return fmt_str, dt
    except Exception:
        return None, None

def strip_publisher_from_reporter(reporter: str | None, publisher: str | None) -> str | None:
    if not reporter: return None
    r = reporter.strip()
    if not r: return None
    known_publishers = ["工商時報", "經濟日報", "時報資訊", "中央社", "Yahoo財經", "Yahoo", 
                        "中時新聞網", "中時", "旺報", "財訊快報", "財訊", 
                        "東森財經", "東森新聞", "東森", "鉅亨網"]
    for pub in known_publishers:
        r = r.replace(pub, "")
    if publisher:
        p_full = publisher.strip()
        if p_full:
            r = r.replace(p_full, "")
            r = re.sub(r"(新聞網|新聞|電子報|財經|報|網)$", "", r)
    r = re.split(r"(台北|新北|台中|高雄|台南|桃園|新竹|綜合外電|外電|報導|電\s*[）)])", r)[0]
    r = re.sub(r"(記者|特派|派駐|採訪|整理|編輯|專欄)", "", r)
    r = re.sub(r"[／/｜|：:\[\]【】\(\)\s]", " ", r)
    r = re.sub(r"\s+", " ", r).strip()
    if len(r) > 10 or len(r) < 2: return None
    return r

# ================= 非同步核心區 =================

async def get_html_async(session: AsyncSession, url: str, referer: str = None) -> str:
    """非同步取得 HTML，使用 curl_cffi 模擬真實瀏覽器，包含指數退避重試機制"""
    retries = 3
    base_delay = 2.0
    
    for i in range(retries):
        try:
            headers = HEADERS.copy()
            if referer:
                headers["referer"] = referer
            
            # 使用 impersonate 參數模擬真實瀏覽器的 TLS 指紋
            response = await session.get(
                url, 
                headers=headers, 
                timeout=20,
                impersonate="chrome131",  # 🔥 關鍵：模擬 Chrome 131 的 TLS 指紋
                allow_redirects=True
            )
            
            if response.status_code == 200:
                return response.text
            elif response.status_code in [403, 429]:  # 被封鎖或限流
                if i < retries - 1:
                    # 指數退避 + 隨機抖動
                    delay = base_delay * (2 ** i) + random.uniform(0, 2)
                    print(f"⚠️ 狀態碼 {response.status_code}，等待 {delay:.1f}s 後重試... ({url})")
                    await asyncio.sleep(delay)
                else:
                    print(f"❌ 請求被拒絕 ({url}): Status {response.status_code}")
                    return None
            else:
                response.raise_for_status()
                return response.text
                
        except Exception as e:
            if i == retries - 1:
                print(f"⚠️ 請求失敗 ({url}): {e}")
                return None
            # 指數退避
            delay = base_delay * (2 ** i) + random.uniform(0, 1)
            await asyncio.sleep(delay)
    
    return None

async def get_nvidia_sentiment_score_async(client: AsyncOpenAI, text: str) -> int:
    """非同步呼叫 NVIDIA/OpenAI API"""
    if not text or len(text) < 10: return None
    if not NVIDIA_API_KEY or "你的_NVIDIA" in NVIDIA_API_KEY: return None # 防呆

    prompt = f"""
    你是一個專業的金融情緒分析師。請閱讀以下新聞文章內容，並給出一個 1 到 9 的情緒分數。
    評分標準：1分(極度負面) ~ 5分(中性) ~ 9分(極度正面)。
    請只回答一個數字 (1-9)，不要有任何解釋。
    文章內容：{text[:2000]}
    """
    try:
        completion = await client.chat.completions.create(
            model="meta/llama-3.1-8b-instruct",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=10, top_p=1
        )
        result = completion.choices[0].message.content.strip()
        match = re.search(r"(\d+)", result)
        if match:
            score = int(match.group(1))
            return max(1, min(9, score))
        return None
    except Exception as e:
        print(f"⚠️ 情緒分析失敗: {e}")
        return None

def parse_list_page(html: str):
    """解析列表頁 (CPU bound，但在這規模下直接跑即可)"""
    if not html: return []
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if "/news/" not in href: continue
        list_title = a.get_text(strip=True)
        if not list_title or list_title == "新聞" or list_title.isdigit(): continue
        
        url = urljoin(BASE_URL, href).split("?")[0]
        if url in seen: continue
        seen.add(url)
        results.append((list_title, url))
    return results

def parse_detail_page(html: str):
    """解析內頁"""
    if not html: return None
    soup = BeautifulSoup(html, "lxml")
    
    headline, publisher, reporter_jsonld, published_text, content, published_dt = None, None, None, None, None, None

    # JSON-LD
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string
        if not raw: continue
        try:
            data = json.loads(raw.strip())
            objs = data if isinstance(data, list) else [data]
            if isinstance(data, dict) and isinstance(data.get("@graph"), list): objs = data["@graph"]
            for obj in objs:
                if obj.get("@type") == "NewsArticle":
                    headline = obj.get("headline")
                    if isinstance(obj.get("provider"), dict): publisher = obj["provider"].get("name")
                    if isinstance(obj.get("author"), dict): reporter_jsonld = obj["author"].get("name")
                    iso_time = obj.get("datePublished") or obj.get("dateModified")
                    if iso_time: published_text, published_dt = format_time_yahoo_tw(iso_time)
                    break
        except: continue

    # Meta Backup
    if not reporter_jsonld:
        meta = soup.find("meta", attrs={"name": "description"})
        if meta:
            m = re.search(r"記者\s*([^\s／/】]+)", meta.get("content", ""))
            if m: reporter_jsonld = m.group(1).strip()

    reporter = strip_publisher_from_reporter(reporter_jsonld, publisher)
    
    content_div = soup.find("div", class_="caas-body")
    content = content_div.get_text(separator="\n", strip=True) if content_div else ""
    if not content:
        ps = soup.find_all("p")
        if ps: content = "\n".join([p.get_text(strip=True) for p in ps])

    return headline, publisher, reporter, published_text, content, published_dt

def mark_stock_done(stock_code: str):
    """記錄已完成的股票"""
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{stock_code}\n")

def load_processed_stocks():
    """載入已處理的股票清單"""
    if not os.path.exists(PROGRESS_FILE):
        return set()
    with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

async def process_stock(sem: asyncio.Semaphore, session: AsyncSession, db_pool: asyncpg.Pool, ai_client: AsyncOpenAI, stock_code: str):
    """處理單一股票的完整流程"""
    
    # 使用 Semaphore 控制並發數量，避免被鎖 IP
    async with sem:
        # 決定 URL
        if ".TW" not in stock_code and ".TWO" not in stock_code:
            target_url = f"https://tw.stock.yahoo.com/quote/{stock_code}.TW/news"
        else:
            target_url = f"https://tw.stock.yahoo.com/quote/{stock_code}/news"

        # 🔥 使用配置的延遲範圍
        await asyncio.sleep(random.uniform(*STOCK_DELAY_RANGE))
        
        # 1. 抓取列表
        list_html = await get_html_async(session, target_url, referer=BASE_URL)
        news_list = parse_list_page(list_html)
        
        if not news_list:
            print(f"💤 ({stock_code}) 無新聞列表")
            mark_stock_done(stock_code)
            return

        print(f"🚀 ({stock_code}) 掃描到 {len(news_list)} 則新聞，開始解析...")
        
        today_date = datetime.now(ZoneInfo("Asia/Taipei")).date()
        processed_count = 0
        
        # 2. 逐一處理該股票的新聞
        # 這裡我們選擇「依序」處理該股票的新聞，避免單一股票同時發出太多內頁請求
        for list_title, url in news_list:
            
            # 2.1 抓取內頁（加入 referer）
            await asyncio.sleep(random.uniform(*NEWS_DELAY_RANGE))  # 🔥 使用配置的延遲範圍
            detail_html = await get_html_async(session, url, referer=target_url)
            detail = parse_detail_page(detail_html)
            if not detail: continue

            headline, publisher, reporter, published_text, content, pub_dt = detail
            
            # 2.2 日期過濾 🔥 使用配置的天數過濾
            if not pub_dt: continue
            days_diff = (today_date - pub_dt.date()).days
            if days_diff > DAYS_FILTER:
                continue

            final_title = headline or list_title
            
            # 2.3 情緒分析 (這一步比較慢，非同步優勢最大)
            sentiment_score = await get_nvidia_sentiment_score_async(ai_client, content)
            
            # 2.4 寫入資料庫
            try:
                now_dt = datetime.now(ZoneInfo("Asia/Taipei"))
                async with db_pool.acquire() as conn:
                    await conn.execute(UPSERT_SQL, 
                        stock_code, final_title, publisher, reporter, published_text, 
                        content, sentiment_score, url, now_dt, now_dt.date(), now_dt.time()
                    )
                processed_count += 1
                print(f"   ✅ ({stock_code}) 分數:{sentiment_score} | {final_title[:10]}...")
            except Exception as e:
                print(f"   ❌ ({stock_code}) DB 錯誤: {e}")

        if processed_count > 0:
            print(f"💾 ({stock_code}) 完成，共存入 {processed_count} 則新聞")
        
        # 記錄進度（無論有沒有新聞都記錄，避免重複掃描）
        mark_stock_done(stock_code)

async def main():
    # 1. 讀取 CSV
    try:
        df = pd.read_csv(STOCK_FILE, dtype=str)
        all_stocks = df.iloc[:, 0].dropna().tolist()
        
        # 載入已處理的股票
        processed = load_processed_stocks()
        stock_list = [s for s in all_stocks if s not in processed]
        
        print(f"⚡ [{CRAWLER_TIER}] 爬蟲啟動（平衡模式）")
        print(f"📄 總共 {len(all_stocks)} 支股票")
        print(f"✅ 已完成 {len(processed)} 支")
        print(f"🔄 待處理 {len(stock_list)} 支")
        print(f"⚙️  配置：並發={MAX_CONCURRENCY}, 天數過濾={DAYS_FILTER}天")
        
        if len(stock_list) == 0:
            print("🎉 全部股票已處理完成！")
            return
    except Exception as e:
        print(f"❌ 讀取檔案失敗: {e}")
        return

    # 2. 初始化資源 (DB Pool, OpenAI Client)
    try:
        pool = await asyncpg.create_pool(PG_DSN)
        async with pool.acquire() as conn:
            await conn.execute(CREATE_TABLE_SQL)
    except Exception as e:
        print(f"❌ 資料庫連線失敗: {e}")
        return

    ai_client = AsyncOpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=NVIDIA_API_KEY)

    # 3. 建立並發控制與 Session
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    
    # 使用 curl_cffi 的 AsyncSession，支援 impersonate
    async with AsyncSession() as session:
        tasks = []
        # 打亂順序避免被偵測模式
        random.shuffle(stock_list)
        
        for stock_code in stock_list:
            stock_code = str(stock_code).strip()
            if not stock_code: continue
            
            # 建立任務但不需立刻等待，放到列表裡
            task = process_stock(sem, session, pool, ai_client, stock_code)
            tasks.append(task)
        
        # 4. 開始執行所有任務
        print(f"⚡ 開始並行處理 {len(tasks)} 個任務 (最大並發: {MAX_CONCURRENCY})...\n")
        await asyncio.gather(*tasks)

    # 5. 關閉資源
    await pool.close()
    print(f"\n🎉 [{CRAWLER_TIER}] 全部處理完成！")

if __name__ == "__main__":
    # Windows 系統上的 asyncio fix
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    start_time = time.time()
    asyncio.run(main())
    print(f"⏱️ 總耗時: {time.time() - start_time:.2f} 秒")
