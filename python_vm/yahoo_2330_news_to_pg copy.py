import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urljoin

import requests
import psycopg2
from bs4 import BeautifulSoup

BASE_URL = "https://tw.stock.yahoo.com"
LIST_URL = "https://tw.stock.yahoo.com/quote/2330.TW/news"

PG_DSN = "host=host.docker.internal port=5432 dbname=postgres user=postgres password=lab529"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "zh-TW,zh;q=0.9",
}

WEEKDAY_ZH = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]

# 1. 修改 SQL：新增 content 欄位
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.yahoo_2330_news (
  id BIGSERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  publisher TEXT,
  reporter TEXT,
  published_text TEXT,
  content TEXT,  -- 新增這一行
  url TEXT NOT NULL UNIQUE,
  fetched_at TIMESTAMPTZ DEFAULT now()
);
"""

# 2. 修改 SQL：插入時也寫入 content
UPSERT_SQL = """
INSERT INTO public.yahoo_2330_news
(title, publisher, reporter, published_text, content, url)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (url) DO UPDATE SET
  title = EXCLUDED.title,
  publisher = EXCLUDED.publisher,
  reporter = EXCLUDED.reporter,
  published_text = EXCLUDED.published_text,
  content = EXCLUDED.content;
"""


def get_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def format_time_yahoo_tw(iso_z: str):
    """
    ISO 時間（例：2025-12-19T07:01:48.000Z）
    → 2025年12月19日 週五 下午2:15
    """
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
    不做關鍵字過濾
    """
    soup = BeautifulSoup(list_html, "lxml")
    results = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if "/news/" not in href:
            continue

        list_title = a.get_text(strip=True)
        if not list_title:
            continue
        # ❌ 排除導覽用的「新聞」這一列
        if list_title == "新聞":
            continue

        url = urljoin(BASE_URL, href)
        if url in seen:
            continue

        seen.add(url)
        results.append((list_title, url))

    return results


def extract_reporter_from_meta_description(soup: BeautifulSoup):
    """
    從 <meta name="description"> 抓記者。
    """
    meta = soup.find("meta", attrs={"name": "description"})
    if not meta or not meta.get("content"):
        return None

    content = meta["content"].strip()

    # 格式 1：包含「記者」
    m = re.search(r"記者\s*([^\s／/】]+)", content)
    if m:
        name = m.group(1).strip()
        name = re.split(r"(台北|新北|台中|高雄|桃園|報導)", name)[0].strip()
        return name or None

    # 格式 2：用「／」分隔
    m = re.search(r"／\s*([^】]+)\s*】", content)
    if m:
        name = m.group(1).strip()
        return name or None

    return None

def strip_publisher_from_reporter(reporter: str | None, publisher: str | None) -> str | None:
    """
    記者抓到後，把裡頭的出版社名稱去掉，只留名字。
    """
    if not reporter:
        return None

    r = reporter.strip()
    if not r:
        return None

    if publisher:
        p = publisher.strip()
        if p:
            r = r.replace(p, "")

    r = r.replace("記者", "")
    r = r.replace("特派", "")
    r = r.replace("特派記者", "")
    r = r.replace("派駐", "")
    r = r.replace("派駐記者", "")

    r = re.sub(r"[／/｜|：:]", " ", r)
    r = re.sub(r"\s+", " ", r).strip()

    return r or None


def extract_detail(article_url: str):
    """
    抓內頁：包含 title, publisher, reporter, published_text, content
    """
    html = get_html(article_url)
    soup = BeautifulSoup(html, "lxml")

    headline = None
    publisher = None            # provider.name
    reporter_jsonld = None      # author.name
    published_text = None
    content = None              # 內文

    # --- 1. JSON-LD 解析 ---
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string
        if not raw:
            continue

        try:
            data = json.loads(raw.strip())
        except Exception:
            continue

        objs = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and isinstance(data.get("@graph"), list):
            objs = data["@graph"]

        for obj in objs:
            if not isinstance(obj, dict):
                continue
            if obj.get("@type") != "NewsArticle":
                continue

            if obj.get("headline"):
                headline = obj["headline"]

            prov = obj.get("provider")
            if isinstance(prov, dict) and prov.get("name"):
                publisher = prov["name"]

            author = obj.get("author")
            if isinstance(author, dict) and author.get("name"):
                reporter_jsonld = author["name"]

            iso_time = obj.get("datePublished") or obj.get("dateModified")
            if iso_time:
                published_text = format_time_yahoo_tw(iso_time)

    # --- 2. 記者與出版社清理 ---
    reporter_meta = extract_reporter_from_meta_description(soup)
    reporter = reporter_meta or reporter_jsonld
    reporter = strip_publisher_from_reporter(reporter, publisher)

    # --- 3. 新增：抓取內文 ---
    # Yahoo 新聞內文通常包在 class="caas-body" 裡面
    content_div = soup.find("div", class_="caas-body")
    
    if content_div:
        # 使用 get_text 並用換行符號分隔段落
        content = content_div.get_text(separator="\n", strip=True)
    else:
        # 如果找不到 caas-body，嘗試抓所有 p 標籤 (備用方案)
        ps = soup.find_all("p")
        if ps:
            content = "\n".join([p.get_text(strip=True) for p in ps])

    return headline, publisher, reporter, published_text, content


def main():
    list_html = get_html(LIST_URL)
    news = extract_news(list_html)

    if not news:
        print("❌ 沒抓到新聞")
        return

    rows = []
    for list_title, url in news:
        try:
            # 這裡接收 5 個回傳值 (包含 content)
            headline, publisher, reporter, published_text, content = extract_detail(url)
        except Exception as e:
            print(f"⚠️ 內頁解析失敗：{url}\n   {e}")
            continue

        final_title = headline or list_title
        # 這裡將 5 個欄位放入 rows
        rows.append((final_title, publisher, reporter, published_text, content, url))

    if not rows:
        print("❌ 沒有可寫入的新聞")
        return

    with psycopg2.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
            for row in rows:
                cur.execute(UPSERT_SQL, row)
        conn.commit()

    print(f"✅ 從列表抓到 {len(news)} 則，成功寫入/更新 {len(rows)} 則新聞 (包含內文)")


if __name__ == "__main__":
    main()