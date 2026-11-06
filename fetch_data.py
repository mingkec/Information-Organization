"""每天收盤後由 GitHub Actions 執行：抓 ETF 成分股、股價、本益比，加密後寫成 data.json。

資料來源（全部免金鑰）
- 股價：臺灣證券交易所「每日收盤行情」、證券櫃檯買賣中心「上櫃股票每日收盤行情」，逐日抓全市場
- 本益比：證交所、櫃買中心 OpenAPI（政府資料開放授權條款）
- 成分股：MoneyDJ ETF 持股頁（抓不到時改用 config.json 的 manual_holdings）

歷史股價會存在 prices_cache.json（由 GitHub Actions 快取保存，不進專案），
所以第一次要回補約 60 個交易日（5～8 分鐘），之後每天只抓當天。
"""
import base64
import datetime as dt
import json
import os
import re
import sys
import time
from io import StringIO

import pandas as pd
import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "data.json")
CACHE = os.path.join(ROOT, "prices_cache.json")
with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
    CONFIG = json.load(f)

PASSWORD = os.environ.get("APP_PASSWORD", "")
ITERATIONS = 310_000
TW = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime.now(TW)
TODAY = NOW.date()
LOOKBACK = CONFIG.get("lookback", [1, 3, 5, 7, 14, 28, 56])
MAX_CAL_DAYS = 140          # 最多往回找幾個日曆天
POLITE_DELAY = 3.0          # 證交所請求太密會被暫時封鎖

TWSE_PRICE = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_PRICE = "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc"
TWSE_PE = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
TPEX_PE = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Safari/605.1.15",
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
})
CODE_RE = re.compile(r"^(.*?)\((\d{4,6}[A-Z]?)\.(TW|TWO)\)\s*$")


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def get(url, params=None, kind="json", tries=3):
    for i in range(tries):
        try:
            r = SESSION.get(url, params=params, timeout=40)
            r.raise_for_status()
            if kind == "json":
                return r.json()
            if "charset" not in r.headers.get("content-type", "").lower():
                r.encoding = "utf-8"
            return r.text
        except Exception as e:  # noqa: BLE001
            log(f"  ! {url} 第 {i + 1} 次失敗：{e}")
            time.sleep(8 * (i + 1))
    return None


def to_num(v):
    s = re.sub(r"<[^>]+>", "", str(v)).replace(",", "").strip()
    try:
        x = float(s)
    except ValueError:
        return None  # "--"、"N/A"、空白
    return x if x > 0 else None


# ---------- 加密 ----------

def b64e(b):
    return base64.b64encode(b).decode()


def derive_key(salt, iterations=ITERATIONS):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(PASSWORD.encode("utf-8"))


def encrypt(obj, salt):
    iv = os.urandom(12)
    plain = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ct = AESGCM(derive_key(salt)).encrypt(iv, plain, None)
    return {"enc": 1, "kdf": "PBKDF2-SHA256", "iter": ITERATIONS,
            "salt": b64e(salt), "iv": b64e(iv), "ct": b64e(ct)}


def load_old():
    """讀上次的 data.json，回傳 (內容, salt)。密碼沒變就沿用 salt，手機上記住的密碼才不會失效。"""
    if not os.path.exists(OUT):
        return {}, None
    with open(OUT, encoding="utf-8") as f:
        raw = json.load(f)
    if not raw.get("enc"):
        return raw, None
    salt = base64.b64decode(raw["salt"])
    try:
        key = derive_key(salt, raw["iter"])
        plain = AESGCM(key).decrypt(base64.b64decode(raw["iv"]), base64.b64decode(raw["ct"]), None)
        return json.loads(plain), (salt if raw["iter"] == ITERATIONS else None)
    except Exception:  # noqa: BLE001  密碼換了：舊資料打不開，salt 也重新產生
        log("  上次的資料解不開（密碼可能改過），重新產生")
        return {}, None


# ---------- 成分股 ----------

def parse_holdings(html):
    """從 MoneyDJ 頁面的表格找出「名稱(代號.TW) | 比例」這種列，回傳最長的那張表。"""
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError:
        return []
    best = []
    for t in tables:
        rows, seen = [], set()
        for _, row in t.iterrows():
            cells = [str(c).strip() for c in row.tolist()]
            for i, c in enumerate(cells):
                m = CODE_RE.match(c)
                if not m:
                    continue
                code = m.group(2)
                if code in seen:
                    break
                weight = None
                if i + 1 < len(cells):
                    try:
                        weight = float(cells[i + 1].replace(",", ""))
                    except ValueError:
                        pass
                rows.append({"code": code, "name": m.group(1).strip(), "weight": weight})
                seen.add(code)
                break
        if len(rows) > len(best):
            best = rows
    return best


def holdings_from_moneydj(etf):
    urls = [
        f"https://www.moneydj.com/ETF/X/Basic/Basic0007B.xdjhtm?etfid={etf}.TW",  # 全部持股
        f"https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid={etf}.TW",   # 前十大
    ]
    for url in urls:
        html = get(url, kind="text", tries=2)
        rows = parse_holdings(html) if html else []
        if rows:
            return rows, url
        log(f"  ! {url} 沒有找到持股表")
        time.sleep(1)
    return [], None


# ---------- 每日全市場收盤價 ----------

def find_table(j, *, code_kw="代號", name_kw="名稱", close_kw="收盤"):
    """在證交所／櫃買的 JSON 裡找出有「代號」和「收盤」欄位的那張表。"""
    if not isinstance(j, dict):
        return None
    tables = list(j.get("tables") or [])
    if j.get("fields") and j.get("data"):
        tables.insert(0, {"fields": j["fields"], "data": j["data"]})
    for t in tables:
        fields = [str(x) for x in (t.get("fields") or [])]
        ci = next((i for i, x in enumerate(fields) if code_kw in x), None)
        ni = next((i for i, x in enumerate(fields) if name_kw in x), None)
        pi = next((i for i, x in enumerate(fields) if close_kw in x), None)
        if ci is not None and pi is not None and t.get("data"):
            return t["data"], ci, ni, pi
    return None


def table_to_closes(found):
    closes, names = {}, {}
    if not found:
        return closes, names
    data, ci, ni, pi = found
    for row in data:
        code = str(row[ci]).strip()
        if not re.fullmatch(r"\d{4,6}[A-Z]?", code):
            continue
        c = to_num(row[pi])
        if c:
            closes[code] = c
        if ni is not None:
            names[code] = str(row[ni]).strip()
    return closes, names


def date_matches(j, d):
    """櫃買的回應若帶日期，確認真的是我們要的那天（避免參數被忽略、回傳成今天的資料）。"""
    for k in ("date", "reportDate"):
        v = j.get(k) if isinstance(j, dict) else None
        if v:
            digits = re.sub(r"\D", "", str(v))
            roc = f"{d.year - 1911}{d:%m%d}"
            return digits in (d.strftime("%Y%m%d"), roc)
    return True


def fetch_day(d):
    """回傳 {"twse": {...}, "tpex": {...}}；休市回傳 {"holiday": True}；連線失敗回傳 None。"""
    j = get(TWSE_PRICE, {"date": d.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"})
    time.sleep(POLITE_DELAY)
    if j is None:
        return None
    if str(j.get("stat", "")).upper() != "OK":
        return {"holiday": True} if d < TODAY else None
    twse, names1 = table_to_closes(find_table(j))
    if not twse:
        log(f"  ! {d} 證交所格式看不懂，欄位：{[t.get('fields') for t in j.get('tables', [])][:3]}")
        return None

    tpex, names2 = {}, {}
    for date_str in (d.strftime("%Y/%m/%d"), f"{d.year - 1911}/{d:%m/%d}"):
        k = get(TPEX_PRICE, {"date": date_str, "type": "EW", "response": "json"}, tries=2)
        time.sleep(1.5)
        if k and date_matches(k, d):
            tpex, names2 = table_to_closes(find_table(k))
            if tpex:
                break
    if not tpex:
        log(f"  ! {d} 櫃買沒抓到（上櫃個股這天會缺資料）")
    return {"twse": twse, "tpex": tpex, "names": {**names2, **names1}}


def load_cache():
    if os.path.exists(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            pass
    return {}


def trading_days(cache):
    """往回找到足夠的交易日，缺的日子才去抓。回傳由新到舊的日期字串。"""
    need = max(LOOKBACK) + 1
    days = []
    fetched = 0
    for back in range(MAX_CAL_DAYS):
        d = TODAY - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        key = d.isoformat()
        entry = cache.get(key)
        if entry is None or (key == TODAY.isoformat() and entry.get("holiday")):
            log(f"抓 {key}")
            entry = fetch_day(d)
            fetched += 1
            if entry is None:
                continue
            cache[key] = entry
        if not entry.get("holiday"):
            days.append(key)
            if len(days) >= need:
                break
    log(f"本次抓了 {fetched} 天，共 {len(days)} 個交易日")
    # 清掉太舊的快取
    oldest = (TODAY - dt.timedelta(days=MAX_CAL_DAYS)).isoformat()
    for k in [k for k in cache if k < oldest]:
        del cache[k]
    return days


# ---------- 本益比 ----------

def pick(row, *cands):
    for c in cands:
        for k, v in row.items():
            if k.lower() == c.lower():
                return v
    return None


def pe_table():
    out = {}
    for url, code_keys, pe_keys, pb_keys, dy_keys in (
        (TWSE_PE, ("Code",), ("PEratio",), ("PBratio",), ("DividendYield",)),
        (TPEX_PE, ("SecuritiesCompanyCode", "Code"), ("PriceEarningRatio", "PEratio"),
         ("PriceBookRatio", "PBratio"), ("YieldRatio", "DividendYield")),
    ):
        rows = get(url) or []
        time.sleep(1)
        if not isinstance(rows, list):
            rows = []
        log(f"本益比 {url.split('/')[-1]}：{len(rows)} 筆")
        for r in rows:
            code = str(pick(r, *code_keys) or "").strip()
            if code:
                out[code] = {"pe": to_num(pick(r, *pe_keys)), "pb": to_num(pick(r, *pb_keys)),
                             "dy": to_num(pick(r, *dy_keys))}
    return out


# ---------- 主程式 ----------

def main():
    if not PASSWORD:
        log("! 沒有設定 APP_PASSWORD，為了不讓資料公開，這次不更新。請到 GitHub 的 Settings → Secrets 新增。")
        sys.exit(1)

    old, salt = load_old()
    cache = load_cache()
    days = trading_days(cache)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))
    if not days:
        log("! 一個交易日都沒抓到，可能被證交所暫時封鎖或網路有問題")
        sys.exit(1)

    names = {}
    for k in reversed(days):
        names.update(cache[k].get("names", {}))

    manual = CONFIG.get("manual_holdings", {})
    etfs = {}
    for etf, etf_name in CONFIG["etfs"].items():
        etf_name = etf_name or names.get(etf, etf)  # app 新增時只給代號，名稱從證交所資料補上
        log(f"成分股 {etf} {etf_name}")
        if manual.get(etf):
            rows = [{"code": c, "name": names.get(c, c), "weight": None} for c in manual[etf]]
            source = "manual"
        else:
            rows, source = holdings_from_moneydj(etf)
            if not rows and etf in old.get("etfs", {}):
                log("  沿用上次的成分股")
                rows = old["etfs"][etf]["holdings"]
                source = old["etfs"][etf].get("source", "previous")
        log(f"  {len(rows)} 檔（{source}）")
        etfs[etf] = {"name": etf_name, "holdings": rows, "source": source}

    pes = pe_table()

    def close_on(code, key):
        e = cache[key]
        return e.get("twse", {}).get(code) or e.get("tpex", {}).get(code)

    stocks = {}
    for code in sorted({h["code"] for e in etfs.values() for h in e["holdings"]}):
        # 最新收盤：最近一個有成交的交易日（停牌時往前找）
        start = next((i for i, k in enumerate(days) if close_on(code, k)), None)
        if start is None:
            log(f"  ! {code} 找不到股價")
            continue
        series = days[start:]
        history = {}
        for n in LOOKBACK:
            if n < len(series):
                c = close_on(code, series[n])
                if c:
                    history[str(n)] = {"date": series[n], "close": c}
        p = pes.get(code, {})
        stocks[code] = {"date": series[0], "close": close_on(code, series[0]),
                        "pe": p.get("pe"), "pb": p.get("pb"), "dy": p.get("dy"),
                        "history": history, **({"stale": True} if start > 0 else {})}

    out = {
        "updated_at": NOW.isoformat(timespec="minutes"),
        "trade_date": days[0],
        "lookback": LOOKBACK,
        "etfs": etfs,
        "stocks": stocks,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(encrypt(out, salt or os.urandom(16)), f, separators=(",", ":"))
    log(f"完成：{len(etfs)} 檔 ETF、{len(stocks)} 檔個股，資料日期 {days[0]}")


if __name__ == "__main__":
    main()
