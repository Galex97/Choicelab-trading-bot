"""
行情数据获取模块 - 基于 akshare 的A股市场数据

功能：
- 涨停板数据
- 热门股票（成交量/换手率排名）
- 板块热度
- 实时行情
- 历史K线
- 龙虎榜数据
- 数据缓存机制

所有函数均包含异常处理，出错时返回空数据而非崩溃。
"""

import time
from datetime import datetime, timedelta
from typing import Optional, Any
from functools import lru_cache

import pandas as pd

from utils.logger import get_logger

logger = get_logger("data_fetcher")

# ============================================================
# 缓存管理
# ============================================================

# 简易缓存：{key: (timestamp, data)}
_cache: dict[str, tuple[float, Any]] = {}  # Bug15修复：any→Any

# 默认缓存有效期（秒）
DEFAULT_CACHE_TTL = 60  # 1分钟


def _get_cached(key: str, ttl: int = DEFAULT_CACHE_TTL) -> Optional[Any]:  # Bug15修复：any→Any
    """
    获取缓存数据

    Args:
        key: 缓存键
        ttl: 缓存有效期（秒）

    Returns:
        缓存的数据，过期或不存在返回 None
    """
    if key in _cache:
        timestamp, data = _cache[key]
        if time.time() - timestamp < ttl:
            logger.debug(f"缓存命中: {key}")
            return data
    return None


def _set_cache(key: str, data: any) -> None:
    """
    设置缓存数据

    Args:
        key: 缓存键
        data: 要缓存的数据
    """
    _cache[key] = (time.time(), data)


def clear_cache() -> None:
    """清空所有缓存"""
    _cache.clear()
    logger.info("数据缓存已清空")


# ============================================================
# 热门股票
# ============================================================

def _get_exchange_prefix(code: str) -> str:
    """根据股票代码返回新浪接口所需的市场前缀 (sh/sz/bj)

    注意：必须先判断 "92" 再判断 "9"，否则 92xxxx 北交所股会被误判为沪市。
    """
    code = str(code).strip()
    if code.startswith("92"):               # 北交所 92xxxx 先判断，优先于 "9"
        return "bj"
    elif code.startswith(("6", "9")):       # 沪市
        return "sh"
    elif code.startswith(("0", "1", "2", "3")):  # 深市
        return "sz"
    elif code.startswith(("4", "8")):       # 北交所 4/8 开头
        return "bj"
    return "sh"


def _is_bj_stock(code: str) -> bool:
    """判断是否为北交所股票"""
    return _get_exchange_prefix(code) == "bj"


def get_hot_stocks(top_n: int = 20) -> pd.DataFrame:
    """
    获取今日热门股票（使用涨停板数据作为主要来源）

    新浪/东财全市场实时接口容易被限流返回 HTML。
    涨停板中的股票就是最热门的动量标的，直接使用即可。

    Args:
        top_n: 返回前N只股票

    Returns:
        DataFrame，包含列：code, name, price, change_pct, amount, continuous_count
    """
    cache_key = f"hot_stocks_{top_n}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    try:
        logger.info("获取热门股票数据...")

        # 主要数据源：涨停板（最稳定的接口，且贵为直接比赛相关）
        zt_df = get_limit_up_stocks()

        if not zt_df.empty:
            # 涨停板数据已有标准化列名
            result = zt_df.copy()

            # 构建统一格式的输出
            out = pd.DataFrame()
            out["code"]   = result.get("code",   result.get("代码", pd.Series()))
            out["name"]   = result.get("name",   result.get("名称", pd.Series()))
            out["price"]  = pd.to_numeric(result.get("price", result.get("最新价", 0)), errors="coerce").fillna(0)
            out["change_pct"] = 10.0   # 涨停股超过+9.9%
            out["amount"] = pd.to_numeric(result.get("amount", result.get("成交额", 0)), errors="coerce").fillna(0)
            out["continuous_count"] = pd.to_numeric(
                result.get("continuous_count", result.get("连板数", 1)), errors="coerce"
            ).fillna(1)

            # 按连板数降序（连板却强）
            out = out.sort_values("continuous_count", ascending=False)
            out = out.head(top_n).reset_index(drop=True)

            _set_cache(cache_key, out)
            logger.info(f"获取到 {len(out)} 只热门股票（来源：涨停板）")
            return out

        logger.warning("涨停板数据为空")
        return pd.DataFrame()

    except Exception as e:
        logger.error(f"获取热门股票失败: {e}")
        return pd.DataFrame()



# ============================================================
# 涨停股票
# ============================================================

def get_limit_up_stocks() -> pd.DataFrame:
    """
    获取今日涨停板股票

    Returns:
        DataFrame，包含涨停股票信息
    """
    cache_key = "limit_up"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    try:
        import akshare as ak

        logger.info("获取涨停板数据...")

        # 获取涨停板数据
        today_str = datetime.now().strftime("%Y%m%d")
        df = ak.stock_zt_pool_em(date=today_str)

        if df is None or df.empty:
            logger.info("今日暂无涨停数据（可能非交易时间）")
            return pd.DataFrame()

        # 标准化列名
        columns_mapping = {
            "代码": "code",
            "名称": "name",
            "涨停价": "limit_price",
            "最新价": "price",
            "成交额": "amount",
            "流通市值": "float_market_cap",
            "封板资金": "seal_amount",
            "首次封板时间": "first_seal_time",
            "连板数": "continuous_count",
            "涨停统计": "limit_stats",
            "所属行业": "sector",   # ← 保留行业列，供板块热度分析使用
        }

        available_cols = {k: v for k, v in columns_mapping.items() if k in df.columns}
        if available_cols:
            df = df[list(available_cols.keys())].rename(columns=available_cols)

        _set_cache(cache_key, df)
        logger.info(f"获取到 {len(df)} 只涨停股票")
        return df

    except Exception as e:
        logger.error(f"获取涨停板数据失败: {e}")
        return pd.DataFrame()


# ============================================================
# 板块热度
# ============================================================

def get_sector_hot(top_n: int = 15) -> pd.DataFrame:
    """
    获取今日最热行业板块（从涨停板数据的"所属行业"字段统计）

    东财实时板块接口不稳定，改用涨停板数据中的行业字段统计热度。

    Args:
        top_n: 返回前N个板块

    Returns:
        DataFrame，包含 sector_name, stock_count, leading_stock 等
    """
    cache_key = f"sector_hot_{top_n}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    try:
        logger.info("从涨停板数据统计板块热度...")

        # 从涨停板数据提取行业分布（该接口稳定可用）
        zt_df = get_limit_up_stocks()
        if zt_df.empty:
            logger.warning("涨停板数据为空，无法统计板块热度")
            return pd.DataFrame()

        # 找行业列（可能叫"sector"或"所属行业"）
        industry_col = None
        for col in ["sector", "所属行业", "行业"]:
            if col in zt_df.columns:
                industry_col = col
                break

        if industry_col is None:
            logger.warning("涨停板数据中无行业列")
            return pd.DataFrame()

        # 按行业统计涨停数量
        sector_counts = zt_df[industry_col].value_counts().head(top_n)
        result = pd.DataFrame({
            "sector_name": sector_counts.index,
            "stock_count": sector_counts.values,
        }).reset_index(drop=True)

        # 附上每个板块的代表股（连板最多的）
        if "name" in zt_df.columns and "continuous_count" in zt_df.columns:
            for idx, row in result.iterrows():
                sector_stocks = zt_df[zt_df[industry_col] == row["sector_name"]]
                if not sector_stocks.empty:
                    top_stock = sector_stocks.sort_values(
                        "continuous_count", ascending=False
                    ).iloc[0]
                    result.at[idx, "leading_stock"] = top_stock.get("name", "")

        _set_cache(cache_key, result)
        logger.info(f"统计到 {len(result)} 个热门板块")
        return result

    except Exception as e:
        logger.error(f"获取板块热度数据失败: {e}")
        return pd.DataFrame()


# ============================================================
# 实时行情
# ============================================================

def get_stock_realtime(stock_code: str) -> dict:
    """
    获取单只股票实时行情

    Args:
        stock_code: 股票代码（如 "000001"、"600519"）

    Returns:
        行情字典：
        {
            "code": "股票代码",
            "name": "名称",
            "price": 最新价,
            "change_pct": 涨跌幅,
            "open": 开盘价,
            "high": 最高价,
            "low": 最低价,
            "volume": 成交量,
            "amount": 成交额,
            "turnover_rate": 换手率
        }
    """
    cache_key = f"realtime_{stock_code}"
    cached = _get_cached(cache_key, ttl=10)  # 实时数据缓存10秒
    if cached is not None:
        return cached

    try:
        import requests

        logger.debug(f"获取实时行情: {stock_code}")

        prefix = _get_exchange_prefix(stock_code)
        sina_symbol = f"{prefix}{stock_code}"

        # 使用新浪实时 Tick 接口获取盘中高精度价格
        url = f"https://hq.sinajs.cn/list={sina_symbol}"
        headers = {"Referer": "https://finance.sina.com.cn"}
        
        try:
            resp = requests.get(url, headers=headers, timeout=5)
            text = resp.text
        except Exception as e:
            logger.warning(f"实时价格请求失败 [{stock_code}]: {e}")
            return {}

        if not text or "=\"" not in text:
            return {}

        # 解析新浪数据格式: var hq_str_sh600519="名字,开盘,昨收,现价,最高,最低,买一,卖一,成交量,成交额,..."
        data_str = text.split("=\"")[1].split("\";")[0]
        if not data_str:
            return {}

        fields = data_str.split(",")
        if len(fields) < 32:
            return {}

        def safe_float(val):
            try:
                return float(val) if val else 0.0
            except (ValueError, TypeError):
                return 0.0

        current_price = safe_float(fields[3])
        pre_close = safe_float(fields[2])
        change_pct = ((current_price - pre_close) / pre_close * 100) if pre_close > 0 else 0.0

        result = {
            "code": stock_code,
            "name": fields[0],
            "price": current_price,
            "change_pct": change_pct,
            "open": safe_float(fields[1]),
            "high": safe_float(fields[4]),
            "low": safe_float(fields[5]),
            "volume": safe_float(fields[8]),
            "amount": safe_float(fields[9]),
            "turnover_rate": 0.0, # 新浪接口无直接换手率
        }

        _set_cache(cache_key, result)
        return result

    except Exception as e:
        logger.error(f"获取实时行情失败 [{stock_code}]: {e}")
        return {}



# ============================================================
# 历史K线
# ============================================================

def get_stock_history(
    stock_code: str,
    days: int = 30,
    adjust: str = "qfq",
) -> pd.DataFrame:
    """
    获取股票历史K线数据

    Args:
        stock_code: 股票代码
        days: 获取最近N天的数据
        adjust: 复权方式 ("qfq"前复权 / "hfq"后复权 / ""不复权)

    Returns:
        DataFrame，包含列：日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额
    """
    cache_key = f"history_{stock_code}_{days}_{adjust}"
    cached = _get_cached(cache_key, ttl=300)  # 历史数据缓存5分钟
    if cached is not None:
        return cached

    try:
        import akshare as ak

        logger.debug(f"获取历史K线: {stock_code}，最近{days}天")

        # 计算起始日期
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days + 30)).strftime("%Y%m%d")
        # 多请求30天的数据以确保有足够交易日

        # 判断是否为北交所股票
        prefix = _get_exchange_prefix(stock_code)

        if prefix == "bj":
            # 北交所股票：stock_zh_a_daily 不支持，改用腾讯K线接口
            logger.debug(f"{stock_code} 为北交所股票，使用腾讯K线源")
            try:
                df = ak.stock_zh_a_hist_tx(
                    symbol=f"bj{stock_code}",
                    # 腾讯接口不需要日期参数
                )
            except Exception:
                logger.warning(f"北交所K线获取失败 [{stock_code}]，跳过")
                return pd.DataFrame()
        else:
            # 沪深股票：使用新浪K线接口
            sina_symbol = f"{prefix}{stock_code}"
            df = ak.stock_zh_a_daily(
                symbol=sina_symbol,
                adjust=adjust if adjust else "qfq",
            )

        if df is None or df.empty:
            logger.warning(f"历史数据为空: {stock_code}")
            return pd.DataFrame()

        # date 可能是 index 而非 column，统一 reset_index 处理
        if df.index.name in ("date", "Date") and "date" not in df.columns:
            df = df.reset_index()
            df = df.rename(columns={df.columns[0]: "date"})

        # 统一列名映射
        columns_mapping = {
            "date": "date",
            "open": "open",
            "close": "close",
            "high": "high",
            "low": "low",
            "volume": "volume",
            "amount": "amount",
        }
        available_cols = {k: v for k, v in columns_mapping.items() if k in df.columns}
        if available_cols:
            df = df[list(available_cols.keys())].rename(columns=available_cols)

        # date 列转 string，确保格式统一
        if "date" in df.columns:
            df["date"] = df["date"].astype(str)

        # 只取最近N天
        result = df.tail(days).reset_index(drop=True)
        _set_cache(cache_key, result)

        logger.debug(f"获取到 {len(result)} 条历史记录: {stock_code}")
        return result

    except Exception as e:
        logger.error(f"获取历史K线失败 [{stock_code}]: {e}")
        return pd.DataFrame()


# ============================================================
# 龙虎榜
# ============================================================

def get_dragon_tiger_list() -> pd.DataFrame:
    """
    获取今日龙虎榜数据

    龙虎榜展示当日异常波动个股的主力买卖详情。

    Returns:
        DataFrame，包含龙虎榜个股信息
    """
    cache_key = "dragon_tiger"
    cached = _get_cached(cache_key, ttl=300)
    if cached is not None:
        return cached

    try:
        import akshare as ak

        logger.info("获取龙虎榜数据...")

        # 获取最新龙虎榜数据
        today_str = datetime.now().strftime("%Y%m%d")

        # 尝试获取当日数据，若无则获取最近交易日
        try:
            df = ak.stock_lhb_detail_em(
                start_date=today_str,
                end_date=today_str,
            )
        except Exception:
            # 如果当日无数据（如非交易日），获取最近的
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
            df = ak.stock_lhb_detail_em(
                start_date=yesterday,
                end_date=yesterday,
            )

        if df is None or df.empty:
            logger.info("龙虎榜数据为空（可能非交易日或尚未发布）")
            return pd.DataFrame()

        _set_cache(cache_key, df)
        logger.info(f"获取到 {len(df)} 条龙虎榜记录")
        return df

    except Exception as e:
        logger.error(f"获取龙虎榜数据失败: {e}")
        return pd.DataFrame()


# ============================================================
# 综合市场概览
# ============================================================

def get_market_overview() -> dict:
    """
    获取综合市场概览（用于AI分析）

    Returns:
        字典，包含各维度市场数据摘要
    """
    overview = {
        "hot_stocks": [],
        "limit_up_count": 0,
        "limit_up_stocks": [],
        "hot_sectors": [],
        "market_sentiment": "中性",
    }

    try:
        # 热门股票
        hot_df = get_hot_stocks(top_n=10)
        if not hot_df.empty:
            overview["hot_stocks"] = hot_df.to_dict("records")

        # 涨停板
        zt_df = get_limit_up_stocks()
        if not zt_df.empty:
            overview["limit_up_count"] = len(zt_df)
            overview["limit_up_stocks"] = zt_df.head(10).to_dict("records")

        # 热门板块
        sector_df = get_sector_hot(top_n=10)
        if not sector_df.empty:
            overview["hot_sectors"] = sector_df.to_dict("records")

        # 简单判断市场情绪
        if overview["limit_up_count"] > 50:
            overview["market_sentiment"] = "极度亢奋"
        elif overview["limit_up_count"] > 30:
            overview["market_sentiment"] = "乐观"
        elif overview["limit_up_count"] > 15:
            overview["market_sentiment"] = "偏多"
        elif overview["limit_up_count"] > 5:
            overview["market_sentiment"] = "中性"
        else:
            overview["market_sentiment"] = "偏空"

        logger.info(
            f"市场概览: 涨停{overview['limit_up_count']}只, "
            f"情绪={overview['market_sentiment']}"
        )
        return overview

    except Exception as e:
        logger.error(f"获取市场概览失败: {e}")
        return overview


# ============================================================
# 模块自测
# ============================================================
if __name__ == "__main__":
    from rich.table import Table
    from utils.logger import console

    console.print("[header]📊 数据获取模块测试[/header]\n")

    # 测试热门股票
    console.print("[cyan]1. 热门股票 Top 5:[/cyan]")
    hot = get_hot_stocks(5)
    if not hot.empty:
        console.print(hot.to_string(index=False))
    else:
        console.print("[dim]无数据[/dim]")

    console.print()

    # 测试涨停板
    console.print("[cyan]2. 涨停板:[/cyan]")
    zt = get_limit_up_stocks()
    console.print(f"涨停数量: {len(zt)}")

    console.print()

    # 测试板块热度
    console.print("[cyan]3. 热门板块 Top 5:[/cyan]")
    sectors = get_sector_hot(5)
    if not sectors.empty:
        console.print(sectors.to_string(index=False))

    console.print()

    # 测试市场概览
    console.print("[cyan]4. 市场概览:[/cyan]")
    overview = get_market_overview()
    console.print(f"市场情绪: {overview['market_sentiment']}")
    console.print(f"涨停数量: {overview['limit_up_count']}")

    console.print("\n[success]✅ 数据获取模块测试完成[/success]")
