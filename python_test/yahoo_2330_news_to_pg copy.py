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

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.yahoo_2330_news (
  id BIGSERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  publisher TEXT,
  reporter TEXT,
  published_text TEXT,
  url TEXT NOT NULL UNIQUE,
  fetched_at TIMESTAMPTZ DEFAULT now()
);
"""

UPSERT_SQL = """
INSERT INTO public.yahoo_2330_news
(title, publisher, reporter, published_text, url)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (url) DO UPDATE SET
  title = EXCLUDED.title,
  publisher = EXCLUDED.publisher,
  reporter = EXCLUDED.reporter,
  published_text = EXCLUDED.published_text;
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

        url = urljoin(BASE_URL, href)
        if url in seen:
            continue

        seen.add(url)
        results.append((list_title, url))

    return results


def extract_reporter_from_meta_description(soup: BeautifulSoup):
    """
    以你提供的「目前樣子」為主：從 <meta name="description" content="..."> 抓記者。
    依據你貼的例子，支援兩種常見格式：
      1) 【時報記者莊丙安台北報導】 -> 抓「莊丙安」
      2) 【財訊快報／戴辰Z】 -> 抓「戴辰Z」
    抓不到回 None（不臆測）
    """
    meta = soup.find("meta", attrs={"name": "description"})
    if not meta or not meta.get("content"):
        return None

    content = meta["content"].strip()

    # 格式 1：包含「記者」
    # 例：【時報記者莊丙安台北報導】...  -> 取 記者 後面的名字，遇到 地名/報導/】 等就停
    m = re.search(r"記者\s*([^\s／/】]+)", content)
    if m:
        name = m.group(1).strip()
        # 有些會把「台北報導」黏在一起（你例子：莊丙安台北報導），做保守裁切
        # 只在出現「台北/新北/高雄/台中/桃園/報導」等字樣時截掉後面
        name = re.split(r"(台北|新北|台中|高雄|桃園|報導)", name)[0].strip()
        return name or None

    # 格式 2：用「／」分隔
    # 例：【財訊快報／戴辰Z】... -> 取 ／ 後面到 】 前
    m = re.search(r"／\s*([^】]+)\s*】", content)
    if m:
        name = m.group(1).strip()
        return name or None

    return None


def extract_detail(article_url: str):
    """
    抓內頁：
      - title：JSON-LD headline（抓不到退回 None）
      - publisher：JSON-LD provider.name（抓不到 None）
      - reporter：以 meta description 為主，抓不到再 fallback JSON-LD author.name
      - published_text：datePublished -> Yahoo 顯示格式（抓不到 None）
    """
    html = get_html(article_url)
    soup = BeautifulSoup(html, "lxml")

    headline = None
    publisher = None            # provider.name
    reporter_jsonld = None      # author.name
    published_text = None

    # 先抓 JSON-LD（title / publisher / time / author fallback）
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

    # 以 meta description 抓 reporter（你指定「以目前樣子為主」）
    reporter_meta = extract_reporter_from_meta_description(soup)
    reporter = reporter_meta or reporter_jsonld

    return headline, publisher, reporter, published_text


def main():
    list_html = get_html(LIST_URL)
    news = extract_news(list_html)

    if not news:
        print("❌ 沒抓到新聞")
        return

    rows = []
    for list_title, url in news:
        try:
            headline, publisher, reporter, published_text = extract_detail(url)
        except Exception as e:
            print(f"⚠️ 內頁解析失敗：{url}\n   {e}")
            continue

        final_title = headline or list_title
        rows.append((final_title, publisher, reporter, published_text, url))

    if not rows:
        print("❌ 沒有可寫入的新聞")
        return

    with psycopg2.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
            for row in rows:
                cur.execute(UPSERT_SQL, row)
        conn.commit()

    print(f"✅ 從列表抓到 {len(news)} 則，成功寫入/更新 {len(rows)} 則新聞")


if __name__ == "__main__":
    main()
