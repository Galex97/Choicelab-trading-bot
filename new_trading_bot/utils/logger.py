"""
日志模块 - 提供统一的日志记录功能

功能：
- 文件日志（按日期轮转）
- Rich终端美化输出
- 交易专用日志记录器
"""

import os
import sys
import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

# ============================================================
# 全局常量
# ============================================================

# 项目根目录（trading_bot/）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 日志目录
LOG_DIR = PROJECT_ROOT / "logs"
TRADE_LOG_DIR = LOG_DIR / "trades"
SCREENSHOT_DIR = LOG_DIR / "screenshots"

# Rich 主题配色
CUSTOM_THEME = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "success": "bold green",
    "trade.buy": "bold green",
    "trade.sell": "bold red",
    "trade.hold": "dim",
    "header": "bold magenta",
})

# Windows 终端 UTF-8 兼容处理
if sys.platform == "win32":
    # 包装 stdout 为 UTF-8 编码，替换无法编码的字符
    _utf8_stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
    console = Console(theme=CUSTOM_THEME, file=_utf8_stdout, force_terminal=True)
else:
    console = Console(theme=CUSTOM_THEME)


# ============================================================
# 日志初始化
# ============================================================

def _ensure_log_dirs() -> None:
    """确保日志目录存在"""
    for d in [LOG_DIR, TRADE_LOG_DIR, SCREENSHOT_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def setup_logger(
    name: str = "trading_bot",
    level: str = "INFO",
    log_dir: Optional[str] = None,
) -> logging.Logger:
    """
    配置并返回日志记录器

    Args:
        name: 日志器名称
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        log_dir: 自定义日志目录路径

    Returns:
        配置完成的 Logger 实例
    """
    _ensure_log_dirs()

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    # ----- Rich 终端输出 Handler -----
    rich_handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
        tracebacks_show_locals=True,
    )
    rich_handler.setLevel(logging.DEBUG)
    rich_format = logging.Formatter("%(message)s", datefmt="[%X]")
    rich_handler.setFormatter(rich_format)
    logger.addHandler(rich_handler)

    # ----- 文件日志 Handler（按天轮转） -----
    target_log_dir = Path(log_dir) if log_dir else LOG_DIR
    target_log_dir.mkdir(parents=True, exist_ok=True)

    today_str = datetime.now().strftime("%Y-%m-%d")
    log_file = target_log_dir / f"{name}_{today_str}.log"

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_format)
    logger.addHandler(file_handler)

    return logger


# ============================================================
# 交易日志记录器
# ============================================================

class TradeLogger:
    """
    交易专用日志记录器

    将每笔交易操作以结构化格式写入独立的交易日志文件，
    便于后续复盘和统计分析。
    """

    def __init__(self) -> None:
        """初始化交易日志器"""
        _ensure_log_dirs()
        self._logger = setup_logger("trade_record", level="DEBUG")
        self._trade_file = self._get_trade_log_file()

    def _get_trade_log_file(self) -> Path:
        """获取当日交易日志文件路径"""
        today_str = datetime.now().strftime("%Y-%m-%d")
        return TRADE_LOG_DIR / f"trades_{today_str}.csv"

    def _ensure_csv_header(self) -> None:
        """确保 CSV 文件包含表头"""
        if not self._trade_file.exists():
            with open(self._trade_file, "w", encoding="utf-8") as f:
                f.write("时间,操作,股票代码,股票名称,数量,价格,金额,原因,状态\n")

    def log_trade(
        self,
        action: str,
        stock_code: str,
        stock_name: str = "",
        amount: int = 0,
        price: float = 0.0,
        total: float = 0.0,
        reason: str = "",
        status: str = "成功",
    ) -> None:
        """
        记录一笔交易操作

        Args:
            action: 操作类型（买入/卖出/撤单）
            stock_code: 股票代码
            stock_name: 股票名称
            amount: 交易数量（股）
            price: 成交价格
            total: 成交金额
            reason: 交易原因
            status: 执行状态（成功/失败/待确认）
        """
        self._ensure_csv_header()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 写入 CSV 文件
        line = f"{now_str},{action},{stock_code},{stock_name},{amount},{price:.2f},{total:.2f},{reason},{status}\n"
        with open(self._trade_file, "a", encoding="utf-8") as f:
            f.write(line)

        # 同步到控制台和主日志
        style = "trade.buy" if action == "买入" else "trade.sell" if action == "卖出" else "trade.hold"
        self._logger.info(
            f"[{style}]【{action}】{stock_code} {stock_name} "
            f"数量:{amount} 价格:{price:.2f} 金额:{total:.2f} "
            f"状态:{status} 原因:{reason}[/{style}]"
        )

    def log_plan(self, plan: dict) -> None:
        """
        记录交易计划

        Args:
            plan: 交易计划字典
        """
        self._logger.info("=" * 60)
        self._logger.info("📋 交易计划")
        self._logger.info("=" * 60)

        if plan.get("sell"):
            for item in plan["sell"]:
                self._logger.info(
                    f"  [trade.sell]🔴 卖出 {item.get('code', '?')} - {item.get('reason', '')}[/trade.sell]"
                )

        if plan.get("buy"):
            for item in plan["buy"]:
                self._logger.info(
                    f"  [trade.buy]🟢 买入 {item.get('code', '?')} "
                    f"数量:{item.get('amount', '?')} - {item.get('reason', '')}[/trade.buy]"
                )

        if plan.get("reasoning"):
            self._logger.info(f"  💡 分析: {plan['reasoning']}")

        self._logger.info("=" * 60)

    def log_summary(self, summary: dict) -> None:
        """
        记录日终总结

        Args:
            summary: 包含当日盈亏、排名等汇总信息
        """
        self._logger.info("=" * 60)
        self._logger.info("📊 日终总结")
        self._logger.info("=" * 60)
        for key, value in summary.items():
            self._logger.info(f"  {key}: {value}")
        self._logger.info("=" * 60)


# ============================================================
# 便捷函数
# ============================================================

def get_logger(name: str = "trading_bot") -> logging.Logger:
    """
    获取或创建日志记录器的便捷函数

    Args:
        name: 日志器名称

    Returns:
        Logger 实例
    """
    return setup_logger(name)


def get_trade_logger() -> TradeLogger:
    """
    获取交易日志记录器的便捷函数

    Returns:
        TradeLogger 实例
    """
    return TradeLogger()


# ============================================================
# 模块自测
# ============================================================
if __name__ == "__main__":
    # 测试基本日志
    logger = get_logger("test")
    logger.info("这是一条信息日志")
    logger.warning("这是一条警告日志")
    logger.error("这是一条错误日志")

    # 测试交易日志
    trade_log = get_trade_logger()
    trade_log.log_trade(
        action="买入",
        stock_code="000001",
        stock_name="平安银行",
        amount=100,
        price=12.50,
        total=1250.00,
        reason="测试买入",
        status="成功",
    )

    console.print("[success]✅ 日志模块测试完成[/success]")
