# 股市新聞爬蟲 + Dashboard + LINE Bot 系統

完整的股市新聞追蹤系統，包含多來源資料爬取、AI 情緒與題材分析、視覺化 Dashboard 和 LINE Bot 互動介面。

---

## 📊 系統架構

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Yahoo Finance│  │  鉅亨網      │  │  NStock      │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                  │                  │
       └──────────────────┼──────────────────┘
                          │ 分級爬蟲抓取
                          ↓
              ┌──────────────────────────┐
              │  AI 情緒分析 (NVIDIA NIM) │
              │  主題標籤 (NVIDIA NIM)    │
              └──────────┬───────────────┘
                         │ 寫入
                         ↓
              ┌──────────────────────────┐
              │   本機 PostgreSQL        │  ← 完整歷史資料
              │  (yahoo/cnyes/nstock)    │
              └──────────┬───────────────┘
                         │ 每日 sync 最近30天
                         ↓
              ┌──────────────────────────┐
              │   Supabase (雲端 DB)     │  ← 近期資料 (≤ 30天)
              └──────────┬───────────────┘
                    ┌────┘────────────────┐
                    │ 查詢                 │ 查詢
                    ↓                      ↓
         ┌──────────────────┐  ┌──────────────────┐
         │  Dashboard       │  │   LINE Bot       │
         │  (Railway 雲端)  │  │  (Flask Webhook) │
         └──────────────────┘  └──────────────────┘
```

---

## 🔄 資料流程

### 1. 資料收集（本機執行）

**三個來源，各自有分級爬蟲:**

| 來源 | 路徑 | 說明 |
|------|------|------|
| Yahoo Finance | `stocks_news/yahoo/` | 4 支分級爬蟲 |
| 鉅亨網 (Cnyes) | `stocks_news/cnyes/` | 4 支分級爬蟲 |
| NStock | `stocks_news/nstock/` | 4 支分級爬蟲 |

**分級依據（依股票成交金額分為 4 組）:**
- `crawler_hot.py` - 熱門股（成交金額 ≥ 19,834 元，前 50%）
- `crawler_mid.py` - 中間股（3,684 ~ 19,834 元，25%）
- `crawler_lower.py` - 偏下股（927 ~ 3,684 元，15%）
- `crawler_rare.py` - 稀少股（0 ~ 927 元，10%）

**爬蟲特色:**
- `curl_cffi` 模擬真實瀏覽器 TLS 指紋，繞過反爬蟲
- 非同步並發處理（asyncio + asyncpg）
- 指數退避重試機制
- 斷點續爬（progress_*.txt）
- NVIDIA NIM API 情緒分析（1-9 分）

### 2. 題材分析（本機執行，使用歷史資料）

```
本機 PostgreSQL → analyze_sentiment.py → AI 題材標籤
                → theme_trends_compare_new.py → 趨勢比較報告
```

- `stocks_news/*/analyze_sentiment.py` - 補跑情緒分析
- `test_theme_ai.py` - AI 題材標籤測試
- `theme_trends_compare_new.py` - 題材趨勢比較（整合 Google Trends）

### 3. 雲端同步（每日執行）

```
本機 DB（最近30天） → sync_to_supabase.py → Supabase
```

```powershell
python sync_to_supabase.py
```

- 自動同步近 30 天新聞到 Supabase
- 自動清理 Supabase 中超過 30 天的舊資料
- 確保 Supabase 永遠不超過 500MB 免費上限

### 4. Dashboard 展示（Railway 雲端）

```
Supabase → Flask API (Railway) → 瀏覽器 / LINE
```

---

## 🗄️ 資料庫結構

### `yahoo_stock_news`
```sql
CREATE TABLE yahoo_stock_news (
    id              SERIAL PRIMARY KEY,
    stock_id        VARCHAR(10),
    title           TEXT,
    publisher       VARCHAR(100),
    reporter        VARCHAR(100),
    published_text  VARCHAR(50),
    content         TEXT,
    sentiment_score INTEGER,        -- AI 情緒分數 (1-9)
    url             TEXT,
    fetched_at      TIMESTAMPTZ,
    fetched_date    DATE,
    fetched_time    TIME,
    UNIQUE(stock_id, url)
);
```

### `cnyes_stock_news`
```sql
CREATE TABLE cnyes_stock_news (
    id              SERIAL PRIMARY KEY,
    stock_id        VARCHAR(10),
    news_id         VARCHAR(50),
    title           TEXT,
    category_name   VARCHAR(50),
    published_at    TIMESTAMPTZ,
    content         TEXT,
    sentiment_score INTEGER,
    url             TEXT,
    fetched_date    DATE,
    UNIQUE(stock_id, news_id)
);
```

### `nstock_stock_news`
```sql
CREATE TABLE nstock_stock_news (
    id              SERIAL PRIMARY KEY,
    stock_id        VARCHAR(10),
    title           TEXT,
    content         TEXT,
    sentiment_score INTEGER,
    url             TEXT,
    fetched_date    DATE,
    UNIQUE(stock_id, url)
);
```

### `stock_mapping`
```sql
CREATE TABLE stock_mapping (
    stock_id   VARCHAR(10) PRIMARY KEY,
    stock_name VARCHAR(100)
);
```

---

## 🚀 快速開始

### 前置準備
- Python 3.10+
- PostgreSQL 18（本機）
- NVIDIA NIM API key（用於情緒/題材分析）
- LINE Developers 帳號（用於 LINE Bot）
- Supabase 帳號（免費，用於雲端 Dashboard）
- Railway 帳號（免費，用於部署 Dashboard）

### 1. Clone 專案
```bash
git clone https://github.com/brian36987212/StockPulse.git
cd StockPulse
```

### 2. 建立虛擬環境
```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. 設定環境變數

**根目錄 `.env`（爬蟲 + 同步腳本）:**
```bash
NVIDIA_API_KEY=your_nvidia_api_key
DATABASE_URL=postgresql://postgres.xxxx:PASSWORD@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres
```

**`dashboard/.env`（本機開發用）:**
```bash
LINE_CHANNEL_SECRET=your_channel_secret
LINE_CHANNEL_ACCESS_TOKEN=your_access_token
DATABASE_URL=postgresql://postgres.xxxx:PASSWORD@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres
```

### 4. 執行爬蟲（擇一來源）

```powershell
# Yahoo Finance
cd stocks_news/yahoo
python crawler_hot.py

# 鉅亨網
cd stocks_news/cnyes
python crawler_hot.py

# NStock
cd stocks_news/nstock
python crawler_hot.py
```

### 5. 同步資料到 Supabase

```powershell
cd d:\StockPulse
python sync_to_supabase.py
```

### 6. 本機啟動 Dashboard

```powershell
cd dashboard
python app.py
```

訪問 http://localhost:5000

---

## ☁️ 雲端部署架構

| 服務 | 平台 | 說明 |
|------|------|------|
| Dashboard + LINE Bot | **Railway** | 從 GitHub 自動部署 |
| 資料庫（近期資料） | **Supabase** | 免費 500MB，存最近 30 天 |
| 完整歷史資料 | **本機 PostgreSQL** | 供題材分析使用 |

### Railway 環境變數設定

| 變數名 | 說明 |
|--------|------|
| `DATABASE_URL` | Supabase pooler connection string |
| `LINE_CHANNEL_SECRET` | LINE Bot Channel Secret |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Bot Access Token |

### Railway 設定
- **Root Directory:** `dashboard`
- **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT`

---

## 📱 LINE Bot 功能

詳細說明請參考: [LINE_BOT_GUIDE.md](dashboard/LINE_BOT_GUIDE.md)

**支援指令:**
- `查詢 [股票名稱]` - 查詢特定股票新聞
- `熱門` - 最活躍的 10 檔股票
- `最新` - 最新 5 則新聞
- `正面` - 正面情緒新聞（分數 7-9）
- `負面` - 負面情緒新聞（分數 1-3）

---

## 🖥️ Dashboard 功能

- **總體統計**: 新聞總數、股票數、今日新聞、平均情緒
- **情緒分布圖**: 新聞情緒分數分布
- **熱門股票**: 新聞數量 TOP 10
- **每日趨勢**: 近 30 天新聞量趨勢
- **最新新聞**: 即時更新的最新 20 則新聞

---

## 🔧 技術棧

| 類別 | 技術 |
|------|------|
| 爬蟲 | `curl_cffi`, `BeautifulSoup4`, `asyncio` |
| AI 分析 | `openai` SDK → NVIDIA NIM API |
| 資料庫（本機） | PostgreSQL 18 + `asyncpg`/`psycopg2` |
| 資料庫（雲端） | Supabase（PostgreSQL） |
| Web 框架 | Flask + Gunicorn |
| 部署 | Railway |
| LINE Bot | `line-bot-sdk` |
| 前端圖表 | Chart.js（CDN） |

---

## 📂 專案結構

```
StockPulse/
├── stocks_news/
│   ├── yahoo/              # Yahoo Finance 爬蟲
│   │   ├── crawler_hot.py
│   │   ├── crawler_mid.py
│   │   ├── crawler_lower.py
│   │   ├── crawler_rare.py
│   │   ├── fetch_content.py
│   │   └── analyze_sentiment.py
│   ├── cnyes/              # 鉅亨網爬蟲
│   │   └── (同上結構)
│   └── nstock/             # NStock 爬蟲
│       └── (同上結構)
│
├── dashboard/              # Web Dashboard + LINE Bot
│   ├── app.py              # Flask 主程式
│   ├── templates/
│   │   └── index.html
│   ├── Procfile            # Railway 部署設定
│   ├── requirements.txt
│   └── LINE_BOT_GUIDE.md
│
├── stocks_category/        # 股票分級資料 (CSV)
│
├── migrate_to_supabase.py  # 一次性遷移腳本（初次部署用）
├── sync_to_supabase.py     # 每日同步腳本（維持近30天資料）
├── theme_trends_compare_new.py  # 題材趨勢分析
├── test_theme_ai.py        # AI 題材標籤測試
├── .env                    # 環境變數（不 commit）
├── .gitignore
└── README.md
```

---

## 🐛 疑難排解

| 問題 | 解法 |
|------|------|
| 爬蟲被封鎖 | `curl_cffi` 已模擬瀏覽器指紋，確認 session 設定正確 |
| 情緒分析失敗 | 檢查 `NVIDIA_API_KEY` 是否有效 |
| Dashboard 資料空白 | 執行 `sync_to_supabase.py`，確認 `DATABASE_URL` 設定正確 |
| Railway 連線失敗 | 確認 Railway Variables 中 `DATABASE_URL` 使用 Supabase pooler URL |
| Supabase 密碼驗證失敗 | 去 Supabase → Settings → Database → Reset password |

---

## 📝 待辦事項

- [x] 多來源爬蟲（Yahoo、Cnyes、NStock）
- [x] AI 情緒分析（NVIDIA NIM）
- [x] 雲端部署（Railway + Supabase）
- [x] 每日資料同步機制（保留 30 天）
- [ ] 爬蟲自動排程（Windows Task Scheduler）
- [ ] 題材篩選結果持久化到 DB
- [ ] Dashboard 新增題材趨勢頁面
- [ ] LINE Bot 支援題材查詢

---

## 📄 授權

MIT License

---

**最後更新:** 2026-06-05
