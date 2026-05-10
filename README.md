# 股市新聞爬蟲 + Dashboard + LINE Bot 系統

完整的股市新聞追蹤系統，包含資料爬取、情緒分析、視覺化 Dashboard 和 LINE Bot 互動介面。

---

## 📊 系統架構

```
┌─────────────────┐
│  Yahoo Finance  │  ← 新聞來源
└────────┬────────┘
         │ 爬蟲抓取
         ↓
┌─────────────────────────────────────────┐
│     分級爬蟲 (stocks_news/yahoo/)       │
│  ┌──────────────┐  ┌──────────────┐    │
│  │crawler_hot.py│  │crawler_mid.py│    │
│  │  (熱門股)    │  │  (中間股)    │    │
│  └──────────────┘  └──────────────┘    │
│  ┌──────────────┐  ┌──────────────┐    │
│  │crawler_lower │  │crawler_rare  │    │
│  │  (偏下股)    │  │  (稀少股)    │    │
│  └──────────────┘  └──────────────┘    │
│                                         │
│  - 抓取股票新聞                         │
│  - AI 情緒分析 (NVIDIA NIM)             │
│  - 斷點續爬                             │
└────────┬────────────────────────────────┘
         │ 寫入
         ↓
┌─────────────────┐
│   PostgreSQL    │  ← 資料庫
│  ┌───────────┐  │     - yahoo_stock_news (新聞資料)
│  │yahoo_stock│  │     - stock_mapping (股票對照表)
│  │   _news   │  │
│  └───────────┘  │
└────┬───────┬────┘
     │       │
     │       └──────────────┐
     │ 查詢                 │ 查詢
     ↓                      ↓
┌─────────────────┐  ┌─────────────────┐
│   Dashboard     │  │    LINE Bot     │
│   (Flask Web)   │  │  (Flask Webhook)│
│                 │  │                 │
│ - 統計圖表      │  │ - 查詢股票      │
│ - 即時資料      │  │ - 熱門排行      │
│ - 情緒分析      │  │ - 情緒過濾      │
└─────────────────┘  └─────────────────┘
     ↓                      ↓
  瀏覽器訪問              LINE 聊天
```

---

## 🔄 資料流程

### 1. 資料收集階段
```
Yahoo Finance → 分級爬蟲 (4支) → NVIDIA NIM (情緒分析) → PostgreSQL
```

**執行腳本:** `stocks_news/yahoo/crawler_*.py` (4 支爬蟲)
- `crawler_hot.py` - 爬取熱門股 (974支，3天內新聞)
- `crawler_mid.py` - 爬取中間股 (487支，3天內新聞)
- `crawler_lower.py` - 爬取偏下股 (293支，7天內新聞)
- `crawler_rare.py` - 爬取稀少股 (195支，7天內新聞)

**分級依據:** 依股票成交金額分為 4 組 (詳見 `stocks_category/stock_category.md`)
- 熱門：成交金額 ≥ 19,834 元 (50%)
- 中間：3,684 ~ 19,834 元 (25%)
- 偏下：927 ~ 3,684 元 (15%)
- 稀少：0 ~ 927 元 (10%)

**爬蟲特色:**
- 使用 `curl_cffi` 模擬真實瀏覽器 TLS 指紋
- 非同步並發處理，提升效率
- 指數退避重試機制，避免被封鎖
- 支援斷點續爬 (progress_*.txt)
- AI 情緒分析 (1-9分)

### 2. 資料展示階段
```
PostgreSQL → Flask App → 前端/LINE
```

**Dashboard:** `dashboard/app.py`
- 提供 Web 介面查看統計資料
- 即時圖表和趨勢分析

**LINE Bot:** `dashboard/app.py` (同一個 Flask app)
- 透過 LINE 查詢股票新聞
- 支援情緒過濾和熱門排行

---

## 🗄️ 資料庫結構

### 主要資料表: `yahoo_stock_news`
```sql
CREATE TABLE yahoo_stock_news (
    id SERIAL PRIMARY KEY,
    stock_id VARCHAR(10),           -- 股票代碼
    title TEXT,                     -- 新聞標題
    link TEXT,                      -- 新聞連結
    publisher VARCHAR(100),         -- 發布者
    published_text VARCHAR(50),     -- 發布時間文字
    sentiment_score INTEGER,        -- 情緒分數 (1-9)
    fetched_at TIMESTAMP,          -- 抓取時間
    fetched_date DATE              -- 抓取日期
);
```

### 股票對照表: `stock_mapping`
```sql
CREATE TABLE stock_mapping (
    stock_id VARCHAR(10) PRIMARY KEY,  -- 股票代碼
    stock_name VARCHAR(100)            -- 股票名稱
);
```

**範例資料:**
```sql
INSERT INTO stock_mapping VALUES ('2330', '台積電');
INSERT INTO stock_mapping VALUES ('2317', '鴻海');
```

---

## 🚀 快速開始

### 前置準備
- Python 3.10+
- PostgreSQL
- NVIDIA NIM API key (用於情緒分析)
- LINE Developers 帳號 (用於 LINE Bot)

### 1. Clone 專案
```bash
git clone git@github.com:brian36987212/__mypostgres_test.git
cd __mypostgres_test/python_desktop
```

### 2. 建立虛擬環境
```bash
python -m venv .venv
.\.venv\Scripts\activate
```

### 3. 安裝套件
```bash
pip install -r requirements.txt
```

### 4. 設定資料庫
```sql
-- 建立資料表
CREATE TABLE yahoo_stock_news (
    id SERIAL PRIMARY KEY,
    stock_id VARCHAR(10),
    title TEXT,
    link TEXT,
    publisher VARCHAR(100),
    published_text VARCHAR(50),
    sentiment_score INTEGER,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    fetched_date DATE DEFAULT CURRENT_DATE
);

CREATE TABLE stock_mapping (
    stock_id VARCHAR(10) PRIMARY KEY,
    stock_name VARCHAR(100)
);

-- 匯入股票對照表
-- 執行 dashboard/import_stock_names.py
```

### 5. 設定環境變數

**爬蟲環境變數** (`.env`):
```bash
NVIDIA_API_KEY=your_nvidia_api_key
```

**Dashboard/LINE Bot 環境變數** (`dashboard/.env`):
```bash
LINE_CHANNEL_SECRET=your_channel_secret
LINE_CHANNEL_ACCESS_TOKEN=your_access_token
```

### 6. 執行爬蟲

**依序執行 4 支爬蟲:**

```bash
# 1. 熱門股 (974支，3天內新聞)
cd stocks_news/yahoo
python crawler_hot.py

# 2. 中間股 (487支，3天內新聞)
python crawler_mid.py

# 3. 偏下股 (293支，7天內新聞)
python crawler_lower.py

# 4. 稀少股 (195支，7天內新聞)
python crawler_rare.py
```

**或同時執行多個爬蟲 (開多個終端):**
```bash
# 終端 1
python crawler_hot.py

# 終端 2
python crawler_mid.py

# 終端 3
python crawler_lower.py

# 終端 4
python crawler_rare.py
```

**斷點續爬:**
- 每支爬蟲會自動記錄進度到 `progress_*.txt`
- 中斷後重新執行會自動跳過已完成的股票

### 7. 啟動 Dashboard + LINE Bot

你可以使用兩種方法啟動，推薦使用第一種（一鍵啟動）：

**方法一：使用 Batch 腳本（推薦）**
在資料夾中雙擊執行 `start_dashboard.bat`，或在終端機中執行：
```powershell
cd dashboard
start_dashboard.bat
```

**方法二：手動執行 Python**
```powershell
cd dashboard
python app.py
```

Dashboard 會在 http://localhost:5000 啟動。啟動後，LINE Bot 的伺服器也就緒了。

### 8. 啟用 LINE Bot 功能 (使用 ngrok)

要讓 LINE 能夠連線到你的電腦，需要使用 ngrok 將本地的 5000 端口暴露到網際網路上：

1. 開啟一個**新的**終端機視窗，執行：
```powershell
# 依據你安裝 ngrok 的路徑，可能需要輸入完整路徑如 C:\ngrok\ngrok.exe http 5000
ngrok http 5000
```

2. 複製 ngrok 提供的 **HTTPS** URL。
3. 前往 [LINE Developers Console](https://developers.line.biz/console/)，進入你的 Messaging API Channel。
4. 在 Webhook settings 中設定 Webhook URL（記得加上 `/webhook`）：
```
https://你的ngrok網址/webhook
```
5. 點擊 **Verify** 確認連線成功，並確保 **Use webhook** 已開啟。

> **注意：** 確保 `dashboard/.env` 中已設定 `LINE_CHANNEL_SECRET` 和 `LINE_CHANNEL_ACCESS_TOKEN`。免費用戶每次重啟 ngrok 網址會變更，需重新設定 Webhook URL。

---

## 📱 LINE Bot 功能

詳細使用說明請參考: [LINE_BOT_GUIDE.md](dashboard/LINE_BOT_GUIDE.md)

**支援指令:**
- `查詢 [股票名稱]` - 查詢特定股票新聞
- `熱門` - 最活躍的10檔股票
- `最新` - 最新5則新聞
- `正面` - 正面情緒新聞 (分數 7-9)
- `負面` - 負面情緒新聞 (分數 1-3)

---

## 🖥️ Dashboard 功能

訪問 http://localhost:5000 可以看到:

- **總體統計**: 新聞總數、股票數、今日新聞、平均情緒
- **情緒分布圖**: 新聞情緒分數分布
- **熱門股票**: 新聞數量 TOP 10
- **每日趨勢**: 近 30 天新聞數量趨勢
- **最新新聞**: 即時更新的最新 20 則新聞

---

## 🔧 技術棧

### 爬蟲部分
- **requests** / **curl_cffi**: HTTP 請求
- **BeautifulSoup4**: HTML 解析
- **OpenAI SDK**: 呼叫 NVIDIA NIM API 進行情緒分析
- **asyncpg**: 非同步 PostgreSQL 操作

### Dashboard + LINE Bot
- **Flask**: Web 框架
- **asyncpg**: 非同步資料庫查詢
- **line-bot-sdk**: LINE Messaging API
- **Chart.js**: 前端圖表 (透過 CDN)

### 資料庫
- **PostgreSQL**: 主要資料儲存

---

## 📂 專案結構

```
python_desktop/
├── stocks_news/                 # 爬蟲程式目錄
│   └── yahoo/
│       ├── crawler_hot.py      # 熱門股爬蟲 (974支)
│       ├── crawler_mid.py      # 中間股爬蟲 (487支)
│       ├── crawler_lower.py    # 偏下股爬蟲 (293支)
│       └── crawler_rare.py     # 稀少股爬蟲 (195支)
│
├── stocks_category/             # 股票分類資料
│   ├── stock_category.md       # 分類規則說明
│   ├── 股票代號_熱門_v2.csv
│   ├── 股票代號_中間_v2.csv
│   ├── 股票代號_偏下_v2.csv
│   └── 股票代號_稀少_v2.csv
│
├── dashboard/                   # Dashboard + LINE Bot
│   ├── app.py                   # Flask 主程式
│   ├── templates/
│   │   └── index.html          # Dashboard 前端
│   ├── import_stock_names.py   # 匯入股票對照表工具
│   ├── .env                    # LINE Bot 環境變數
│   ├── .env.example            # 環境變數範例
│   └── LINE_BOT_GUIDE.md       # LINE Bot 使用指南
│
├── requirements.txt             # Python 套件
├── .env                        # 爬蟲環境變數
└── README.md                   # 本檔案
```

---

## 🔍 串接邏輯詳解

### 爬蟲 → 資料庫
```python
# stocks_news/yahoo/crawler_hot.py (以熱門股爬蟲為例)
async def process_stock(sem, session, db_pool, ai_client, stock_code):
    # 1. 抓取新聞列表
    list_html = await get_html_async(session, target_url)
    news_list = parse_list_page(list_html)
    
    # 2. 逐一處理新聞
    for list_title, url in news_list:
        detail_html = await get_html_async(session, url)
        detail = parse_detail_page(detail_html)
        
        # 3. AI 情緒分析
        sentiment_score = await get_nvidia_sentiment_score_async(ai_client, content)
        
        # 4. 寫入資料庫
        async with db_pool.acquire() as conn:
            await conn.execute(UPSERT_SQL, 
                stock_code, title, publisher, reporter, published_text, 
                content, sentiment_score, url, now_dt, now_dt.date(), now_dt.time()
            )
```

### 資料庫 → Dashboard
```python
# dashboard/app.py
@app.route('/api/recent_news')
def get_recent_news():
    # 查詢最新新聞
    rows = await conn.fetch("""
        SELECT n.*, m.stock_name
        FROM yahoo_stock_news n
        LEFT JOIN stock_mapping m ON n.stock_id = m.stock_id
        ORDER BY n.fetched_at DESC
        LIMIT 20
    """)
    return jsonify(rows)
```

### 資料庫 → LINE Bot
```python
# dashboard/app.py
@app.route("/callback", methods=['POST'])
def callback():
    # 接收 LINE webhook
    handler.handle(body, signature)

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    # 查詢資料庫
    if user_text.startswith("查詢"):
        stock_name = user_text.replace("查詢", "").strip()
        reply = await query_stock_news(stock_name)
        # 回傳給 LINE
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
```

---

## 🐛 疑難排解

### 爬蟲問題
- **Request denied**: Yahoo 反爬蟲機制，使用 `curl_cffi` 模擬瀏覽器
- **情緒分析失敗**: 檢查 NVIDIA API key 是否正確

### Dashboard 問題
- **資料庫連線失敗**: 檢查 PostgreSQL 是否運行
- **圖表不顯示**: 檢查瀏覽器 console 是否有錯誤

### LINE Bot 問題
- **Bot 沒回應**: 檢查 ngrok 是否運行、Webhook URL 是否正確
- **查詢無結果**: 檢查資料庫是否有該股票資料

---

## 📝 待辦事項

- [ ] 支援更多股票代碼
- [ ] 新增新聞全文爬取
- [ ] 實作定時自動爬取
- [ ] Dashboard 新增更多圖表
- [ ] LINE Bot 支援圖片回覆

---

## 📄 授權

MIT License

---

**最後更新:** 2026-01-27


