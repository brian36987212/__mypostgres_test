# Yahoo 2330 News → PostgreSQL (with Sentiment)

## 需求
- Python 3.10+
- PostgreSQL
- NVIDIA NIM API key（可選）

## 1. Clone
git clone git@github.com:brian36987212/__mypostgres_test.git
cd __mypostgres_test/python_desktop

## 2. 虛擬環境
python -m venv .venv
.\.venv\Scripts\activate

## 3. 安裝套件
pip install -r requirements.txt

## 4. 設定環境變數
copy .env.example .env
（填入你的 NVIDIA_API_KEY 與 PG_DSN）

## 5. 執行
python yahoo_2330_news_to_pg_dt.py

