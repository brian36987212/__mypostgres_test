import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urljoin

import requests
import psycopg2
from bs4 import BeautifulSoup
from openai import OpenAI  # 引入 OpenAI 套件用來連線 NVIDIA

# ================= 設定區 =================

# ⚠️ 請將這裡換成你的 NVIDIA API Key (以 nvapi- 開頭)
NVIDIA_API_KEY = "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" 

BASE_URL = "https://tw.stock.yahoo.com"
LIST_URL = "https://tw.stock.yahoo.com/quote/2330.TW/news"
PG_DSN = "host=host.docker.internal port=5432 dbname=postgres user=postgres password=lab529"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9",
}

WEEKDAY_ZH = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]

# 1. 修改 SQL：新增 sentiment_score 欄位 (整數 1-9)
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.yahoo_2330_news (
  id BIGSERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  publisher TEXT,
  reporter TEXT,
  published_text TEXT,
  content TEXT,
  sentiment_score INTEGER,  -- 新增：情緒分數 1-9
  url TEXT NOT NULL UNIQUE,
  fetched_at TIMESTAMPTZ DEFAULT now()
);
"""

# 2. 修改 SQL：插入時也寫入 sentiment_score
UPSERT_SQL = """
INSERT INTO public.yahoo_2330_news
(title, publisher, reporter, published_text, content, sentiment_score, url)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (url) DO UPDATE SET
  title = EXCLUDED.title,
  publisher = EXCLUDED.publisher,
  reporter = EXCLUDED.reporter,
  published_text = EXCLUDED.published_text,
  content = EXCLUDED.content,
  sentiment_score = EXCLUDED.sentiment_score;
"""

# ================= 功能函式區 =================

def get_nvidia_sentiment_score(text: str) -> int:
    """
    使用 NVIDIA NIM (透過 OpenAI 介面) 分析文章情緒。
    回傳：1 (非常負面) ~ 9 (非常正面) 的整數。
    如果失敗或沒內容，回傳 None。
    """
    if not text or len(text) < 10:
        return None

    # 如果沒有設定 API Key，直接跳過以免報錯
    if "你的_NVIDIA_API_KEY" in NVIDIA_API_KEY:
        print("⚠️ 警告：尚未設定 NVIDIA_API_KEY，跳過情緒分析")
        return None

    client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=NVIDIA_API_KEY
    )

    # 這裡使用 Llama-3.1-8B 或 70B 作為分析模型，速度快且準確
    # 你可以依需求換成其他 NVIDIA 支援的模型
    model_name = "meta/llama-3.1-8b-instruct"

    prompt = f"""
    你是一個專業的金融情緒分析師。請閱讀以下關於台積電 (2330) 的新聞文章內容，並給出一個 1 到 9 的情緒分數。
    
    評分標準：
    - 1分：極度負面 (利空、暴跌、虧損)
    - 5分：中性 (無明顯好壞、純事實陳述)
    - 9分：極度正面 (利多、營收創新高、大漲)
    
    請只回答一個數字 (1-9)，不要有任何解釋或文字。
    
    文章內容：
    {text[:2000]}  -- 避免文章過長，截取前2000字
    """

    try:
        completion = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=10,
            top_p=1
        )
        
        result = completion.choices[0].message.content.strip()
        
        # 嘗試將回傳結果轉為整數
        score = int(re.search(r"\d+", result).group())
        
        # 確保分數在 1-9 之間
        if score < 1: score = 1
        if score > 9: score = 9
        
        return score

    except Exception as e:
        print(f"⚠️ 情緒分析失敗: {e}")
        return None


def get_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def format_time_yahoo_tw(iso_z: str):
    if not iso_z:
        return None
    try:
        s = iso_z.strip()
        if s.endswith("Z"):
            s = s.replace("Z", "+00:00")
        dt_utc = datetime.fromisoformat(s)
        dt = dt_utc.astimezone(ZoneInfo("Asia/Taipei"))
        ampm = "上午" if dt.hour < 12 else "下午"
        hour_12 = dt.hour % 12 or 12
        weekday = WEEKDAY_ZH[dt.weekday()]
        return f"{dt.year}年{dt.month}月{dt.day}日 {weekday} {ampm}{hour_12}:{dt.minute:02d}"
    except Exception:
        return None


def extract_news(list_html: str):
    """
    從 Yahoo 2330.TW 新聞列表頁抓出 (list_title, url)
    修正：同時過濾「重複網址」與「重複標題」
    """
    soup = BeautifulSoup(list_html, "lxml")
    results = []
    seen_urls = set()   # 用來存網址
    seen_titles = set() # 新增：用來存標題

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if "/news/" not in href:
            continue

        list_title = a.get_text(strip=True)
        if not list_title:
            continue
        
        # 排除導覽用的「新聞」
        if list_title == "新聞":
            continue

        # --- 修改重點開始 ---
        # 1. 先檢查標題是否出現過
        if list_title in seen_titles:
            continue
        
        url = urljoin(BASE_URL, href)
        
        # 2. 再檢查網址是否出現過
        if url in seen_urls:
            continue

        seen_titles.add(list_title) # 記錄這個標題
        seen_urls.add(url)          # 記錄這個網址
        # --- 修改重點結束 ---

        results.append((list_title, url))

    return results


def extract_reporter_from_meta_description(soup: BeautifulSoup):
    meta = soup.find("meta", attrs={"name": "description"})
    if not meta or not meta.get("content"): return None
    content = meta["content"].strip()
    m = re.search(r"記者\s*([^\s／/】]+)", content)
    if m:
        name = m.group(1).strip()
        name = re.split(r"(台北|新北|台中|高雄|桃園|報導)", name)[0].strip()
        return name or None
    m = re.search(r"／\s*([^】]+)\s*】", content)
    if m: return m.group(1).strip() or None
    return None


def strip_publisher_from_reporter(reporter: str | None, publisher: str | None) -> str | None:
    if not reporter: return None
    r = reporter.strip()
    if not r: return None
    if publisher:
        p = publisher.strip()
        if p: r = r.replace(p, "")
    r = r.replace("記者", "").replace("特派", "").replace("派駐", "")
    r = re.sub(r"[／/｜|：:]", " ", r)
    r = re.sub(r"\s+", " ", r).strip()
    return r or None


def extract_detail(article_url: str):
    html = get_html(article_url)
    soup = BeautifulSoup(html, "lxml")
    headline, publisher, reporter_jsonld, published_text, content = None, None, None, None, None

    # JSON-LD 解析
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string
        if not raw: continue
        try:
            data = json.loads(raw.strip())
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
            if iso_time: published_text = format_time_yahoo_tw(iso_time)

    reporter_meta = extract_reporter_from_meta_description(soup)
    reporter = reporter_meta or reporter_jsonld
    reporter = strip_publisher_from_reporter(reporter, publisher)

    # 抓內文
    content_div = soup.find("div", class_="caas-body")
    if content_div:
        content = content_div.get_text(separator="\n", strip=True)
    else:
        ps = soup.find_all("p")
        if ps: content = "\n".join([p.get_text(strip=True) for p in ps])

    return headline, publisher, reporter, published_text, content


def main():
    list_html = get_html(LIST_URL)
    news = extract_news(list_html)

    if not news:
        print("❌ 沒抓到新聞")
        return

    rows = []
    print(f"🔍 找到 {len(news)} 則新聞，開始解析內容與計算情緒...")
    
    for i, (list_title, url) in enumerate(news):
        try:
            headline, publisher, reporter, published_text, content = extract_detail(url)
            
            # --- 新增：計算情緒分數 ---
            # 為了省錢或省時間，你可以加上 time.sleep(1) 避免太快
            sentiment_score = get_nvidia_sentiment_score(content)
            print(f"  [{i+1}/{len(news)}] 分數:{sentiment_score} | 標題: {headline or list_title}")

        except Exception as e:
            print(f"⚠️ 內頁解析失敗：{url}\n   {e}")
            continue

        final_title = headline or list_title
        # 存入資料庫的順序要跟 SQL 對應：
        # title, publisher, reporter, published_text, content, sentiment_score, url
        rows.append((final_title, publisher, reporter, published_text, content, sentiment_score, url))

    if not rows:
        print("❌ 沒有可寫入的新聞")
        return

    with psycopg2.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
            for row in rows:
                cur.execute(UPSERT_SQL, row)
        conn.commit()

    print(f"✅ 成功寫入 {len(rows)} 則新聞 (含情緒分數)")


if __name__ == "__main__":
    main()