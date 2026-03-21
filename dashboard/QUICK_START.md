# 🚀 快速啟動指南

## 一周新聞分析功能使用說明

---

## 📱 方法一：使用 Dashboard（推薦）

### 1. 啟動服務器

```powershell
cd d:\__mypostgres_test\python_desktop\dashboard
python app.py
```

### 2. 打開瀏覽器

訪問：`http://localhost:5000`

### 3. 查看一周分析

頁面會自動顯示：
- **本周新聞統計**（4個彩色卡片）
- **本周最熱門股票 TOP 5**
- **一周情緒趨勢圖**

數據每30秒自動刷新！

---

## 💬 方法二：使用 LINE Bot

### 1. 確保服務器運行

```powershell
cd d:\__mypostgres_test\python_desktop\dashboard
python app.py
```

### 2. 在 LINE 中發送指令

發送任一指令：
- `一周`
- `本周`

### 3. 查看回應

Bot 會立即回覆包含以下資訊：
- 📰 總新聞數
- 📊 平均情緒
- 📈 正面新聞數
- 📉 負面新聞數
- 🔥 本周最熱門股票 TOP 5

---

## 🧪 測試 API

### 快速測試

```powershell
cd d:\__mypostgres_test\python_desktop\dashboard
python test_weekly_api.py
```

### 測試內容

- `/api/stats` - 基本統計
- `/api/weekly_analysis` - 一周分析
- `/api/weekly_sentiment_trend` - 情緒趨勢

---

## 📊 示例輸出

### Dashboard 顯示

```
本周新聞統計：
┌────────────────┬────────────────┬────────────────┬────────────────┐
│ 本周總新聞     │ 平均情緒       │ 正面新聞       │ 負面新聞       │
│ 2472          │ 6.09          │ 913           │ 130           │
└────────────────┴────────────────┴────────────────┴────────────────┘

本周最熱門股票 TOP 5:
1. 華邦電 - 23 則新聞
2. 旺宏 - 22 則新聞
3. 力積電 - 21 則新聞
4. 台積電 - 21 則新聞
5. 聯電 - 21 則新聞
```

### LINE Bot 回應

```
📊 最近一周新聞分析

📰 總新聞數：2472 則
📈 平均情緒：6.09
📈 正面新聞：913 則
📉 負面新聞：130 則

🔥 本周最熱門股票：
1. 華邦電 (23 則)
2. 旺宏 (22 則)
3. 力積電 (21 則)
4. 台積電 (21 則)
5. 聯電 (21 則)
```

---

## 🔧 常見問題

### Q1: 服務器無法啟動？

**檢查：**
- PostgreSQL 是否運行
- 連接字串是否正確
- Python 套件是否安裝

### Q2: Dashboard 沒有數據？

**可能原因：**
- 資料庫沒有7天內的數據
- 爬蟲尚未執行
- 數據庫連接失敗

**解決方案：**
```powershell
# 執行爬蟲獲取數據
cd d:\__mypostgres_test\python_desktop\stocks_news
python yahoo/crawler_hot.py
python cnyes/crawler_hot.py
```

### Q3: LINE Bot 沒有回應？

**檢查清單：**
- [ ] Flask 服務器是否運行
- [ ] ngrok 是否運行（如果使用）
- [ ] Webhook URL 是否正確設置
- [ ] .env 檔案是否有 LINE credentials

---

## 📖 更多資訊

- **完整使用指南**：`WEEKLY_ANALYSIS_GUIDE.md`
- **LINE Bot 指南**：`LINE_BOT_GUIDE.md`
- **實現總結**：`IMPLEMENTATION_SUMMARY.md`
- **主文檔**：`../stocks_news/README.md`

---

## ⚡ 一鍵啟動（建議）

創建一個 batch 檔案 `start_dashboard.bat`：

```batch
@echo off
cd /d d:\__mypostgres_test\python_desktop\dashboard
echo Starting Stock News Dashboard...
python app.py
pause
```

雙擊執行即可啟動！

---

## 🎯 快速檢查清單

使用前確認：
- [ ] PostgreSQL 已啟動
- [ ] 資料庫有數據（至少有7天內的新聞）
- [ ] Python 環境已設置
- [ ] 必要套件已安裝（Flask, asyncpg, requests, etc.）

---

**享受你的股市新聞分析吧！** 📊✨
