# 股市新聞爬蟲系統

本系統用於爬取 Yahoo 新聞和鉅亨網（cnyes）的股市新聞，並進行情緒分析。

## 📁 目錄結構

```
stocks_news/
├── yahoo/              # Yahoo 新聞爬蟲
│   ├── crawler_hot.py      # 熱門股爬蟲
│   ├── crawler_mid.py      # 中型股爬蟲
│   ├── crawler_lower.py    # 低流動性股爬蟲
│   ├── crawler_rare.py     # 冷門股爬蟲
│   ├── fetch_content.py    # 補抓新聞內文
│   └── analyze_sentiment.py # 情緒分析
│
├── cnyes/              # 鉅亨網新聞爬蟲
│   ├── crawler_hot.py      # 熱門股爬蟲
│   ├── crawler_mid.py      # 中型股爬蟲
│   ├── crawler_lower.py    # 低流動性股爬蟲
│   ├── crawler_rare.py     # 冷門股爬蟲
│   ├── fetch_content.py    # 補抓新聞內文
│   └── analyze_sentiment.py # 情緒分析
│
└── README.md           # 本文件
```

---

## 🔄 爬蟲架構

兩個新聞來源都採用 **三階段架構**：

### 階段 1：爬取新聞列表
- **檔案**：`crawler_*.py`（依股票分級分為 hot/mid/lower/rare）
- **功能**：
  - 爬取新聞標題、URL、發布時間等 metadata
  - **不抓取內文**，只儲存基本資訊
  - 根據股票分級使用不同的並發數和延遲設定
  - 使用進度檔案（`progress_*.txt`）記錄已處理的股票

### 階段 2：補抓新聞內文
- **檔案**：`fetch_content.py`
- **功能**：
  - 查詢資料庫中 `content IS NULL` 的新聞
  - 非同步抓取完整內文
  - 根據股票分級（stock_tier）過濾新聞日期
  - 刪除過舊的新聞（超過分級天數限制）

### 階段 3：情緒分析
- **檔案**：`analyze_sentiment.py`
- **功能**：
  - 查詢資料庫中 `sentiment_score IS NULL` 且 `content IS NOT NULL` 的新聞
  - 使用 NVIDIA API (llama-3.1-8b-instruct) 分析情緒
  - 回傳 1-9 分的情緒分數（1=極度負面，5=中性，9=極度正面）

---

## 📊 股票分級設定

| 分級 | 檔案 | 並發數 | 日期過濾 | 說明 |
|------|------|--------|----------|------|
| **熱門股** (hot) | `crawler_hot.py` | 5 | 3 天 | 高流動性股票 |
| **中型股** (mid) | `crawler_mid.py` | 5 | 7 天 | 中等流動性股票 |
| **低流動性股** (lower) | `crawler_lower.py` | 3 | 30 天 | 低流動性股票 |
| **冷門股** (rare) | `crawler_rare.py` | 3 | 30 天 | 極低流動性股票 |

---

## 🌐 Yahoo 新聞爬蟲

### 資料來源
- **URL 格式**：`https://tw.stock.yahoo.com/quote/{股票代號}.TW/news`
- **資料表**：`yahoo_stock_news`

### 爬取流程

#### 1. 爬取新聞列表
```bash
# 執行熱門股爬蟲
python yahoo/crawler_hot.py

# 執行中型股爬蟲
python yahoo/crawler_mid.py

# 執行低流動性股爬蟲
python yahoo/crawler_lower.py

# 執行冷門股爬蟲
python yahoo/crawler_rare.py
```

**特點**：
- 從股票新聞頁面解析新聞列表
- 每支股票抓取前 5 則新聞
- 只儲存標題和 URL，不進內頁
- 使用 `curl_cffi` 模擬真實瀏覽器（impersonate Chrome 131）
- 自動重試機制（指數退避）

#### 2. 補抓內文
```bash
python yahoo/fetch_content.py
```

**特點**：
- 查詢 `content IS NULL` 的新聞
- 解析內頁的 JSON-LD 結構化資料
- 提取發布者、記者、發布時間、內文
- 根據 `stock_tier` 過濾日期：
  - hot: 3 天內
  - mid: 7 天內
  - lower/rare: 30 天內
- 自動刪除過舊的新聞

#### 3. 情緒分析
```bash
# 設定 API Key
$env:NVIDIA_API_KEY='your_api_key'

# 執行分析
python yahoo/analyze_sentiment.py
```

### 資料庫結構
```sql
CREATE TABLE yahoo_stock_news (
  id BIGSERIAL PRIMARY KEY,
  stock_id TEXT NOT NULL,
  title TEXT NOT NULL,
  publisher TEXT,
  reporter TEXT,
  published_text TEXT,
  content TEXT,
  sentiment_score INTEGER,
  url TEXT NOT NULL,
  stock_tier TEXT,
  fetched_at TIMESTAMPTZ,
  fetched_date DATE,
  fetched_time TIME,
  UNIQUE (stock_id, url)
);
```

---

## 🌐 鉅亨網新聞爬蟲

### 資料來源
- **URL 格式**：`https://www.cnyes.com/twstock/{股票代號}/news/stock`
- **API**：`https://api.cnyes.com/media/api/v1/newslist/TWS:{股票代號}:STOCK/symbolNews`
- **資料表**：`cnyes_stock_news`

### 爬取流程

#### 1. 爬取新聞列表
```bash
# 執行熱門股爬蟲
python cnyes/crawler_hot.py

# 執行中型股爬蟲
python cnyes/crawler_mid.py

# 執行低流動性股爬蟲
python cnyes/crawler_lower.py

# 執行冷門股爬蟲
python cnyes/crawler_rare.py
```

**特點**：
- 從 `__NEXT_DATA__` JSON 提取第一頁新聞
- 使用 API 抓取後續頁面
- 支援分頁爬取（自動處理所有頁面）
- 根據分級設定過濾日期
- 只儲存 metadata，不抓內文

#### 2. 補抓內文
```bash
python cnyes/fetch_content.py
```

**特點**：
- 查詢 `content IS NULL` 的新聞
- 從新聞詳情頁抓取完整內文
- 解析 HTML 結構提取文章內容
- 非同步處理提升效率

#### 3. 情緒分析
```bash
# 設定 API Key
$env:NVIDIA_API_KEY='your_api_key'

# 執行分析
python cnyes/analyze_sentiment.py
```

### 資料庫結構
```sql
CREATE TABLE cnyes_stock_news (
  id BIGSERIAL PRIMARY KEY,
  news_id BIGINT NOT NULL,
  stock_id TEXT NOT NULL,
  title TEXT NOT NULL,
  category_name TEXT,
  category_id INTEGER,
  published_at TIMESTAMPTZ,
  content TEXT,
  sentiment_score INTEGER,
  url TEXT NOT NULL,
  fetched_at TIMESTAMPTZ,
  fetched_date DATE,
  fetched_time TIME,
  UNIQUE (stock_id, news_id)
);
```

---

## ⚙️ 技術細節

### 並發控制
- 使用 `asyncio.Semaphore` 控制並發數
- 避免過度請求導致 IP 被封鎖
- 不同分級使用不同的並發設定

### 延遲機制
- **股票間延遲**：2.5-5.0 秒（隨機）
- **新聞間延遲**：1.2-2.8 秒（隨機）
- **API 請求延遲**：0.5-1.5 秒（隨機）

### 重試機制
- 指數退避策略（exponential backoff）
- 最多重試 3 次
- 針對 403/429 狀態碼特別處理

### 瀏覽器模擬
- 使用 `curl_cffi` 的 `impersonate="chrome131"`
- 完整的 HTTP Headers 模擬
- TLS 指紋偽裝

---

## 🔧 環境設定

### 必要套件
```bash
pip install asyncpg curl-cffi pandas beautifulsoup4 lxml openai
```

### 環境變數
```powershell
# NVIDIA API Key（用於情緒分析）
$env:NVIDIA_API_KEY='your_api_key'
```

### 資料庫設定
- PostgreSQL 連線字串：`postgresql://postgres:lab529@localhost:5432/postgres`
- 自動建立資料表（首次執行時）

---

## 📝 使用流程

### 完整執行順序

#### Yahoo 新聞
```bash
# 1. 爬取新聞列表
python yahoo/crawler_hot.py
python yahoo/crawler_mid.py
python yahoo/crawler_lower.py
python yahoo/crawler_rare.py

# 2. 補抓內文
python yahoo/fetch_content.py

# 3. 情緒分析
$env:NVIDIA_API_KEY='your_api_key'
python yahoo/analyze_sentiment.py
```

#### 鉅亨網新聞
```bash
# 1. 爬取新聞列表
python cnyes/crawler_hot.py
python cnyes/crawler_mid.py
python cnyes/crawler_lower.py
python cnyes/crawler_rare.py

# 2. 補抓內文
python cnyes/fetch_content.py

# 3. 情緒分析
$env:NVIDIA_API_KEY='your_api_key'
python cnyes/analyze_sentiment.py
```

---

## 🛠️ 進度管理

### 進度檔案
- **Yahoo**：`progress_hot.txt`, `progress_mid.txt`, `progress_lower.txt`, `progress_rare.txt`
- **鉅亨網**：`progress_cnyes_hot.txt`, `progress_cnyes_mid.txt`, `progress_cnyes_lower.txt`, `progress_cnyes_rare.txt`

### 重新爬取
如需重新爬取某個分級的股票，刪除對應的進度檔案即可：
```bash
# 刪除 Yahoo 熱門股進度
rm yahoo/progress_hot.txt

# 刪除鉅亨網熱門股進度
rm cnyes/progress_cnyes_hot.txt
```

---

## 📊 資料查詢範例

### 檢查爬取進度
```sql
-- Yahoo 新聞統計
SELECT 
  stock_tier,
  COUNT(*) as total,
  COUNT(content) as has_content,
  COUNT(sentiment_score) as has_sentiment
FROM yahoo_stock_news
GROUP BY stock_tier;

-- 鉅亨網新聞統計
SELECT 
  COUNT(*) as total,
  COUNT(content) as has_content,
  COUNT(sentiment_score) as has_sentiment
FROM cnyes_stock_news;
```

### 查詢需要處理的新聞
```sql
-- 需要補抓內文的新聞
SELECT COUNT(*) FROM yahoo_stock_news WHERE content IS NULL;
SELECT COUNT(*) FROM cnyes_stock_news WHERE content IS NULL;

-- 需要情緒分析的新聞
SELECT COUNT(*) FROM yahoo_stock_news 
WHERE sentiment_score IS NULL AND content IS NOT NULL;

SELECT COUNT(*) FROM cnyes_stock_news 
WHERE sentiment_score IS NULL AND content IS NOT NULL;
```

---

## ⚠️ 注意事項

1. **API Key**：情緒分析需要 NVIDIA API Key
2. **速率限制**：注意不要過度請求，避免 IP 被封鎖
3. **資料庫容量**：定期清理過舊的新聞資料
4. **進度檔案**：不要手動修改進度檔案，除非要重新爬取
5. **並發數**：根據網路狀況調整 `MAX_CONCURRENCY`
6. **Windows 系統**：已自動設定 `WindowsSelectorEventLoopPolicy`

---

## 📈 效能優化

- **非同步 I/O**：使用 `asyncio` 提升爬取效率
- **連線池**：使用 `asyncpg.create_pool()` 管理資料庫連線
- **批次處理**：可在 `analyze_sentiment.py` 設定 `BATCH_SIZE` 限制單次處理數量
- **日期過濾**：只處理指定天數內的新聞，減少無效請求

---

## 🐛 常見問題

### 1. 爬蟲被封鎖（403/429）
- 增加延遲時間（調整 `DELAY_RANGE`）
- 降低並發數（調整 `MAX_CONCURRENCY`）
- 檢查 Headers 是否正確

### 2. 資料庫連線失敗
- 檢查 PostgreSQL 是否啟動
- 確認連線字串 `PG_DSN` 正確
- 檢查防火牆設定

### 3. 情緒分析失敗
- 確認 `NVIDIA_API_KEY` 已設定
- 檢查 API 額度是否用完
- 降低並發數避免速率限制

### 4. 內文抓取失敗
- 網站結構可能已改變，需更新解析邏輯
- 檢查網路連線
- 查看錯誤訊息調整重試策略
