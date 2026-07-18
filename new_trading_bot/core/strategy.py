"""
量化策略引擎 - 多策略信号生成与合并

策略列表：
- MomentumStrategy: 动量策略（基于近期价格动量+成交量）
- LeaderStockStrategy: 龙头股策略（识别热门板块中的领涨标的）
- BreakoutStrategy: 突破策略（均线交叉 / 放量突破）
- StrategyManager: 策略管理器（组合多策略信号）

每个策略的 generate_signals() 返回格式：
[
    {
        "stock_code": "000001",
        "stock_name": "平安银行",
        "action": "buy" / "sell" / "hold",
        "confidence": 0.0 ~ 1.0,
        "reason": "信号说明"
    }
]
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from core.data_fetcher import (
    get_hot_stocks,
    get_limit_up_stocks,
    get_sector_hot,
    get_stock_realtime,
    get_stock_history,
)
from utils.logger import get_logger

logger = get_logger("strategy")


# ============================================================
# 信号数据结构
# ============================================================

@dataclass
class Signal:
    """交易信号"""
    stock_code: str          # 股票代码
    stock_name: str = ""     # 股票名称
    action: str = "hold"     # 操作：buy / sell / hold
    confidence: float = 0.0  # 置信度 0~1
    reason: str = ""         # 信号原因
    strategy: str = ""       # 来源策略名称
    priority: int = 0        # 优先级（数值越大越优先）

    def to_dict(self) -> dict:
        """转为字典"""
        return {
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "action": self.action,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "strategy": self.strategy,
            "priority": self.priority,
        }


# ============================================================
# 策略基类
# ============================================================

class BaseStrategy(ABC):
    """策略基类，所有策略需继承此类"""

    def __init__(self, name: str = "base"):
        self.name = name
        self.logger = get_logger(f"strategy.{name}")

    @abstractmethod
    def generate_signals(
        self,
        positions: Optional[list[dict]] = None,
        account_info: Optional[dict] = None,
    ) -> list[Signal]:
        """
        生成交易信号

        Args:
            positions: 当前持仓列表
            account_info: 账户信息

        Returns:
            信号列表
        """
        pass


# ============================================================
# 动量策略
# ============================================================

class MomentumStrategy(BaseStrategy):
    """
    动量策略

    核心逻辑：
    - 选取近期涨幅靠前、成交量放大的股票
    - 短期均线向上且价格在均线上方 → 买入信号
    - 价格跌破短期均线且成交量萎缩 → 卖出信号

    参数：
    - lookback_days: 回看天数
    - min_momentum: 最低动量阈值（涨幅百分比）
    - volume_ratio: 量比阈值（相对均量的倍数）
    """

    def __init__(
        self,
        lookback_days: int = 5,
        min_momentum: float = 3.0,
        volume_ratio: float = 1.5,
        allow_chinext: bool = False,
        allow_star_market: bool = False,
    ):
        super().__init__("momentum")
        self.lookback_days = lookback_days
        self.min_momentum = min_momentum
        self.volume_ratio = volume_ratio
        # Bug5修复：从配置读取板块过滤，替代硬编码
        self.allow_chinext = allow_chinext      # 是否允许创业板（30xxxx）
        self.allow_star_market = allow_star_market  # 是否允许科创板（68xxxx）

    def generate_signals(
        self,
        positions: Optional[list[dict]] = None,
        account_info: Optional[dict] = None,
    ) -> list[Signal]:
        """
        基于动量指标生成买入/卖出信号

        Args:
            positions: 当前持仓
            account_info: 账户信息

        Returns:
            信号列表
        """
        signals = []
        positions = positions or []

        try:
            self.logger.info("动量策略开始分析...")

            # ----- 分析候选买入标的 -----
            hot_stocks = get_hot_stocks(top_n=30)
            if hot_stocks.empty:
                self.logger.warning("热门股票数据为空，跳过买入分析")
            else:
                for _, stock in hot_stocks.iterrows():
                    code = stock.get("code", "")
                    name = stock.get("name", "")

                    if not code:
                        continue

                    # Bug5修复：根据配置决定是否跳过创业板/科创板，不再硬编码过滤
                    if code.startswith("30") and not self.allow_chinext:
                        continue
                    if code.startswith("68") and not self.allow_star_market:
                        continue

                    # 获取历史K线
                    hist = get_stock_history(code, days=self.lookback_days + 10)
                    if hist.empty or len(hist) < self.lookback_days:
                        continue

                    signal = self._analyze_momentum(code, name, hist)
                    if signal and signal.action == "buy":
                        signals.append(signal)

            # ----- 分析持仓是否需要卖出 -----
            for pos in positions:
                code = pos.get("code", "")
                name = pos.get("name", "")

                if not code:
                    continue

                hist = get_stock_history(code, days=self.lookback_days + 10)
                if hist.empty:
                    continue

                signal = self._check_sell_signal(code, name, hist, pos)
                if signal:
                    signals.append(signal)

            self.logger.info(f"动量策略生成 {len(signals)} 个信号")

        except Exception as e:
            self.logger.error(f"动量策略执行出错: {e}")

        return signals

    def _analyze_momentum(
        self, code: str, name: str, hist: pd.DataFrame
    ) -> Optional[Signal]:
        """
        分析单只股票的动量信号

        Args:
            code: 股票代码
            name: 股票名称
            hist: 历史K线数据

        Returns:
            买入信号或 None
        """
        try:
            if "close" not in hist.columns or "volume" not in hist.columns:
                return None

            closes = hist["close"].astype(float).values
            volumes = hist["volume"].astype(float).values

            if len(closes) < self.lookback_days:
                return None

            # 计算近期涨幅（动量）
            recent_return = (closes[-1] - closes[-self.lookback_days]) / closes[-self.lookback_days] * 100

            # 计算量比（近期成交量 / 前期平均成交量）
            recent_vol = np.mean(volumes[-3:])
            avg_vol = np.mean(volumes[:-3]) if len(volumes) > 3 else recent_vol
            vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0

            # 计算5日均线方向
            ma5 = np.mean(closes[-5:]) if len(closes) >= 5 else closes[-1]
            ma5_prev = np.mean(closes[-6:-1]) if len(closes) >= 6 else ma5
            ma_trend_up = ma5 > ma5_prev

            # 买入条件判断
            if (
                recent_return >= self.min_momentum
                and vol_ratio >= self.volume_ratio
                and ma_trend_up
                and closes[-1] > ma5
            ):
                confidence = min(0.9, 0.3 + recent_return / 20 + (vol_ratio - 1) * 0.2)
                return Signal(
                    stock_code=code,
                    stock_name=name,
                    action="buy",
                    confidence=confidence,
                    reason=(
                        f"动量信号: {self.lookback_days}日涨幅{recent_return:.1f}%, "
                        f"量比{vol_ratio:.1f}, 均线多头"
                    ),
                    strategy=self.name,
                    priority=int(confidence * 100),
                )

        except Exception as e:
            self.logger.debug(f"分析 {code} 动量时出错: {e}")

        return None

    def _check_sell_signal(
        self,
        code: str,
        name: str,
        hist: pd.DataFrame,
        position: dict,
    ) -> Optional[Signal]:
        """
        检查持仓股票的卖出信号

        Args:
            code: 股票代码
            name: 股票名称
            hist: 历史K线
            position: 持仓信息

        Returns:
            卖出信号或 None
        """
        try:
            if "close" not in hist.columns:
                return None

            closes = hist["close"].astype(float).values
            current_price = closes[-1]

            # 计算成本盈亏
            cost_price = position.get("cost_price", current_price)
            profit_pct = (current_price - cost_price) / cost_price * 100 if cost_price > 0 else 0

            # 计算5日均线
            ma5 = np.mean(closes[-5:]) if len(closes) >= 5 else current_price

            # 卖出条件1：跌破5日均线
            if current_price < ma5 * 0.98:
                return Signal(
                    stock_code=code,
                    stock_name=name,
                    action="sell",
                    confidence=0.7,
                    reason=f"跌破5日均线，当前盈亏{profit_pct:.1f}%",
                    strategy=self.name,
                    priority=70,
                )

            # 卖出条件2：动量衰减（连续3日下跌）
            if len(closes) >= 3 and all(closes[-i] < closes[-i-1] for i in range(1, 4)):
                return Signal(
                    stock_code=code,
                    stock_name=name,
                    action="sell",
                    confidence=0.6,
                    reason=f"连续3日下跌，动量衰减，盈亏{profit_pct:.1f}%",
                    strategy=self.name,
                    priority=60,
                )

        except Exception as e:
            self.logger.debug(f"检查 {code} 卖出信号时出错: {e}")

        return None


# ============================================================
# 龙头股策略
# ============================================================

class LeaderStockStrategy(BaseStrategy):
    """
    龙头股策略

    核心逻辑：
    - 识别当日最热板块
    - 从热门板块中筛选龙头股（涨幅领先 + 成交额最大）
    - 优先选择有连板记录的个股
    """

    def __init__(self, max_candidates: int = 5, allow_chinext: bool = False, allow_star_market: bool = False):
        super().__init__("leader_stock")
        self.max_candidates = max_candidates
        # Bug5修复：从配置读取板块过滤参数
        self.allow_chinext = allow_chinext
        self.allow_star_market = allow_star_market

    def generate_signals(
        self,
        positions: Optional[list[dict]] = None,
        account_info: Optional[dict] = None,
    ) -> list[Signal]:
        """
        基于板块龙头逻辑生成信号

        Args:
            positions: 当前持仓
            account_info: 账户信息

        Returns:
            信号列表
        """
        signals = []

        try:
            self.logger.info("龙头股策略开始分析...")

            # 获取热门板块
            sectors = get_sector_hot(top_n=5)
            if sectors.empty:
                self.logger.warning("板块数据为空，跳过龙头股分析")
                return signals

            # 获取涨停板数据（识别连板股）
            limit_up = get_limit_up_stocks()
            limit_up_codes = set()
            if not limit_up.empty and "code" in limit_up.columns:
                limit_up_codes = set(limit_up["code"].tolist())

            # 获取热门股票并尝试匹配板块龙头
            hot_stocks = get_hot_stocks(top_n=50)
            if hot_stocks.empty:
                return signals

            candidates = []

            for _, stock in hot_stocks.iterrows():
                code = stock.get("code", "")
                name = stock.get("name", "")
                change_pct = float(stock.get("change_pct", 0))
                amount = float(stock.get("amount", 0))

                if not code:
                    continue

                # Bug5修复：根据配置决定是否跳过创业板/科创板
                if code.startswith("30") and not self.allow_chinext:
                    continue
                if code.startswith("68") and not self.allow_star_market:
                    continue

                # 龙头特征评分
                score = 0.0
                reasons = []

                # 特征1：当日涨幅高
                if change_pct >= 5:
                    score += 0.3
                    reasons.append(f"涨幅{change_pct:.1f}%")
                elif change_pct >= 3:
                    score += 0.15

                # 特征2：成交额大（资金关注度高）
                if amount > 5e8:  # 5亿以上
                    score += 0.2
                    reasons.append("成交额突出")
                elif amount > 2e8:
                    score += 0.1

                # 特征3：涨停或曾涨停（强势特征）
                if code in limit_up_codes:
                    score += 0.3
                    reasons.append("涨停/曾涨停")

                # 特征4：不是高位股（简单判断：涨幅不超过9.9%说明未涨停，也不是一字板）
                if 3 <= change_pct <= 9.5:
                    score += 0.1
                    reasons.append("涨幅合理区间")

                if score >= 0.4:
                    candidates.append({
                        "code": code,
                        "name": name,
                        "score": score,
                        "reasons": reasons,
                        "change_pct": change_pct,
                    })

            # 按得分排序取前N个
            candidates.sort(key=lambda x: x["score"], reverse=True)

            for cand in candidates[:self.max_candidates]:
                signals.append(Signal(
                    stock_code=cand["code"],
                    stock_name=cand["name"],
                    action="buy",
                    confidence=min(0.85, cand["score"]),
                    reason=f"龙头股信号: {', '.join(cand['reasons'])}",
                    strategy=self.name,
                    priority=int(cand["score"] * 100),
                ))

            self.logger.info(f"龙头股策略生成 {len(signals)} 个信号")

        except Exception as e:
            self.logger.error(f"龙头股策略执行出错: {e}")

        return signals


# ============================================================
# 突破策略
# ============================================================

class BreakoutStrategy(BaseStrategy):
    """
    突破策略

    核心逻辑：
    - MA5/MA10 金叉（短期均线上穿长期均线）→ 买入
    - MA5/MA10 死叉 → 卖出
    - 价格放量突破近期高点 → 加强买入信号
    """

    def __init__(
        self,
        short_ma: int = 5,
        long_ma: int = 10,
        breakout_days: int = 20,
        allow_chinext: bool = False,
        allow_star_market: bool = False,
    ):
        super().__init__("breakout")
        self.short_ma = short_ma
        self.long_ma = long_ma
        self.breakout_days = breakout_days
        # Bug5修复：从配置读取板块过滤参数
        self.allow_chinext = allow_chinext
        self.allow_star_market = allow_star_market

    def generate_signals(
        self,
        positions: Optional[list[dict]] = None,
        account_info: Optional[dict] = None,
    ) -> list[Signal]:
        """
        基于均线交叉和突破生成信号

        Args:
            positions: 当前持仓
            account_info: 账户信息

        Returns:
            信号列表
        """
        signals = []
        positions = positions or []

        try:
            self.logger.info("突破策略开始分析...")

            # 分析热门股票的突破信号
            hot_stocks = get_hot_stocks(top_n=30)

            if not hot_stocks.empty:
                for _, stock in hot_stocks.iterrows():
                    code = stock.get("code", "")
                    name = stock.get("name", "")

                    if not code:
                        continue
                    # Bug5修复：根据配置决定是否跳过创业板/科创板
                    if code.startswith("30") and not self.allow_chinext:
                        continue
                    if code.startswith("68") and not self.allow_star_market:
                        continue

                    hist = get_stock_history(code, days=self.breakout_days + 10)
                    if hist.empty or len(hist) < self.long_ma + 2:
                        continue

                    signal = self._check_breakout(code, name, hist)
                    if signal:
                        signals.append(signal)

            # 检查持仓是否触发均线死叉卖出
            for pos in positions:
                code = pos.get("code", "")
                name = pos.get("name", "")

                if not code:
                    continue

                hist = get_stock_history(code, days=self.breakout_days + 10)
                if hist.empty or len(hist) < self.long_ma + 2:
                    continue

                signal = self._check_death_cross(code, name, hist, pos)
                if signal:
                    signals.append(signal)

            self.logger.info(f"突破策略生成 {len(signals)} 个信号")

        except Exception as e:
            self.logger.error(f"突破策略执行出错: {e}")

        return signals

    def _check_breakout(
        self, code: str, name: str, hist: pd.DataFrame
    ) -> Optional[Signal]:
        """
        检查金叉/突破买入信号

        Args:
            code: 股票代码
            name: 名称
            hist: 历史K线

        Returns:
            买入信号或 None
        """
        try:
            closes = hist["close"].astype(float).values
            volumes = hist["volume"].astype(float).values

            if len(closes) < self.long_ma + 2:
                return None

            # 计算均线
            ma_short = self._calc_ma(closes, self.short_ma)
            ma_long = self._calc_ma(closes, self.long_ma)

            if ma_short is None or ma_long is None:
                return None

            # 金叉判断：短期均线从下方穿越长期均线
            # 今日短期 > 长期，昨日短期 <= 长期
            today_cross = ma_short[-1] > ma_long[-1]
            yesterday_below = ma_short[-2] <= ma_long[-2]

            if today_cross and yesterday_below:
                # 确认放量
                vol_ratio = volumes[-1] / np.mean(volumes[-10:-1]) if len(volumes) > 10 else 1.0

                confidence = 0.5
                reasons = [f"MA{self.short_ma}/MA{self.long_ma}金叉"]

                if vol_ratio > 1.5:
                    confidence += 0.2
                    reasons.append(f"量比{vol_ratio:.1f}")

                # 检查是否突破近期高点
                recent_high = np.max(closes[-self.breakout_days:-1])
                if closes[-1] > recent_high:
                    confidence += 0.15
                    reasons.append(f"突破{self.breakout_days}日新高")

                return Signal(
                    stock_code=code,
                    stock_name=name,
                    action="buy",
                    confidence=min(0.85, confidence),
                    reason=f"突破信号: {', '.join(reasons)}",
                    strategy=self.name,
                    priority=int(confidence * 100),
                )

        except Exception as e:
            self.logger.debug(f"检查 {code} 突破信号时出错: {e}")

        return None

    def _check_death_cross(
        self,
        code: str,
        name: str,
        hist: pd.DataFrame,
        position: dict,
    ) -> Optional[Signal]:
        """
        检查均线死叉卖出信号

        Args:
            code: 股票代码
            name: 名称
            hist: 历史K线
            position: 持仓信息

        Returns:
            卖出信号或 None
        """
        try:
            closes = hist["close"].astype(float).values

            if len(closes) < self.long_ma + 2:
                return None

            ma_short = self._calc_ma(closes, self.short_ma)
            ma_long = self._calc_ma(closes, self.long_ma)

            if ma_short is None or ma_long is None:
                return None

            # 死叉判断：短期均线从上方穿越长期均线
            today_below = ma_short[-1] < ma_long[-1]
            yesterday_above = ma_short[-2] >= ma_long[-2]

            if today_below and yesterday_above:
                cost = position.get("cost_price", closes[-1])
                profit_pct = (closes[-1] - cost) / cost * 100 if cost > 0 else 0

                return Signal(
                    stock_code=code,
                    stock_name=name,
                    action="sell",
                    confidence=0.65,
                    reason=f"均线死叉(MA{self.short_ma}/MA{self.long_ma})，盈亏{profit_pct:.1f}%",
                    strategy=self.name,
                    priority=65,
                )

        except Exception as e:
            self.logger.debug(f"检查 {code} 死叉信号时出错: {e}")

        return None

    @staticmethod
    def _calc_ma(data: np.ndarray, period: int) -> Optional[np.ndarray]:
        """
        计算移动平均线

        Args:
            data: 价格序列
            period: 均线周期

        Returns:
            移动平均数组，长度不足时返回 None
        """
        if len(data) < period:
            return None
        # 使用 pandas 的滚动均值
        return pd.Series(data).rolling(window=period).mean().values


# ============================================================
# 策略管理器
# ============================================================

class StrategyManager:
    """
    策略管理器 - 组合多个策略的信号

    功能：
    - 注册并管理多个策略
    - 运行所有策略并合并信号
    - 按置信度加权去重
    - 输出最终交易建议
    """

    def __init__(
        self,
        weights: Optional[dict[str, float]] = None,
        max_buy_signals: int = 5,
    ):
        """
        初始化策略管理器

        Args:
            weights: 策略权重映射 {策略名: 权重}
            max_buy_signals: 最大买入信号数
        """
        self.strategies: list[BaseStrategy] = []
        self.weights = weights or {
            "momentum": 0.4,
            "leader_stock": 0.35,
            "breakout": 0.25,
        }
        self.max_buy_signals = max_buy_signals
        self.logger = get_logger("strategy_manager")

    def add_strategy(self, strategy: BaseStrategy) -> None:
        """
        注册策略

        Args:
            strategy: 策略实例
        """
        self.strategies.append(strategy)
        self.logger.info(f"已注册策略: {strategy.name}")

    def add_default_strategies(self, config: Optional[dict] = None) -> None:
        """注册默认策略组合，从配置读取板块过滤参数（Bug5修复）"""
        # 从 settings.yaml 读取风控配置中的板块过滤参数
        risk_cfg = (config or {}).get("risk", {})
        allow_chinext = risk_cfg.get("allow_chinext", False)
        allow_star_market = risk_cfg.get("allow_star_market", False)

        self.add_strategy(MomentumStrategy(allow_chinext=allow_chinext, allow_star_market=allow_star_market))
        self.add_strategy(LeaderStockStrategy(allow_chinext=allow_chinext, allow_star_market=allow_star_market))
        self.add_strategy(BreakoutStrategy(allow_chinext=allow_chinext, allow_star_market=allow_star_market))

    def run_all(
        self,
        positions: Optional[list[dict]] = None,
        account_info: Optional[dict] = None,
    ) -> dict:
        """
        运行所有策略并合并信号

        Args:
            positions: 当前持仓
            account_info: 账户信息

        Returns:
            合并后的信号字典：
            {
                "buy_signals": [Signal, ...],
                "sell_signals": [Signal, ...],
                "all_signals": [Signal, ...],
                "summary": "策略摘要"
            }
        """
        all_signals: list[Signal] = []

        # 运行每个策略
        for strategy in self.strategies:
            try:
                self.logger.info(f"运行策略: {strategy.name}")
                signals = strategy.generate_signals(positions, account_info)

                # 应用权重
                weight = self.weights.get(strategy.name, 0.3)
                for sig in signals:
                    sig.confidence *= weight

                all_signals.extend(signals)
                self.logger.info(f"策略 {strategy.name} 产生 {len(signals)} 个信号")

            except Exception as e:
                self.logger.error(f"策略 {strategy.name} 执行失败: {e}")

        # 合并同一股票的信号
        merged = self._merge_signals(all_signals)

        # 分离买入和卖出信号
        buy_signals = [s for s in merged if s.action == "buy"]
        sell_signals = [s for s in merged if s.action == "sell"]

        # 按置信度排序
        buy_signals.sort(key=lambda s: s.confidence, reverse=True)
        sell_signals.sort(key=lambda s: s.confidence, reverse=True)

        # 限制买入信号数量
        buy_signals = buy_signals[:self.max_buy_signals]

        result = {
            "buy_signals": buy_signals,
            "sell_signals": sell_signals,
            "all_signals": merged,
            "summary": (
                f"共分析 {len(self.strategies)} 个策略，"
                f"产生 {len(buy_signals)} 个买入信号、"
                f"{len(sell_signals)} 个卖出信号"
            ),
        }

        self.logger.info(result["summary"])
        return result

    def _merge_signals(self, signals: list[Signal]) -> list[Signal]:
        """
        合并多个策略对同一股票的信号

        合并规则：
        - 同一股票的同方向信号：合并置信度（取加权平均），合并原因
        - 同一股票的不同方向信号：以卖出优先（风控优先）

        Args:
            signals: 原始信号列表

        Returns:
            合并后的信号列表
        """
        # 按 (stock_code, action) 分组
        groups: dict[tuple[str, str], list[Signal]] = {}
        for sig in signals:
            key = (sig.stock_code, sig.action)
            if key not in groups:
                groups[key] = []
            groups[key].append(sig)

        # 合并每组信号
        merged: dict[str, Signal] = {}
        for (code, action), group in groups.items():
            # 计算合并置信度
            combined_confidence = sum(s.confidence for s in group) / len(group)
            combined_confidence = min(0.95, combined_confidence * (1 + len(group) * 0.1))

            # 合并原因
            reasons = list(set(s.reason for s in group))
            combined_reason = " | ".join(reasons[:3])  # 最多3个原因

            # 合并策略来源
            strategies = list(set(s.strategy for s in group))

            merged_signal = Signal(
                stock_code=code,
                stock_name=group[0].stock_name,
                action=action,
                confidence=combined_confidence,
                reason=combined_reason,
                strategy="+".join(strategies),
                priority=max(s.priority for s in group),
            )

            # 如果同一股票有买入和卖出信号，卖出优先
            if code in merged:
                existing = merged[code]
                if existing.action == "sell" or action == "sell":
                    # 保留卖出信号
                    if action == "sell":
                        merged[code] = merged_signal
                else:
                    # 两个都是买入，取置信度更高的
                    if combined_confidence > existing.confidence:
                        merged[code] = merged_signal
            else:
                merged[code] = merged_signal

        return list(merged.values())


# ============================================================
# 模块自测
# ============================================================
if __name__ == "__main__":
    from utils.logger import console

    console.print("[header]🧠 策略引擎测试[/header]\n")

    # 创建策略管理器并注册默认策略
    manager = StrategyManager()
    manager.add_default_strategies()

    # 模拟持仓（空仓状态）
    test_positions = []
    test_account = {
        "total_assets": 1000000,
        "available_cash": 1000000,
        "market_value": 0,
    }

    # 运行所有策略
    console.print("[cyan]运行策略分析...[/cyan]")
    result = manager.run_all(test_positions, test_account)

    # 展示结果
    console.print(f"\n📊 {result['summary']}\n")

    if result["buy_signals"]:
        console.print("[green]买入信号:[/green]")
        for sig in result["buy_signals"]:
            console.print(
                f"  🟢 {sig.stock_code} {sig.stock_name} "
                f"置信度:{sig.confidence:.2f} 来源:{sig.strategy}"
            )
            console.print(f"     原因: {sig.reason}")

    if result["sell_signals"]:
        console.print("\n[red]卖出信号:[/red]")
        for sig in result["sell_signals"]:
            console.print(
                f"  🔴 {sig.stock_code} {sig.stock_name} "
                f"置信度:{sig.confidence:.2f}"
            )
            console.print(f"     原因: {sig.reason}")

    console.print("\n[success]✅ 策略引擎测试完成[/success]")
