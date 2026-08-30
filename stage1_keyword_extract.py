"""
第一階段：關鍵詞萃取
- jieba 斷詞
- 移除公司名稱（來自 stocks_category CSV）
- 移除政府/機構名稱（靜態清單）
- 移除停用詞、純數字
- 結果寫入各新聞表的 keywords TEXT[] 欄位

執行：python stage1_keyword_extract.py
"""

import asyncio
import csv
import re
from pathlib import Path

import asyncpg
import jieba

# ═══════════════════════════════════════════════════════════════
# 設定
# ═══════════════════════════════════════════════════════════════
PG_DSN = "postgresql://postgres:lab529@localhost:5432/postgres"
DAYS_BACK = None  # None = 處理全部歷史資料；設為整數（如 90）= 只處理近 N 天

CSV_DIR = Path(__file__).parent / "stocks_category"
CSV_FILES = [
    "股票代號_熱門_v2.csv",
    "股票代號_中間_v2.csv",
    "股票代號_偏下_v2.csv",
    "股票代號_稀少_v2.csv",
]

# ═══════════════════════════════════════════════════════════════
# 政府機關 / 金融監管 / 媒體 靜態清單
# ═══════════════════════════════════════════════════════════════
INSTITUTIONS: set[str] = {
    # 台灣政府機關
    "行政院", "立法院", "司法院", "考試院", "監察院", "總統府",
    "財政部", "經濟部", "工業局", "國發會", "金管會", "金融監督管理委員會",
    "勞動部", "環保署", "衛福部", "教育部", "交通部", "外交部",
    "內政部", "農業部", "農委會", "科技部", "國科會",
    "NCC", "國家通訊傳播委員會", "公平會", "公平交易委員會", "促轉會",
    # 台灣金融監管
    "證交所", "台灣證券交易所", "櫃買中心", "期交所",
    "投信投顧公會", "銀行公會", "壽險公會",
    # 各國央行
    "央行", "中央銀行",
    "聯準會", "美聯準", "Fed",
    "歐洲央行", "ECB",
    "日本央行", "日銀", "BOJ",
    "英格蘭銀行", "BOE",
    "人行", "中國人民銀行", "PBOC",
    # 國際組織
    "SEC", "FDIC", "IMF", "WTO", "WHO", "世界銀行", "G7", "G20", "OPEC",
    # 媒體（出現在內文但非題材）
    "鉅亨網", "Anue", "Yahoo財經", "工商時報", "經濟日報",
    "聯合報", "中時", "自由時報", "MoneyDJ", "Bloomberg", "彭博",
    "Reuters", "路透", "道瓊", "那斯達克",
    # Yahoo/Google 頁面模板（出現在每篇爬取的 content 裡）
    "Yahoo", "Google", "Finance", "NOWNEWS", "FTNN",
    # 公司短稱（英文名或慣用縮稱，未收錄在 CSV/stock_mapping 裡）
    "momo", "璞玉",
    # 台灣科技公司英文名（新聞中常以英文出現，不在 CSV 裡）
    "Accton", "MediaTek", "TSMC", "UMC", "ASE", "Foxconn", "Pegatron",
    "Wistron", "Quanta", "Compal", "Inventec", "Lite-On", "Delta",
    "Largan", "Novatek", "Realtek", "Himax", "Phison", "Silicon Motion",
    "ADATA", "Kingston", "Transcend", "Seagate", "Western Digital",
    "Micron", "SK Hynix", "Samsung", "Intel", "AMD", "Qualcomm",
    "Broadcom", "Marvell", "Nvidia", "NVIDIA", "Apple", "Microsoft",
    "Meta", "Amazon", "Alphabet", "Tesla", "Spectra",
}

# ═══════════════════════════════════════════════════════════════
# 公告型文章偵測（title 含以下關鍵字 → 只取 title，不取 content）
# ═══════════════════════════════════════════════════════════════
_ANNOUNCE_RE = re.compile(
    r"異動|重大訊息|法人說明會|法說會|股東常會|股東臨時會|董事會決議"
    r"|公告參加|代理人|重訊|申報轉讓|申報買回|庫藏股|增資|減資|下市"
)

# Yahoo Finance content 頁頭模板（這行之後才是真正內容）
_YAHOO_HEADER_RE = re.compile(
    r"^.*?(加入\s*Google\s*新聞|將\s*Yahoo\s*新聞設定|在\s*Google\s*新聞找尋)[^\n]*\n",
    re.DOTALL,
)

# Yahoo Finance 頁腳免責聲明（從這裡到文末全部移除）
_YAHOO_FOOTER_RE = re.compile(
    r"首選來源.*$",
    re.DOTALL,
)

# ═══════════════════════════════════════════════════════════════
# 停用詞
# ═══════════════════════════════════════════════════════════════
STOPWORDS: set[str] = {
    # 功能詞
    "的", "了", "是", "在", "也", "都", "要", "有", "和", "對",
    "從", "等", "將", "已", "並", "為", "以", "及", "而", "但",
    "其", "或", "由", "到", "向", "中", "後", "前", "上", "下",
    "內", "外", "間", "時", "年", "月", "日", "季", "度",
    # 新聞說話動詞（無題材意義）
    "表示", "指出", "說明", "指", "稱", "據", "坦言", "透露",
    "報導", "分析", "顯示", "認為", "預計", "預期", "估計",
    "相關", "相比", "較", "達", "約", "近", "逾", "超過",
    "記者", "採訪", "消息", "來源", "資料", "根據",
    "此外", "然而", "因此", "不過", "另外", "同時",
    "目前", "近期", "未來", "整體", "整個", "整年", "全年",
    "主要", "重要", "最新", "最近", "目標", "計畫",
    # 數量單位
    "元", "億", "萬", "千", "百", "億元", "萬元", "千元", "億美元",
    "個", "件", "項", "支", "台", "張", "家", "筆", "批",
    "百分之", "百分", "倍",
    # 時間副詞
    "今年", "去年", "明年", "本季", "上季", "今日", "昨日", "今天",
    "上週", "本週", "上月", "本月", "第一季", "第二季", "第三季", "第四季",
    # 公司/市場通用詞（不代表特定題材）
    "公司", "企業", "集團", "股票", "股價", "市場", "大盤", "個股",
    "投資", "投資人", "法人", "外資", "投信", "自營商",
    "業績", "獲利", "營收", "毛利", "淨利", "EPS", "股利",
    "成長", "增加", "提升", "下滑", "衰退", "持平", "攀升", "下降",
    "漲停", "跌停", "漲幅", "跌幅", "收盤", "開盤", "盤中",
    "董事長", "執行長", "總經理", "董事會", "股東會", "法說會",
    "子公司", "母公司", "轉投資",
    # Yahoo 頁面模板詞
    "加入", "頻道", "新聞", "設定", "主頁", "找尋", "報導",
    "發布", "發布人", "主旨", "公告", "內容",
    # 公告文件結構詞
    "符合", "第幾屆", "開始", "時間", "地點", "確認", "無",
    "其他", "說明", "事項",
    # 財務報表固定詞（每篇財報文章都有，非題材）
    "每股", "盈餘", "損益表", "季綜合", "損益", "配發", "發行", "金額",
    "本期", "稅前", "稅後", "淨額",
    # 地理 / 大盤泛稱（過於廣泛）
    "台北", "民國", "台股", "台灣", "股市", "國際", "全球",
    # 公司治理固定用語
    "股東", "決議", "董事", "證券", "交易所",
    # 通用形容詞 / 副詞 / 動詞
    "資訊", "中心", "基本", "持續", "全面", "綜合", "影響", "年度",
    "以上", "提供", "使用", "我們", "更多", "偏好",
    "持有", "轉讓", "申報", "登記", "處分",
    # Yahoo 頁腳安全網（footer regex 後仍可能殘留的碎片）
    "首選來源", "財務資訊", "服務前", "精彩", "詳閱", "設為",
    "料及", "財團", "規範", "聲明", "查看",
    # 斷詞碎片（台灣 / 台股在文中被錯誤切割的殘字）
    "灣證券", "灣期貨", "股行情", "灣期",
    # 財報表頭整詞（需搭配 _FINANCIAL_COMPOUNDS 加入 jieba 字典）
    "資產負債表", "現金流量表", "綜合損益表", "財務報告", "財務報表",
    "股東常會", "股東臨時會", "月合計", "季合計",
    # 財報表頭碎片（jieba 切割後的殘字）
    "季資產負", "債表", "流量表", "季現", "季財務", "月合", "季合",
    # 公告模板固定詞
    "財務", "報告", "年增", "通過", "召開", "日期",
    "有限公司", "股份有限公司", "股份", "重訊",
    "東常會", "常會", "年股", "受邀",
    "事宜", "明會", "更正", "注意",
    "取得", "舉辦", "參加", "合計",
    # 雜訊動名詞
    "原因", "以及", "營業", "成交", "交易", "自營",
    "列入", "財經", "編輯",
    # 市場技術指標（非題材）
    "均量", "本益比", "基準", "日三大", "三大法人",
    # 斷詞碎片（有價證券/融券資比/股價）
    "有價", "券資", "日收盤", "價漲", "日漲",
    # 數字/數量碎片
    "六個", "一步",
    # 通用連接詞/副詞
    "其中", "加上", "包括", "部分", "同步",
    # 通用動詞（非題材）
    "辦理", "完成", "新增",
    # 公告/公司治理雜訊
    "公司公告", "普通股", "重大",
    # 市場數據詞（非題材）
    "萬張", "成交量", "盤價", "日收", "外資賣",
    # 通用動名詞（再清一輪）
    "調整", "累積", "成為", "有望", "公布", "能力",
    "標準", "規模", "核心", "首季", "價格", "分派",
    # 公司名稱後綴碎片
    "科技股份", "股份公司",
    # 碎片詞
    "價為", "檔新",
    # 公告/選舉/會議模板
    "當選名", "明細表", "今開", "人化", "續任",
    # 市場行情詞（非題材）
    "價跌", "打落",
    # 通用詞彙（無題材辨別力）
    "格式", "新舊", "盲目", "新篇章", "依證",
    # 常見人名碎片（董監事名稱）
    "淑芳",
    # 過於泛用的英文詞
    "Data", "Award",
    # 財報/新聞用語雜訊（測試趨勢報告時發現的高頻假訊號）
    "收入", "快訊", "年減", "年增率", "原訂", "投票", "H1", "H2", "Q1", "Q2", "Q3", "Q4",
}

# ═══════════════════════════════════════════════════════════════
# 三個新聞表設定
# ═══════════════════════════════════════════════════════════════
NEWS_TABLES = [
    {"table": "yahoo_stock_news",  "date_col": "fetched_date"},
    {"table": "cnyes_stock_news",  "date_col": "fetched_date"},
    {"table": "nstock_stock_news", "date_col": "fetched_date"},
]

# ═══════════════════════════════════════════════════════════════
# 載入公司名稱 & 建 jieba 詞典
# ═══════════════════════════════════════════════════════════════
def load_company_names() -> set[str]:
    names: set[str] = set()
    for fname in CSV_FILES:
        fpath = CSV_DIR / fname
        if not fpath.exists():
            print(f"  [WARN] CSV 不存在: {fpath}")
            continue
        with open(fpath, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                name = row.get("股票名稱", "").strip()
                if name:
                    names.add(name)
    print(f"  載入 {len(names)} 個股票名稱（公司）")
    return names


_FINANCIAL_COMPOUNDS = {
    # 財報表頭：加入字典讓 jieba 整詞切割，再由 stopwords 過濾
    "資產負債表", "現金流量表", "綜合損益表", "財務報告", "財務報表",
    # 股東大會：_ANNOUNCE_RE 過不到 content 裡的提及，整詞切割再過濾
    "股東常會", "股東臨時會",
    # 月/季合計：避免「月合」「季合」碎片
    "月合計", "季合計",
}


def setup_jieba(company_names: set[str]) -> None:
    """把公司名稱和機構名稱加入 jieba，讓它們成為完整 token，方便後續過濾"""
    for name in company_names:
        jieba.add_word(name, freq=1000, tag="company")
    for name in INSTITUTIONS:
        jieba.add_word(name, freq=1000, tag="institution")
    for term in _FINANCIAL_COMPOUNDS:
        jieba.add_word(term, freq=1000)
    jieba.initialize()
    print("  jieba 詞典初始化完成")


# ═══════════════════════════════════════════════════════════════
# 斷詞 + 過濾
# ═══════════════════════════════════════════════════════════════
_PURE_NUM   = re.compile(r"^[\d\s,，.．%％+\-()（）]+$")
_STOCK_ID   = re.compile(r"^\d{4,5}$")       # 股票代號如 2330
_SINGLE_EN  = re.compile(r"^[A-Za-z]$")      # 單一英文字母


def extract_keywords(text: str, filter_set: set[str]) -> list[str]:
    if not text or len(text) < 5:
        return []

    seen: set[str] = set()
    result: list[str] = []

    for tok in jieba.cut(text, cut_all=False):
        tok = tok.strip()
        if len(tok) < 2:
            continue
        if tok in filter_set:        # 公司名稱 / 機構名稱
            continue
        if tok in STOPWORDS:
            continue
        if _PURE_NUM.match(tok):     # 純數字/符號
            continue
        if _STOCK_ID.match(tok):     # 股票代號
            continue
        if _SINGLE_EN.match(tok):    # 單一英文字母
            continue
        if tok not in seen:
            seen.add(tok)
            result.append(tok)

    return result


# ═══════════════════════════════════════════════════════════════
# DB 處理
# ═══════════════════════════════════════════════════════════════
async def process_table(
    conn: asyncpg.Connection,
    tbl_cfg: dict,
    filter_set: set[str],
) -> int:
    tbl      = tbl_cfg["table"]
    date_col = tbl_cfg["date_col"]

    await conn.execute(f"""
        ALTER TABLE public.{tbl}
        ADD COLUMN IF NOT EXISTS keywords TEXT[];
    """)

    date_filter = (
        f"AND {date_col} >= CURRENT_DATE - INTERVAL '{DAYS_BACK} days'"
        if DAYS_BACK is not None else ""
    )
    rows = await conn.fetch(f"""
        SELECT id, title, content
        FROM   {tbl}
        WHERE  keywords IS NULL
          {date_filter}
        ORDER  BY id DESC
    """)

    if not rows:
        print(f"  [{tbl}] 無待處理資料")
        return 0

    print(f"  [{tbl}] 待處理 {len(rows)} 筆...")

    updates = []
    for row in rows:
        title   = row["title"] or ""
        content = row["content"] or ""

        # 公告型文章只取 title（content 是固定模板，干擾大）
        if _ANNOUNCE_RE.search(title):
            text = title
        else:
            # 一般新聞：清除 Yahoo 頁頭 + 頁腳模板後再取用
            cleaned = _YAHOO_HEADER_RE.sub("", content, count=1)
            cleaned = _YAHOO_FOOTER_RE.sub("", cleaned, count=1)
            text = f"{title} {cleaned}"

        kws = extract_keywords(text, filter_set)
        updates.append((kws or [], row["id"]))

    await conn.executemany(
        f"UPDATE {tbl} SET keywords = $1 WHERE id = $2",
        updates,
    )

    print(f"  [{tbl}] done {len(updates)} rows")
    return len(updates)


# ═══════════════════════════════════════════════════════════════
# 範例輸出：印出幾篇文章斷詞結果
# ═══════════════════════════════════════════════════════════════
async def preview_sample(conn: asyncpg.Connection, filter_set: set[str], n: int = 5) -> None:
    rows = await conn.fetch("""
        SELECT title, keywords
        FROM   yahoo_stock_news
        WHERE  keywords IS NOT NULL
        ORDER  BY id DESC
        LIMIT  $1
    """, n)

    print("\n── 斷詞預覽（最新 5 筆）─────────────────────────────────")
    for row in rows:
        title = (row["title"] or "")[:40]
        kws   = row["keywords"] or []
        print(f"  標題: {title}")
        print(f"  關鍵詞: {', '.join(kws[:15])}")
        print()


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════
async def main() -> None:
    print("=" * 52)
    print("  第一階段：關鍵詞萃取")
    print(f"  資料範圍：近 {DAYS_BACK} 天")
    print("=" * 52)

    print("\n[1] 載入公司名稱 & 初始化 jieba")
    csv_names = load_company_names()

    # 補充從 DB stock_mapping 讀取：涵蓋新聞中被「提及」但未在 CSV 裡的公司
    conn = await asyncpg.connect(PG_DSN)
    db_names: set[str] = set()
    try:
        rows = await conn.fetch(
            "SELECT stock_name FROM stock_mapping WHERE stock_name IS NOT NULL"
        )
        db_names = {
            r["stock_name"].strip()
            for r in rows
            if r["stock_name"] and len(r["stock_name"].strip()) >= 2
        }
        print(f"  stock_mapping 補充 {len(db_names)} 個公司名稱")
    except Exception as e:
        print(f"  [WARN] 無法載入 stock_mapping: {e}")

    company_names = csv_names | db_names
    filter_set    = company_names | INSTITUTIONS
    setup_jieba(company_names)

    print("\n[2] 開始處理...")
    total = 0
    try:
        for tbl_cfg in NEWS_TABLES:
            total += await process_table(conn, tbl_cfg, filter_set)

        if total > 0:
            await preview_sample(conn, filter_set)
    finally:
        await conn.close()

    print(f"\n{'=' * 52}")
    print(f"  完成！共處理 {total} 筆新聞")
    print(f"  關鍵詞已寫入各表的 keywords 欄位")
    print("=" * 52)


if __name__ == "__main__":
    asyncio.run(main())
