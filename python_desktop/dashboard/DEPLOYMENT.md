# 部署到 Render.com 指南

## 前置準備

1. **註冊 Render.com 帳號**
   - 前往 https://render.com
   - 使用 GitHub 帳號註冊（推薦）

2. **建立 GitHub Repository**
   - 將專案推送到 GitHub
   - 確保包含所有檔案（app.py, requirements.txt, render.yaml 等）

## 部署步驟

### 1. 建立 PostgreSQL 資料庫

1. 登入 Render Dashboard
2. 點擊 **New +** → **PostgreSQL**
3. 設定：
   - Name: `stock-news-db`
   - Database: `postgres`
   - User: `postgres`
   - Region: `Singapore` (最接近台灣)
   - Plan: **Free**
4. 點擊 **Create Database**
5. 等待建立完成後，複製 **Internal Database URL**

### 2. 匯入資料庫結構

1. 在 Render Dashboard 中找到你的資料庫
2. 點擊 **Connect** → 選擇 **External Connection**
3. 複製連線資訊，使用 pgAdmin 或 psql 連線
4. 執行你的資料庫 schema（建立 tables）
5. 匯入 stock_mapping 資料

或使用 Render 的 Shell:
```bash
# 在資料庫頁面點擊 Shell
# 然後執行你的 SQL 指令
```

### 3. 部署 Web Service

1. 在 Render Dashboard 點擊 **New +** → **Web Service**
2. 連接你的 GitHub Repository
3. 設定：
   - Name: `stock-news-bot`
   - Environment: `Python 3`
   - Region: `Singapore`
   - Branch: `main`
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT`
   - Plan: **Free**

4. **環境變數設定** (點擊 Advanced → Add Environment Variable):
   ```
   DATABASE_URL = [貼上步驟1複製的 Internal Database URL]
   LINE_CHANNEL_SECRET = [你的 LINE Channel Secret]
   LINE_CHANNEL_ACCESS_TOKEN = [你的 LINE Access Token]
   PYTHON_VERSION = 3.11.0
   ```

5. 點擊 **Create Web Service**

### 4. 等待部署完成

- 部署過程約 5-10 分鐘
- 可以在 Logs 查看部署進度
- 部署成功後會顯示你的 URL，例如：
  ```
  https://stock-news-bot.onrender.com
  ```

### 5. 設定 LINE Webhook

1. 複製你的 Render URL
2. 到 LINE Developers Console
3. 在 Messaging API 設定中，將 Webhook URL 設為：
   ```
   https://stock-news-bot.onrender.com/callback
   ```
4. 點擊 **Verify** 測試連線
5. 啟用 **Use webhook**

## 注意事項

### Free Plan 限制
- 服務閒置 15 分鐘後會自動休眠
- 下次請求時需要 30-60 秒喚醒
- 每月 750 小時免費運行時間
- PostgreSQL 資料庫 90 天後會過期（需要重新建立）

### 保持服務運行
如果想避免休眠，可以：
1. 升級到付費方案（$7/月）
2. 使用 cron job 定期 ping 你的服務

### 更新部署
- 推送新的 commit 到 GitHub
- Render 會自動重新部署

## 其他免費替代方案

### Railway.app
- 優點：更簡單、$5 免費額度
- 缺點：需要信用卡驗證
- 網址：https://railway.app

### Fly.io
- 優點：不會休眠、效能好
- 缺點：設定較複雜
- 網址：https://fly.io

### PythonAnywhere
- 優點：專為 Python 設計
- 缺點：免費版功能受限
- 網址：https://www.pythonanywhere.com

## 疑難排解

### 部署失敗
1. 檢查 Logs 查看錯誤訊息
2. 確認 requirements.txt 包含所有套件
3. 確認環境變數設定正確

### LINE Webhook 驗證失敗
1. 確認服務已成功部署且運行中
2. 檢查 URL 是否正確（要加 /callback）
3. 查看 Render Logs 確認有收到請求

### 資料庫連線失敗
1. 確認 DATABASE_URL 設定正確
2. 使用 Internal Database URL（不是 External）
3. 確認資料庫已建立且運行中
