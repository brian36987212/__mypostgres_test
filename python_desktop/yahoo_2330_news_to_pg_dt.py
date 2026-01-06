import json
import os
import re
from datetime import datetime, date
from zoneinfo import ZoneInfo
from urllib.parse import urljoin

import requests
import psycopg2
from bs4 import BeautifulSoup
from openai import OpenAI

# ================= 安全設定區（不寫死憑證） =================
# 從環境變數讀取：
# - NVIDIA_API_KEY：你的 NVIDIA NIM API Key
# - PG_DSN：PostgreSQL DSN（避免把 DB 密碼寫進 Git）
#
# 你也可以建立 .env 檔並在本機載入（見下方說明），但 .env 不要提交到 GitHub。

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")

BASE_URL = "https://tw.stock.yahoo.com"
LIST_URL = "https://tw.stock.yahoo.com/quote/2330.TW/news"
PG_DSN = "host=localhost port=5432 dbname=postgres user=postgres password=lab529"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9",
}

WEEKDAY_ZH = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]

# SQL 設定保持不變
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.yahoo_2330_news (
  id BIGSERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  publisher TEXT,
  reporter TEXT,
  published_text TEXT,
  content TEXT,
  sentiment_score INTEGER,
  url TEXT NOT NULL UNIQUE,
  fetched_at TIMESTAMPTZ,
  fetched_date DATE,
  fetched_time TIME
);
"""

UPSERT_SQL = """
INSERT INTO public.yahoo_2330_news
(title, publisher, reporter, published_text, content, sentiment_score, url, fetched_at, fetched_date, fetched_time)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (url) DO UPDATE SET
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

# ================= 功能函式區 =================

def get_nvidia_sentiment_score(text: str) -> int:
    if not text or len(text) < 10: return None
    # 這裡只簡單判斷如果是範例文字才跳過，你已經填了 Key 所以會正常執行
    if "你的_NVIDIA_API_KEY" in NVIDIA_API_KEY:
        return None

    client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=NVIDIA_API_KEY)
    model_name = "meta/llama-3.1-8b-instruct"

    prompt = f"""
    你是一個專業的金融情緒分析師。請閱讀以下關於台積電 (2330) 的新聞文章內容，並給出一個 1 到 9 的情緒分數。
    評分標準：1分(極度負面) ~ 5分(中性) ~ 9分(極度正面)。
    請只回答一個數字 (1-9)，不要有任何解釋。
    文章內容：{text[:2000]}
    """
    try:
        completion = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=10, top_p=1
        )
        result = completion.choices[0].message.content.strip()
        score = int(re.search(r"\d+", result).group())
        return max(1, min(9, score))
    except Exception as e:
        print(f"[WARN] sentiment analysis failed: {e}")
        return None


def get_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


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


def extract_news(list_html: str):
    soup = BeautifulSoup(list_html, "lxml")
    results = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if "/news/" not in href: continue
        
        list_title = a.get_text(strip=True)
        
        # 原本的檢查
        if not list_title or list_title == "新聞": continue
        
        # ✅ 新增：如果是純數字 (例如 "1", "4")，通常是輪播按鈕，跳過
        if list_title.isdigit():
            continue

        url = urljoin(BASE_URL, href)
        if url in seen: continue
        seen.add(url)
        results.append((list_title, url))
    return results

def strip_publisher_from_reporter(reporter: str | None, publisher: str | None) -> str | None:
    """
    針對圖片案例優化的記者欄位清理：
    1. 遇到「台北」、「綜合外電」等地點名詞，直接切斷後面所有內容（解決長串日期問題）。
    2. 強制移除常見的財經媒體名稱（工商時報、Yahoo財經等）。
    3. 清理職稱與符號。
    """
    if not reporter:
        return None

    r = reporter.strip()
    if not r:
        return None

    # --- 1. 強制移除已知的媒體名稱 (不管 publisher 變數是什麼) ---
    # 這是為了解決圖片中「工商時報 王淑以」或「陳依旻 Yahoo財經」這類情況
    known_publishers = [
        "工商時報", "經濟日報", "時報資訊", "中央社", "Yahoo財經", "Yahoo", 
        "中時新聞網", "中時", "旺報", "財訊快報", "財訊","東森財經", "東森新聞", "東森"
    ]
    for pub in known_publishers:
        r = r.replace(pub, "")

    # --- 2. 移除傳入的 publisher 變數 (防呆) ---
    if publisher:
        p_full = publisher.strip()
        if p_full:
            r = r.replace(p_full, "")
            # 移除簡稱 (如 "XX新聞網" -> "XX")
            common_suffixes = ["新聞網", "新聞", "電子報", "財經", "報", "網"]
            p_short = p_full
            for suffix in common_suffixes:
                if p_short.endswith(suffix) and len(p_short) > len(suffix):
                    p_short = p_short[:-len(suffix)]
                    break
            if len(p_short) >= 2:
                r = r.replace(p_short, "")

    # --- 3. 關鍵修正：地點/通訊稿截斷 ---
    # 圖片中的 "吳家豪台北2025..."，名字後面緊接 "台北"，我們直接在 "台北" 處切斷
    # 使用正則表達式，遇到這些詞，就只取前面的部分
    # 邏輯：名字通常在最前面，後面接的一律丟掉
    split_pattern = r"(台北|新北|台中|高雄|台南|桃園|新竹|綜合外電|外電|報導|電\s*[）)])"
    r = re.split(split_pattern, r)[0]

    # --- 4. 移除常見職稱 ---
    job_titles = ["記者", "特派", "派駐", "採訪", "整理", "編輯", "專欄"]
    for title in job_titles:
        r = r.replace(title, "")

    # --- 5. 移除標點符號與括號 ---
    r = re.sub(r"[／/｜|：:\[\]【】\(\)\s]", " ", r)
    
    # --- 6. 最後修剪 ---
    r = re.sub(r"\s+", " ", r).strip()

    # --- 7. 長度檢查 (最後防線) ---
    # 如果清完還是太長 (例如超過 5 個字)，很有可能還是抓錯，寧願回傳 None 或截短
    # 大部分中文姓名是 2-4 字，少數複姓或翻譯名可能較長
    if len(r) > 10: 
        return None # 放棄這個看起來像亂碼的結果

    return r or None


def extract_detail(article_url: str):
    html = get_html(article_url)
    soup = BeautifulSoup(html, "lxml")
    headline, publisher, reporter_jsonld, published_text, content, published_dt = None, None, None, None, None, None

    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string
        if not raw: continue
        try: data = json.loads(raw.strip())
        except Exception: continue
        objs = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and isinstance(data.get("@graph"), list): objs = data["@graph"]
        for obj in objs:
            if not isinstance(obj, dict): continue
            if obj.get("@type") != "NewsArticle": continue
            if obj.get("headline"): headline = obj["headline"]
            prov = obj.get("provider")
            if isinstance(prov, dict) and prov.get("name"): publisher = prov["name"]
            author = obj.get("author")
            if isinstance(author, dict) and author.get("name"): reporter_jsonld = author["name"]
            iso_time = obj.get("datePublished") or obj.get("dateModified")
            if iso_time: published_text, published_dt = format_time_yahoo_tw(iso_time)

    # 記者名稱處理
    meta = soup.find("meta", attrs={"name": "description"})
    meta_content = meta["content"].strip() if meta and meta.get("content") else ""
    m = re.search(r"記者\s*([^\s／/】]+)", meta_content)
    reporter_meta = m.group(1).strip() if m else None
    
    reporter = reporter_meta or reporter_jsonld

    # ✅ 修改點 2：這裡改成呼叫新的函式，取代原本的簡單 replace
    reporter = strip_publisher_from_reporter(reporter, publisher)

    content_div = soup.find("div", class_="caas-body")
    if content_div: content = content_div.get_text(separator="\n", strip=True)
    else:
        ps = soup.find_all("p")
        if ps: content = "\n".join([p.get_text(strip=True) for p in ps])

    return headline, publisher, reporter, published_text, content, published_dt


def main():
    list_html = get_html(LIST_URL)
    news = extract_news(list_html)

    if not news:
        print("[ERROR] no news fetched")
        return

    rows = []
    seen_final_titles = set()
    processed_count = 0
    
    now_dt = datetime.now(ZoneInfo("Asia/Taipei"))
    today_date = now_dt.date()
    
    print(f"[TIME] now={now_dt} (filter date={today_date})")
    print(f"[INFO] found {len(news)} links, start processing...")
    
    for i, (list_title, url) in enumerate(news):
        try:
            headline, publisher, reporter, published_text, content, pub_dt = extract_detail(url)
            
            # 1. 日期過濾
            if pub_dt:
                if pub_dt.date() != today_date:
                    # 想要看被跳過的新聞可以把下面這行註解拿掉
                    print(f"  [跳過] 非今日新聞 ({pub_dt.date()}) | {list_title}")
                    continue
            else:
                continue

            # 2. 標題去重
            final_title = headline or list_title
            if final_title in seen_final_titles:
                continue
            seen_final_titles.add(final_title)
            
            # 3. 計算情緒
            sentiment_score = get_nvidia_sentiment_score(content)
            
            current_fetched_at = now_dt
            current_fetched_date = now_dt.date()
            current_fetched_time = now_dt.time()
            
            processed_count += 1
            print(f"[OK] {processed_count} score={sentiment_score} reporter={reporter} title={final_title}")

            rows.append((
                final_title, 
                publisher, 
                reporter, 
                published_text, 
                content, 
                sentiment_score, 
                url,
                current_fetched_at,
                current_fetched_date,
                current_fetched_time
            ))

        except Exception as e:
            print(f"[ERROR] article parse failed: {url} | {e}")
            continue

    if not rows:
        print("[INFO] no matching news for today")
        return

    with psycopg2.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
            for row in rows:
                cur.execute(UPSERT_SQL, row)
        conn.commit()

    print(f"[DONE] completed, inserted {len(rows)} records")

if __name__ == "__main__":
    main()