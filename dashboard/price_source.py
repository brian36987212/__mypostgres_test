"""
股價 / 產業別來源：台灣證交所 (TWSE) + 櫃買中心 (TPEX) 官方 OpenAPI。

- 免安裝額外套件（只用標準庫 urllib）
- 一次抓全市場，模組層級快取（預設 30 分鐘），因日線資料一天只更新一次
- get_price(stock_id) 回傳 {price, change, change_pct, open, high, low, trade_date}
- get_industry(stock_id) 回傳產業別字串（僅上市，OTC 目前無來源）
"""

import json
import ssl
import threading
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# TWSE / TPEX 的憑證鏈在部分環境無法驗證，這裡放寬（僅讀取公開資料）
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

TWSE_DAY_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_QUOTES = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
TWSE_COMPANY = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"

CACHE_TTL_SECONDS = 30 * 60

# TWSE 產業別代碼對照（t187ap03_L 的「產業別」欄位為數字代碼）
TWSE_INDUSTRY = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維",
    "05": "電機機械", "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業",
    "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "14": "建材營造",
    "15": "航運業", "16": "觀光事業", "17": "金融保險", "18": "貿易百貨",
    "19": "綜合", "20": "其他", "21": "化學工業", "22": "生技醫療業",
    "23": "油電燃氣業", "24": "半導體業", "25": "電腦及週邊設備業", "26": "光電業",
    "27": "通信網路業", "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業",
    "31": "其他電子業", "32": "文化創意業", "33": "農業科技業", "34": "電子商務",
    "35": "綠能環保", "36": "數位雲端", "37": "運動休閒", "38": "居家生活", "80": "管理股票",
}


def _industry_name(code):
    code = (code or "").strip()
    return TWSE_INDUSTRY.get(code.zfill(2), TWSE_INDUSTRY.get(code, code))

_lock = threading.Lock()
_cache = {
    "prices": {},      # {stock_id: {...}}
    "industries": {},  # {stock_id: str}
    "fetched_ts": None,
}


def _http_json(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as resp:
        return json.load(resp)


def _to_float(s):
    try:
        return float(str(s).replace(",", "").replace("+", "").strip())
    except (ValueError, AttributeError):
        return None


def _roc_to_date(roc):
    """'1150828' -> '2026/08/28'"""
    try:
        roc = str(roc).strip()
        y = int(roc[:-4]) + 1911
        return f"{y}/{roc[-4:-2]}/{roc[-2:]}"
    except (ValueError, IndexError):
        return None


def _build_price(code, name, open_, high, low, close, change_raw, roc_date):
    close = _to_float(close)
    change = _to_float(change_raw)
    if close is None:
        return None
    prev = close - change if change is not None else None
    change_pct = round(change / prev * 100, 2) if (change is not None and prev) else None
    return {
        "stock_id": code,
        "name": name,
        "price": round(close, 2),
        "change": round(change, 2) if change is not None else None,
        "change_pct": change_pct,
        "open": _to_float(open_),
        "high": _to_float(high),
        "low": _to_float(low),
        "trade_date": _roc_to_date(roc_date),
    }


def _refresh():
    # 從既有快取複製，任一來源失敗時保留舊資料（不因單一來源掛掉而清空）
    prices = dict(_cache["prices"])
    industries = dict(_cache["industries"])
    twse_ok = tpex_ok = ind_ok = False

    # 上市（含漲跌）
    try:
        for r in _http_json(TWSE_DAY_ALL):
            code = str(r.get("Code", "")).strip()
            p = _build_price(
                code, r.get("Name"), r.get("OpeningPrice"), r.get("HighestPrice"),
                r.get("LowestPrice"), r.get("ClosingPrice"), r.get("Change"), r.get("Date"),
            )
            if code and p:
                prices[code] = p
        twse_ok = True
    except Exception as e:  # noqa: BLE001 - 來源失效不應讓整頁掛掉
        print(f"[price_source] TWSE 抓取失敗: {e}")

    # 上櫃（Change 帶正負號）
    try:
        for r in _http_json(TPEX_QUOTES):
            code = str(r.get("SecuritiesCompanyCode", "")).strip()
            p = _build_price(
                code, r.get("CompanyName"), r.get("Open"), r.get("High"),
                r.get("Low"), r.get("Close"), r.get("Change"), r.get("Date"),
            )
            if code and p:
                prices[code] = p
        tpex_ok = True
    except Exception as e:  # noqa: BLE001
        print(f"[price_source] TPEX 抓取失敗: {e}")

    # 上市產業別
    try:
        for r in _http_json(TWSE_COMPANY):
            code = str(r.get("公司代號", "")).strip()
            ind = _industry_name(r.get("產業別"))
            if code and ind:
                industries[code] = ind
        ind_ok = True
    except Exception as e:  # noqa: BLE001
        print(f"[price_source] 產業別抓取失敗: {e}")

    _cache["prices"] = prices
    _cache["industries"] = industries

    print(f"[price_source] refresh twse_ok={twse_ok} tpex_ok={tpex_ok} ind_ok={ind_ok} "
          f"prices={len(prices)} industries={len(industries)}", flush=True)

    now = datetime.now(ZoneInfo("Asia/Taipei"))
    if twse_ok and tpex_ok and ind_ok:
        _cache["fetched_ts"] = now          # 全部成功 → 正常 30 分鐘
    else:
        # 部分/全部失敗 → 約 3 分鐘後重試（回填 fetched_ts 以縮短 TTL），避免殘缺結果被鎖住
        _cache["fetched_ts"] = now - timedelta(seconds=max(0, CACHE_TTL_SECONDS - 180))


def _ensure_fresh():
    ts = _cache["fetched_ts"]
    if ts is not None:
        age = (datetime.now(ZoneInfo("Asia/Taipei")) - ts).total_seconds()
        if age < CACHE_TTL_SECONDS and _cache["prices"]:
            return
    with _lock:
        # 進入鎖後再檢查一次，避免並發重複抓取
        ts = _cache["fetched_ts"]
        if ts is not None:
            age = (datetime.now(ZoneInfo("Asia/Taipei")) - ts).total_seconds()
            if age < CACHE_TTL_SECONDS and _cache["prices"]:
                return
        _refresh()


def get_price(stock_id):
    try:
        _ensure_fresh()
    except Exception as e:  # noqa: BLE001
        print(f"[price_source] refresh error: {e}")
        return None
    return _cache["prices"].get(str(stock_id).strip())


def get_industry(stock_id):
    try:
        _ensure_fresh()
    except Exception:  # noqa: BLE001
        return None
    return _cache["industries"].get(str(stock_id).strip())
