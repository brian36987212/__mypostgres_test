# 股市新聞 LINE Bot + Dashboard

即時股市新聞追蹤系統，包含 Web Dashboard 和 LINE Bot 互動介面。

## 功能特色

- 📊 **即時 Dashboard**: 視覺化呈現股市新聞統計
- 🤖 **LINE Bot**: 透過 LINE 查詢股票新聞
- 📈 **情緒分析**: AI 分析新聞情緒分數
- 🔍 **智慧搜尋**: 支援股票代碼和名稱查詢

## LINE Bot 指令

```
查詢 [股票名稱] - 查詢特定股票新聞
熱門 - 最活躍的10檔股票
最新 - 最新5則新聞
正面 - 正面情緒新聞
負面 - 負面情緒新聞
```

## 本地開發

### 1. 安裝套件

```bash
pip install -r requirements.txt
```

### 2. 設定環境變數

複製 `.env.example` 為 `.env` 並填入你的設定:

```bash
cp .env.example .env
```

編輯 `.env`:
```
DATABASE_URL=postgresql://postgres:password@localhost:5432/postgres
LINE_CHANNEL_SECRET=your_channel_secret
LINE_CHANNEL_ACCESS_TOKEN=your_access_token
```

### 3. 啟動服務

```bash
python app.py
```

服務會在 http://localhost:5000 啟動

### 4. 使用 ngrok 測試 LINE Bot

```bash
ngrok http 5000
```

將 ngrok 提供的 HTTPS URL 設定到 LINE Developers Console:
```
https://your-ngrok-url.ngrok.io/callback
```

## 部署到生產環境

詳見 [DEPLOYMENT.md](DEPLOYMENT.md)

推薦使用 **Render.com** (免費):
1. 註冊 Render.com
2. 建立 PostgreSQL 資料庫
3. 部署 Web Service
4. 設定環境變數
5. 更新 LINE Webhook URL

## 專案結構

```
dashboard/
├── app.py                 # Flask 主程式
├── templates/
│   └── index.html        # Dashboard 前端
├── requirements.txt      # Python 套件
├── render.yaml          # Render 部署設定
├── .env.example         # 環境變數範例
├── DEPLOYMENT.md        # 部署指南
└── README.md           # 本檔案
```

## 技術棧

- **Backend**: Flask + asyncpg
- **Database**: PostgreSQL
- **LINE Bot**: line-bot-sdk
- **部署**: Render.com / Railway / Fly.io

## 授權

MIT License
