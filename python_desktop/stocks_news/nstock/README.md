# NStock (恩投資財經網) 新聞爬蟲

## 📋 簡介

從 nstock.tw (恩投資財經網) 爬取台股新聞的完整解決方案。

## 🎯 特色

- **資料來源**: 解析 `window.__NUXT__` JavaScript 變數
- **三階段架構**: 新聞列表 → 內文補抓 → 情緒分析
- **分層爬取**: 依股票熱度分為 4 個 tier
- **日期過濾**: 根據股票熱度智慧過濾舊新聞
- **進度追蹤**: 支援斷點續爬

## 📊 資料庫 Schema

```sql
CREATE TABLE nstock_stock_news (
  id BIGSERIAL PRIMARY KEY,
  news_id TEXT NOT NULL,
  stock_id TEXT NOT NULL,
  title TEXT NOT NULL,
  category TEXT,
  published_at TIMESTAMPTZ,
  content TEXT,
  sentiment_score INTEGER,
  url TEXT NOT NULL,
  stock_tier TEXT,
  related_stocks TEXT,
  fetched_at TIMESTAMPTZ,
  UNIQUE (stock_id, news_id)
);
```

## 📁 檔案結構

```
nstock/
├── crawler_hot.py          # 熱門股爬蟲 (3天內新聞)
├── crawler_mid.py          # 中型股爬蟲 (7天內新聞)
├── crawler_lower.py        # 低流動性股爬蟲 (30天內新聞)
├── crawler_rare.py         # 冷門股爬蟲 (30天內新聞)
├── fetch_content.py        # 內文補抓工具
├── analyze_sentiment.py   # 情緒分析工具
└── README.md              # 本文件
```

## 🚀 使用方式

### 1. 環境準備

```bash
# 安裝相依套件
pip install curl-cffi asyncpg pandas beautifulsoup4 lxml openai

# 設定環境變數 (情緒分析需要)
$env:NVIDIA_API_KEY='your_api_key'
```

### 2. 爬取新聞列表

依序執行各 tier 的爬蟲:

```bash
# 熱門股 (150支, 3天內新聞)
python nstock/crawler_hot.py

# 中型股 (200支, 7天內新聞)
python nstock/crawler_mid.py

# 低流動性股 (30天內新聞)
python nstock/crawler_lower.py

# 冷門股 (30天內新聞)
python nstock/crawler_rare.py
```

### 3. 補抓完整內文

```bash
python nstock/fetch_content.py
```

### 4. 情緒分析

```bash
python nstock/analyze_sentiment.py
```

## ⚙️ 配置說明

### 爬蟲參數

| 參數 | 熱門股 | 中型股 | 低流動性股 | 冷門股 |
|------|--------|--------|-----------|--------|
| `DAYS_FILTER` | 3 | 7 | 30 | 30 |
| `MAX_CONCURRENCY` | 5 | 5 | 3 | 3 |
| `STOCK_DELAY_RANGE` | 2.5-5s | 2.5-5s | 2.5-5s | 2.5-5s |

### 資料庫連線

修改 `PG_DSN`:
```python
PG_DSN = "postgresql://user:password@host:port/database"
```

## 📈 執行統計

### 預期新聞數量

- 每支股票: 約 5 筆新聞
- 熱門股 (150支): ~750 筆
- 中型股 (200支): ~1000 筆
- 低流動性+冷門股: 視實際新聞量而定

### 執行時間

- 熱門股: 約 15-20 分鐘
- 中型股: 約 20-30 分鐘
- 內文補抓: 依新聞數量而定
- 情緒分析: 約 0.5-1 秒/篇

## 🔧 進階功能

### 斷點續爬

每個 tier 都會記錄進度到 `progress_nstock_{tier}.txt`:
- 若中途中斷,重新執行會自動跳過已處理股票
- 若要重新爬取,刪除對應的 progress 檔案即可

### 批次控制

`analyze_sentiment.py` 支援批次處理:

```python
BATCH_SIZE = 100  # 只處理 100 筆
DAYS_LIMIT = 7    # 只分析 7 天內的新聞
```

## 🎨 技術細節

### 資料擷取方式

NStock 使用 Nuxt.js 框架,新聞資料以 IIFE 函數形式嵌入:

```javascript
window.__NUXT__=(function(a,b,c,d,e,...){
  return {
    fetch: {
      "2": {
        news: [
          {id:"...", title:"...", date:"...", ...}
        ]
      }
    }
  }
})(...);
```

**解析策略**:
1. 提取包含 `window.__NUXT__` 的 script 標籤
2. 用正則表達式匹配 `news:[]` 陣列
3. 逐項解析新聞物件

### 變數引用處理

部分欄位使用變數引用 (如 `stocks:l`),需對應函數參數:
- `e` = "時報新聞" (source)
- `l` = "2330(TW)" (stocks)
- `b` = "" (image)

## ⚠️ 注意事項

1. **新聞數量有限**: 每支股票約 5 筆,不適合歷史資料分析
2. **需進內頁**: 列表頁只有 metadata,完整內文需二次請求
3. **來源單一**: 所有新聞都來自時報新聞
4. **延遲控制**: 請勿調低延遲時間,避免被封鎖

## 🐛 常見問題

**Q: 找不到 NUXT 資料?**
- 檢查網頁是否正常載入
- 確認 `curl_cffi` 正確安裝

**Q: 內文抓取失敗?**
- NStock 詳情頁結構可能變動
- 需調整 `fetch_content.py` 中的 CSS 選擇器

**Q: 情緒分析失敗?**
- 確認 `NVIDIA_API_KEY` 已設定
- 檢查 API 額度是否用盡

## 📝 LICENSE

MIT
