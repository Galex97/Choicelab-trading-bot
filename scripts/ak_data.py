"""
共享数据获取模块 — Python 版数据源。

数据源:
- 新浪 hq.sinajs.cn — 实时行情（价格、开盘、最高、最低、昨收）
- 新浪 K线 money.finance.sina.com.cn — 历史日K线
- akshare stock_zh_a_spot() — 全市场扫描（慢，但含PE/PB/市值）
- RESSET CSV — 基本面（PE/ROE/EPS）静态数据

注意: 东财 push2.eastmoney.com 在本机 Python 环境下不可用（服务器断开连接），
      但 Node.js 可用。因此全市场PE/PB数据通过 akshare 的 Sina 源获取。
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import re
import os

# ====== East Money fallback (Bug2修复) ======

EM_SPOT_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
EM_HEADERS = {
    "Referer": "https://finance.eastmoney.com",
    "User-Agent": "Mozilla/5.0",
}


def _get_em_spot(codes):
    """
    东财实时行情备用源（Bug2修复）。
    返回和 get_spot_prices() 相同格式的 DataFrame，失败返回空 DataFrame。

    参数:
        codes: list of str, e.g. ['600377', '000100']
    """
    if not codes:
        return pd.DataFrame()

    # 东财格式: sh600377,sz000100
    secids = []
    for c in codes:
        c = str(c).zfill(6)
        prefix = "1" if c.startswith("6") else "0"
        secids.append(f"{prefix}.{c}")

    params = {
        "secids": ",".join(secids),
        "fields": "f2,f3,f4,f5,f6,f12,f14,f15,f16,f17,f18",
        "fltt": 2,
        "invt": 2,
    }

    try:
        r = requests.get(EM_SPOT_URL, params=params, headers=EM_HEADERS, timeout=8)
        data = r.json()
        items = data.get("data", {}).get("diff", [])
    except Exception as e:
        print(f"  [ak_data] EM fallback error: {e}")
        return pd.DataFrame()

    rows = []
    for item in items:
        # f2=现价*100, f3=涨跌幅*100, f4=涨跌额*100, f5=成交量, f6=成交额
        # f12=代码, f14=名称, f15=最高*100, f16=最低*100, f17=开盘*100, f18=昨收*100
        def _ef(val, divisor=100.0):
            try:
                v = float(val)
                return v / divisor if v != "-" else None
            except (TypeError, ValueError):
                return None

        current = _ef(item.get("f2"))
        yesterday = _ef(item.get("f18"))
        rows.append({
            "code": str(item.get("f12", "")).zfill(6),
            "name": item.get("f14", ""),
            "price": current,
            "open": _ef(item.get("f17")),
            "high": _ef(item.get("f15")),
            "low": _ef(item.get("f16")),
            "yesterday": yesterday,
            "change": _ef(item.get("f4")),
            "changePct": _ef(item.get("f3")),  # 已是百分比形式
            "volume": float(item.get("f5", 0) or 0),
            "amount": float(item.get("f6", 0) or 0),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M:%S"),
        })

    return pd.DataFrame(rows)


def _is_stale_sina_data(df):
    """
    检测新浪返回数据是否陈旧（Bug2修复）。
    判断逻辑：
      - 当前为交易时段（包含下午 13:00-15:00）
      - 且数据中 time 字段指向 11:30 之前
    """
    if df.empty:
        return True

    now = datetime.now()
    hour, minute = now.hour, now.minute
    is_afternoon = (hour == 13) or (hour == 14) or (hour == 15 and minute == 0)
    if not is_afternoon:
        return False  # 上午或非交易时段，不判定为陈旧

    # 取第一行的 time 字段比较
    if "time" not in df.columns:
        return False
    sample_time = str(df.iloc[0]["time"]).strip()
    try:
        t = datetime.strptime(sample_time, "%H:%M:%S").time()
        # 当前为下午且数据 time <= 11:30 → 认为是午休快照
        if t.hour < 12:
            print(f"  [ak_data] Sina data is stale (time={sample_time}), switching to EM fallback")
            return True
    except ValueError:
        pass
    return False


# ====== Real-time spot prices (Sina) ======

SINA_SPOT_URL = "https://hq.sinajs.cn/list="
SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0",
}


def _codes_to_sina_symbols(codes):
    """Convert codes to Sina format: ['sh600377', 'sz000100', ...]"""
    result = []
    for c in codes:
        c = str(c).zfill(6)
        prefix = "sh" if c.startswith("6") else "sz"
        result.append(prefix + c)
    return result


def get_spot_prices(codes):
    """
    获取指定股票的实时行情（新浪源，速度快）。

    参数:
        codes: list of str, e.g. ['600377', '000100']
    返回:
        DataFrame with columns: code, name, price, open, high, low,
        yesterday, change, changePct, volume, amount, date, time
    """
    if not codes:
        return pd.DataFrame()

    symbols = _codes_to_sina_symbols(codes)
    url = SINA_SPOT_URL + ",".join(symbols)

    try:
        r = requests.get(url, headers=SINA_HEADERS, timeout=10)
        r.encoding = "gbk"
        raw = r.text
    except Exception as e:
        print(f"  [ak_data] Sina spot error: {e}")
        return pd.DataFrame()

    rows = []
    for line in raw.strip().split("\n"):
        m = re.search(r'hq_str_(..)(\d+)="(.*)"', line)
        if not m:
            continue
        code = m.group(2)
        fields = m.group(3).split(",")
        if len(fields) < 32:
            continue

        current = _safe_float(fields[3])
        yesterday = _safe_float(fields[2])
        rows.append({
            "code": code,
            "name": fields[0],
            "price": current or yesterday,
            "open": _safe_float(fields[1]) or current or yesterday,
            "high": _safe_float(fields[4]) or current or yesterday,
            "low": _safe_float(fields[5]) or current or yesterday,
            "yesterday": yesterday,
            "change": round(current - yesterday, 2) if current and yesterday else None,
            "changePct": round((current - yesterday) / yesterday * 100, 2) if current and yesterday else None,
            "volume": _safe_float(fields[8]),
            "amount": _safe_float(fields[9]),
            "date": fields[30],
            "time": fields[31],
        })

    df = pd.DataFrame(rows)

    # Bug2修复：检测到陈旧数据时自动切换到东财备用源
    if _is_stale_sina_data(df):
        em_df = _get_em_spot(codes)
        if not em_df.empty:
            return em_df
        # 如果东财也失败，仍返回新浪陈旧数据（总比什么都没有好）
        print("  [ak_data] EM fallback also failed, returning stale Sina data as last resort")

    return df


def _safe_float(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def get_spot_for(codes):
    """
    获取指定股票的实时行情，返回与 monitor_10min.mjs fetchPrices() 兼容的 dict 格式。

    返回: {code: {name, current, open, high, low, yesterday, change, changePct, isLive}}
    """
    df = get_spot_prices(codes)
    if df.empty:
        return {}

    now = datetime.now()
    hour = now.hour
    minute = now.minute
    is_live = ((hour == 9 and minute >= 30) or (hour == 10) or
               (hour == 11 and minute <= 30) or
               (hour == 13) or (hour == 14))

    results = {}
    for _, row in df.iterrows():
        code = row["code"]
        results[code] = {
            "name": row["name"],
            "current": row["price"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "yesterday": row["yesterday"],
            "change": row["change"],
            "changePct": f"{row['changePct']}%" if row["changePct"] is not None else "0.00%",
            "isLive": is_live and row["price"] is not None and row["price"] > 0,
        }
    return results


# ====== Full market scan (akshare Sina source) ======

def get_all_stocks_spot():
    """
    获取全市场A股实时行情（含PE/PB/市值）。
    底层用 akshare 的 stock_zh_a_spot()（新浪源），约14秒。
    适合盘后选股，不适合盘中高频调用。

    返回:
        DataFrame with columns: code, name, price, changePct, change,
        open, high, low, yesterday, volume, amount, pe, pb, mktCap
    """
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot()
    except Exception as e:
        print(f"  [ak_data] akshare full scan error: {e}")
        return pd.DataFrame()

    # Normalize column names (akshare uses Chinese)
    col_map = {
        "代码": "code", "名称": "name", "最新价": "price",
        "涨跌额": "change", "涨跌幅": "changePct",
        "今开": "open", "最高": "high", "最低": "low",
        "昨收": "yesterday", "成交量": "volume", "成交额": "amount",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    # Note: Sina source doesn't include PE/PB, so those columns won't exist
    # We merge with RESSET later for fundamentals
    for col in ["price", "changePct", "change", "open", "high", "low",
                "yesterday", "volume", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "code" in df.columns:
        df["code"] = df["code"].astype(str).str.replace("bj", "").str.zfill(6)

    return df


# ====== Historical K-line (Sina) ======

SINA_KL_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"


def _code_to_sina_sym(code):
    code = str(code).zfill(6)
    return ("sh" if code.startswith("6") else "sz") + code


def get_daily_kline(code, days=60):
    """
    获取个股日K线。

    参数:
        code: '000100' or '600377'
        days: 获取天数
    返回:
        DataFrame with columns: date, open, close, high, low, volume
        或 None
    """
    sym = _code_to_sina_sym(code)
    params = {"symbol": sym, "scale": 240, "ma": "no", "datalen": str(days)}
    try:
        r = requests.get(SINA_KL_URL, params=params,
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if not r.text or r.text.strip() == "null":
            return None
        data = r.json()
        if not isinstance(data, list) or len(data) == 0:
            return None

        rows = []
        for d in data:
            rows.append({
                "date": d["day"],
                "open": float(d["open"]),
                "close": float(d["close"]),
                "high": float(d["high"]),
                "low": float(d["low"]),
                "volume": float(d["volume"]),
            })
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"  [ak_data] K-line error for {code}: {e}")
        return None


# ====== Technical Indicators ======

def compute_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    delta = prices.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]


def compute_ma(prices, period):
    if len(prices) < period:
        return None
    return prices.rolling(period).mean().iloc[-1]


def compute_metrics(kl_df):
    """
    从K线DataFrame计算技术指标。

    返回 dict: latestPrice, latestDate, ret5d, ret10d, ret20d,
               maxDD, avgAmp, avgVol, volRatio, rsi14,
               ma5, ma10, ma20, range10d
    """
    if kl_df is None or len(kl_df) < 5:
        return None

    prices = kl_df["close"]
    latest = kl_df.iloc[-1]

    ret5d = (prices.iloc[-1] - prices.iloc[-6]) / prices.iloc[-6] if len(prices) >= 6 else 0
    ret10d = (prices.iloc[-1] - prices.iloc[-11]) / prices.iloc[-11] if len(prices) >= 11 else 0
    ret20d = (prices.iloc[-1] - prices.iloc[0]) / prices.iloc[0] if len(prices) >= 2 else 0

    peak = prices.iloc[0]
    max_dd = 0
    for p in prices:
        if p > peak:
            peak = p
        dd = (peak - p) / peak
        if dd > max_dd:
            max_dd = dd

    last20 = kl_df.iloc[-20:]
    amps = []
    for i in range(len(last20)):
        prev_close = last20.iloc[i - 1]["close"] if i > 0 else last20.iloc[i]["open"]
        amp = abs(last20.iloc[i]["high"] - last20.iloc[i]["low"]) / prev_close * 100
        amps.append(amp)
    avg_amp = sum(amps) / len(amps)
    avg_vol = last20["volume"].mean()

    last5_vol = kl_df.iloc[-5:]["volume"].mean()
    vol_ratio = last5_vol / avg_vol if avg_vol > 0 else 1

    rsi = compute_rsi(prices)
    ma5 = compute_ma(prices, 5)
    ma10 = compute_ma(prices, 10)
    ma20 = compute_ma(prices, 20)

    last10 = kl_df.iloc[-10:]
    high10 = last10["high"].max()
    low10 = last10["low"].min()
    range10d_pos = (latest["close"] - low10) / (high10 - low10) * 100 if high10 > low10 else 50

    return {
        "latestPrice": latest["close"],
        "latestDate": latest["date"],
        "ret5d": round(ret5d * 100, 1),
        "ret10d": round(ret10d * 100, 1),
        "ret20d": round(ret20d * 100, 1),
        "maxDD": round(max_dd * 100, 1),
        "avgAmp": round(avg_amp, 1),
        "avgVol": round(avg_vol),
        "volRatio": round(vol_ratio, 2),
        "rsi14": round(rsi, 1) if rsi is not None else None,
        "ma5": round(ma5, 2) if ma5 is not None else None,
        "ma10": round(ma10, 2) if ma10 is not None else None,
        "ma20": round(ma20, 2) if ma20 is not None else None,
        "range10d": round(range10d_pos, 1),
    }


# ====== RESSET Fundamentals ======

RESSET_PATH = os.getenv("FUNDAMENTALS_CSV", "")


def load_resset_fundamentals():
    """从RESSET CSV加载基本面（PE, ROE, EPS）。"""
    if not RESSET_PATH or not os.path.exists(RESSET_PATH):
        print("  [ak_data] Optional fundamentals unavailable. Set FUNDAMENTALS_CSV to a locally licensed CSV.")
        return {}

    df = pd.read_csv(RESSET_PATH)
    fundamentals = {}
    for code, group in df.groupby("Stkcd"):
        code_str = str(code).zfill(6)
        latest = group.sort_values("Date").iloc[-1]
        fundamentals[code_str] = {
            "pe": _safe_float(latest.get("PE")),
            "roe": _safe_float(latest.get("ROE")),
            "eps": _safe_float(latest.get("EPS")),
        }
    return fundamentals


def merge_fundamentals(price_df):
    """将RESSET基本面数据合并到价格DataFrame中（按code匹配）。"""
    fund = load_resset_fundamentals()
    if not fund:
        return price_df

    pe_list, roe_list, eps_list = [], [], []
    for _, row in price_df.iterrows():
        code = str(row["code"]).zfill(6)
        f = fund.get(code, {})
        pe_list.append(f.get("pe"))
        roe_list.append(f.get("roe"))
        eps_list.append(f.get("eps"))

    price_df["pe_resset"] = pe_list
    price_df["roe"] = roe_list
    price_df["eps"] = eps_list
    return price_df


# ====== Convenience ======

def is_market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    hour = now.hour + now.minute / 60
    return (9.5 <= hour <= 11.5) or (13.0 <= hour <= 15.0)


if __name__ == "__main__":
    print("=== Testing ak_data.py ===\n")

    # 1. Fast spot (Sina)
    print("1. Spot prices (4 stocks, Sina fast):")
    df = get_spot_prices(["600377", "000100", "601138", "000333"])
    if not df.empty:
        print(df[["code", "name", "price", "changePct", "open", "high", "low"]].to_string(index=False))
    else:
        print("   (no data - market closed)")

    # 2. K-line
    print("\n2. K-line (000100, last 5 days):")
    kl = get_daily_kline("000100", 30)
    if kl is not None:
        print(kl.tail(5).to_string(index=False))
        m = compute_metrics(kl)
        if m:
            print(f"\n3. Metrics: RSI={m['rsi14']}, MA5={m['ma5']}, MA10={m['ma10']}, "
                  f"MA20={m['ma20']}, range10d={m['range10d']}%")

    # 4. RESSET
    print("\n4. RESSET fundamentals (first 3):")
    fund = load_resset_fundamentals()
    for i, (code, f) in enumerate(fund.items()):
        if i >= 3:
            break
        print(f"   {code}: PE={f['pe']}, ROE={f['roe']}, EPS={f['eps']}")

    print(f"\n5. Market open: {is_market_open()}")
    print("\n=== Done ===")
