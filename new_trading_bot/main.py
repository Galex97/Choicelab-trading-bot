"""
东方财富A股争霸赛 - 交易机器人

按需调用模式，每条命令独立执行一次并退出。
JSON 结果写入 output/ 目录，供 Agent 消费。

命令速查：
  python main.py --login              登录保存 Cookie（首次必须）
  python main.py --positions          读取当前持仓 → output/positions.json
  python main.py --status             读取账户+持仓+排名 → output/account_status.json
  python main.py --trade              读 advice.json 执行买卖 → output/last_trade.json
  python main.py --trade --auto       无需确认，直接执行
  python main.py --signals            策略选股信号 → output/signals.json
  python main.py --watch              持续监控（默认每5分钟刷新 account_status.json）
  python main.py --watch --interval 3 每3分钟刷新一次
  python main.py --analyze            调试：截图+导出页面DOM
"""

import sys
import os
import asyncio
import argparse
from pathlib import Path
from datetime import datetime

# ── Windows UTF-8 编码修复（必须在 rich 之前）──────────────────
if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 启动横幅
# ============================================================

def show_banner(mode: str) -> None:
    from rich.panel import Panel
    from rich.text import Text
    from utils.logger import console

    text = Text()
    text.append("A股争霸赛交易机器人\n", style="bold magenta")
    text.append(f"模式: {mode}\n", style="bold cyan")
    text.append(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", style="dim")

    console.print(Panel(text, border_style="bright_magenta", padding=(0, 3)))
    console.print()


# ============================================================
# 参数解析
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="东方财富A股争霸赛交易机器人",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # ── 主命令（互斥）──────────────────────────────────────────
    cmd = parser.add_mutually_exclusive_group()

    cmd.add_argument(
        "--login",
        action="store_true",
        help="打开浏览器引导登录并保存 Cookie",
    )
    cmd.add_argument(
        "--positions",
        action="store_true",
        help="读取当前持仓 → output/positions.json",
    )
    cmd.add_argument(
        "--status",
        action="store_true",
        help="读取账户+持仓+排名 → output/account_status.json",
    )
    cmd.add_argument(
        "--trade",
        action="store_true",
        help="读 advice.json 执行买卖 → output/last_trade.json",
    )
    cmd.add_argument(
        "--signals",
        action="store_true",
        help="运行策略选股，输出信号 → output/signals.json（不连浏览器）",
    )
    cmd.add_argument(
        "--watch",
        action="store_true",
        help="持续监控模式，定时刷新 output/account_status.json",
    )
    cmd.add_argument(
        "--analyze",
        action="store_true",
        help="[调试] 截图+导出页面 DOM 结构",
    )

    # ── 附加选项 ───────────────────────────────────────────────
    parser.add_argument(
        "--auto",
        action="store_true",
        help="与 --trade 配合：跳过人工确认直接执行",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无头模式（不显示浏览器窗口），适合后台运行",
    )
    parser.add_argument(
        "--advice-file",
        type=str,
        default=None,
        metavar="PATH",
        help="与 --trade 配合：指定 advice.json 路径（默认 ../advice.json）",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=5,
        metavar="MIN",
        help="与 --watch 配合：刷新间隔分钟数（默认 5）",
    )

    args = parser.parse_args()

    # 默认命令：无参数时显示帮助
    has_cmd = any([
        args.login, args.positions, args.status,
        args.trade, args.signals, args.watch, args.analyze,
    ])
    if not has_cmd:
        parser.print_help()
        sys.exit(0)

    return args


# ============================================================
# 各命令实现
# ============================================================

async def run_login() -> None:
    from utils.logger import console
    from core.login import save_login_state

    show_banner("登录")
    success = await save_login_state()
    if success:
        console.print("\n[green]✅ 登录成功，Cookie 已保存。[/green]")
        console.print("[dim]之后运行其他命令时将自动使用此登录状态。[/dim]\n")
    else:
        console.print("\n[red]❌ 登录失败，请重试。[/red]\n")
        sys.exit(1)


async def run_positions(args: argparse.Namespace) -> None:
    from core.actions import action_positions
    show_banner("读取持仓")
    ok = await action_positions(headless=args.headless)
    if not ok:
        sys.exit(1)


async def run_status(args: argparse.Namespace) -> None:
    from core.actions import action_status
    show_banner("账户状态")
    ok = await action_status(headless=args.headless)
    if not ok:
        sys.exit(1)


async def run_trade(args: argparse.Namespace) -> None:
    from core.actions import action_trade
    show_banner("执行交易" + ("（自动确认）" if args.auto else "（人工审核）"))
    ok = await action_trade(
        advice_path=args.advice_file,
        review=not args.auto,
        headless=args.headless,
    )
    if not ok:
        sys.exit(1)


async def run_signals() -> None:
    from core.actions import action_signals
    from core.executor import load_config, load_risk_rules
    show_banner("策略选股信号")
    config = load_config()
    ok = await action_signals(config=config)
    if not ok:
        sys.exit(1)


async def run_watch(args: argparse.Namespace) -> None:
    from core.actions import action_watch
    show_banner(f"持续监控（{args.interval}分钟/次）")
    await action_watch(interval_minutes=args.interval, headless=args.headless)


async def run_analyze(args: argparse.Namespace) -> None:
    from utils.logger import console
    from core.login import get_authenticated_browser
    from core.browser_ops import analyze_page_structure, navigate_to_competition

    show_banner("页面结构分析（调试）")

    result = await get_authenticated_browser(headless=False)
    if result is None:
        console.print("[red]❌ 登录失败，无法分析页面[/red]")
        return

    pw, browser, context = result
    try:
        page = await context.new_page()
        await page.goto(
            "https://choicelab.eastmoney.com/mainPage",
            wait_until="domcontentloaded",
        )
        await asyncio.sleep(3)
        await analyze_page_structure(page, "main_page")

        ok = await navigate_to_competition(page)
        if ok:
            await asyncio.sleep(3)
            await analyze_page_structure(page, "competition_page")

        console.print("\n[green]✅ 分析完成，结果保存在 logs/screenshots/[/green]")
        console.print("[yellow]浏览器保持打开，Ctrl+C 关闭...[/yellow]")
        try:
            await asyncio.sleep(3600)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
    finally:
        await browser.close()
        await pw.stop()


# ============================================================
# 主入口
# ============================================================

async def main() -> None:
    args = parse_args()
    from utils.logger import console

    try:
        if args.login:
            await run_login()
        elif args.positions:
            await run_positions(args)
        elif args.status:
            await run_status(args)
        elif args.trade:
            await run_trade(args)
        elif args.signals:
            await run_signals()
        elif args.watch:
            await run_watch(args)
        elif args.analyze:
            await run_analyze(args)

    except KeyboardInterrupt:
        console.print("\n[yellow]已中断[/yellow]\n")
    except Exception as e:
        console.print(f"\n[red]❌ 异常退出: {e}[/red]\n")
        from utils.logger import get_logger
        get_logger("main").error(f"异常: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
