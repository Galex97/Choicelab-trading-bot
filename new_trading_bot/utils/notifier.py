"""
通知模块 - 交易计划展示与执行结果通知

功能：
- 终端美化展示交易计划（供人工审核）
- 执行结果通知
- 可扩展的通知接口（预留微信/钉钉）
"""

from typing import Optional
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Confirm
from rich.layout import Layout
from rich.text import Text
from rich.theme import Theme

from utils.logger import get_logger

# ============================================================
# 全局实例
# ============================================================

logger = get_logger("notifier")

# 通知专用 Console（确保独立于日志的输出）
notify_console = Console(theme=Theme({
    "buy": "bold green",
    "sell": "bold red",
    "hold": "dim cyan",
    "profit": "bold green",
    "loss": "bold red",
    "neutral": "white",
    "header": "bold magenta",
}))


# ============================================================
# 交易计划展示
# ============================================================

def notify_trade_plan(plan: dict) -> bool:
    """
    在终端展示交易计划，等待用户确认

    Args:
        plan: 交易计划字典，格式如下：
            {
                "sell": [{"code": "...", "name": "...", "reason": "..."}],
                "buy": [{"code": "...", "name": "...", "amount": N, "reason": "..."}],
                "reasoning": "整体分析说明"
            }

    Returns:
        bool - 用户是否确认执行
    """
    notify_console.print()
    notify_console.rule("[header]📋 交易计划 - 等待审核[/header]", style="magenta")
    notify_console.print()

    # ----- 展示卖出计划 -----
    sell_items = plan.get("sell", [])
    if sell_items:
        sell_table = Table(
            title="🔴 卖出计划",
            title_style="bold red",
            show_header=True,
            header_style="bold",
            border_style="red",
        )
        sell_table.add_column("股票代码", style="cyan", width=10)
        sell_table.add_column("股票名称", width=12)
        sell_table.add_column("卖出原因", style="dim")

        for item in sell_items:
            sell_table.add_row(
                item.get("code", "?"),
                item.get("name", "-"),
                item.get("reason", "-"),
            )
        notify_console.print(sell_table)
        notify_console.print()

    # ----- 展示买入计划 -----
    buy_items = plan.get("buy", [])
    if buy_items:
        buy_table = Table(
            title="🟢 买入计划",
            title_style="bold green",
            show_header=True,
            header_style="bold",
            border_style="green",
        )
        buy_table.add_column("股票代码", style="cyan", width=10)
        buy_table.add_column("股票名称", width=12)
        buy_table.add_column("买入数量", justify="right", style="green")
        buy_table.add_column("买入原因", style="dim")

        for item in buy_items:
            buy_table.add_row(
                item.get("code", "?"),
                item.get("name", "-"),
                str(item.get("amount", "?")),
                item.get("reason", "-"),
            )
        notify_console.print(buy_table)
        notify_console.print()

    # ----- 展示AI分析摘要 -----
    reasoning = plan.get("reasoning", "")
    if reasoning:
        notify_console.print(
            Panel(
                reasoning,
                title="💡 AI 分析摘要",
                title_align="left",
                border_style="blue",
                padding=(1, 2),
            )
        )
        notify_console.print()

    # ----- 无操作的情况 -----
    if not sell_items and not buy_items:
        notify_console.print(
            Panel(
                "当前无交易信号，建议持仓观望。",
                title="ℹ️ 空仓/持仓不动",
                border_style="dim",
            )
        )
        return False

    # ----- 等待用户确认 -----
    notify_console.print()
    confirmed = Confirm.ask(
        "[bold yellow]❓ 是否确认执行以上交易计划？[/bold yellow]",
        default=False,
    )

    if confirmed:
        notify_console.print("[success]✅ 用户已确认，准备执行...[/success]")
        logger.info("用户确认执行交易计划")
    else:
        notify_console.print("[warning]⏸️ 用户取消执行[/warning]")
        logger.info("用户取消执行交易计划")

    return confirmed


# ============================================================
# 执行结果通知
# ============================================================

def notify_execution_result(results: list[dict]) -> None:
    """
    展示交易执行结果

    Args:
        results: 执行结果列表，每个元素包含：
            {
                "action": "买入/卖出",
                "code": "股票代码",
                "name": "股票名称",
                "amount": 数量,
                "price": 成交价,
                "status": "成功/失败",
                "message": "备注信息"
            }
    """
    notify_console.print()
    notify_console.rule("[header]📊 执行结果[/header]", style="magenta")
    notify_console.print()

    result_table = Table(
        title="交易执行详情",
        show_header=True,
        header_style="bold",
    )
    result_table.add_column("操作", width=6)
    result_table.add_column("代码", style="cyan", width=10)
    result_table.add_column("名称", width=12)
    result_table.add_column("数量", justify="right", width=8)
    result_table.add_column("价格", justify="right", width=10)
    result_table.add_column("状态", width=6)
    result_table.add_column("备注", style="dim")

    success_count = 0
    fail_count = 0

    for r in results:
        action = r.get("action", "?")
        status = r.get("status", "未知")

        # 根据状态设置样式
        if status == "成功":
            status_display = "[green]✅ 成功[/green]"
            success_count += 1
        else:
            status_display = "[red]❌ 失败[/red]"
            fail_count += 1

        # 根据操作类型设置样式
        action_display = f"[buy]{action}[/buy]" if action == "买入" else f"[sell]{action}[/sell]"

        result_table.add_row(
            action_display,
            r.get("code", "?"),
            r.get("name", "-"),
            str(r.get("amount", "-")),
            f"{r.get('price', 0):.2f}" if r.get("price") else "-",
            status_display,
            r.get("message", ""),
        )

    notify_console.print(result_table)
    notify_console.print()

    # 汇总统计
    summary_text = f"成功: [green]{success_count}[/green] 笔  |  失败: [red]{fail_count}[/red] 笔"
    notify_console.print(
        Panel(summary_text, title="📈 执行汇总", border_style="blue")
    )
    notify_console.print()


# ============================================================
# 账户信息展示
# ============================================================

def notify_account_status(account_info: dict) -> None:
    """
    展示账户状态信息

    Args:
        account_info: 账户信息字典：
            {
                "total_assets": 总资产,
                "available_cash": 可用资金,
                "market_value": 持仓市值,
                "profit": 盈亏金额,
                "profit_pct": 盈亏百分比,
                "ranking": 排名
            }
    """
    notify_console.print()

    profit_pct = account_info.get("profit_pct", 0)
    profit_style = "profit" if profit_pct >= 0 else "loss"
    profit_sign = "+" if profit_pct >= 0 else ""

    info_lines = [
        f"💰 总资产:   ¥{account_info.get('total_assets', 0):>12,.2f}",
        f"💵 可用资金: ¥{account_info.get('available_cash', 0):>12,.2f}",
        f"📦 持仓市值: ¥{account_info.get('market_value', 0):>12,.2f}",
        f"📊 盈亏:     [{profit_style}]{profit_sign}{profit_pct:.2f}%[/{profit_style}]"
        f"  (¥{account_info.get('profit', 0):+,.2f})",
        f"🏆 当前排名: 第 {account_info.get('ranking', '?')} 名",
    ]

    notify_console.print(
        Panel(
            "\n".join(info_lines),
            title=f"📋 账户状态 - {datetime.now().strftime('%H:%M:%S')}",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    notify_console.print()


# ============================================================
# 持仓展示
# ============================================================

def notify_positions(positions: list[dict]) -> None:
    """
    展示当前持仓信息

    Args:
        positions: 持仓列表，每个元素包含：
            {
                "code": "股票代码",
                "name": "股票名称",
                "amount": 持仓数量,
                "cost_price": 成本价,
                "current_price": 当前价,
                "profit_pct": 盈亏百分比,
                "market_value": 市值
            }
    """
    if not positions:
        notify_console.print(
            Panel("当前空仓", title="📦 持仓信息", border_style="dim")
        )
        return

    pos_table = Table(
        title="📦 当前持仓",
        show_header=True,
        header_style="bold",
        border_style="cyan",
    )
    pos_table.add_column("代码", style="cyan", width=10)
    pos_table.add_column("名称", width=12)
    pos_table.add_column("数量", justify="right", width=8)
    pos_table.add_column("成本价", justify="right", width=10)
    pos_table.add_column("现价", justify="right", width=10)
    pos_table.add_column("盈亏%", justify="right", width=10)
    pos_table.add_column("市值", justify="right", width=12)

    for pos in positions:
        pct = pos.get("profit_pct", 0)
        pct_style = "green" if pct >= 0 else "red"
        pct_sign = "+" if pct >= 0 else ""

        pos_table.add_row(
            pos.get("code", "?"),
            pos.get("name", "-"),
            str(pos.get("amount", 0)),
            f"{pos.get('cost_price', 0):.2f}",
            f"{pos.get('current_price', 0):.2f}",
            f"[{pct_style}]{pct_sign}{pct:.2f}%[/{pct_style}]",
            f"¥{pos.get('market_value', 0):,.2f}",
        )

    notify_console.print(pos_table)
    notify_console.print()


# ============================================================
# 文件通知（简单日志文件写入，用于无人值守时的记录）
# ============================================================

def save_notification_to_file(title: str, content: str) -> None:
    """
    将通知内容保存到文件（用于无人值守模式回顾）

    Args:
        title: 通知标题
        content: 通知内容
    """
    from utils.logger import LOG_DIR

    notify_file = LOG_DIR / "notifications.log"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(notify_file, "a", encoding="utf-8") as f:
        f.write(f"\n{'=' * 60}\n")
        f.write(f"[{now_str}] {title}\n")
        f.write(f"{'=' * 60}\n")
        f.write(content + "\n")

    logger.debug(f"通知已保存到文件: {notify_file}")


# ============================================================
# 模块自测
# ============================================================
if __name__ == "__main__":
    # 测试交易计划展示
    test_plan = {
        "sell": [
            {"code": "600519", "name": "贵州茅台", "reason": "触及止盈线 +8.5%"},
        ],
        "buy": [
            {"code": "000001", "name": "平安银行", "amount": 200, "reason": "突破20日均线"},
            {"code": "300750", "name": "宁德时代", "amount": 100, "reason": "板块龙头放量"},
        ],
        "reasoning": "今日市场震荡偏强，新能源板块资金流入明显，建议减持高位股，布局低位优质标的。",
    }

    # 展示计划（但不实际等待输入）
    notify_console.print("[header]===== 通知模块测试 =====[/header]")

    # 测试账户状态
    notify_account_status({
        "total_assets": 1050000.00,
        "available_cash": 320000.00,
        "market_value": 730000.00,
        "profit": 50000.00,
        "profit_pct": 5.0,
        "ranking": 42,
    })

    # 测试持仓展示
    notify_positions([
        {
            "code": "600519",
            "name": "贵州茅台",
            "amount": 100,
            "cost_price": 1800.00,
            "current_price": 1950.00,
            "profit_pct": 8.33,
            "market_value": 195000.00,
        },
    ])

    # 测试执行结果
    notify_execution_result([
        {
            "action": "卖出",
            "code": "600519",
            "name": "贵州茅台",
            "amount": 100,
            "price": 1950.00,
            "status": "成功",
            "message": "止盈卖出",
        },
        {
            "action": "买入",
            "code": "000001",
            "name": "平安银行",
            "amount": 200,
            "price": 12.50,
            "status": "成功",
            "message": "策略买入",
        },
    ])

    notify_console.print("[success]✅ 通知模块测试完成[/success]")
