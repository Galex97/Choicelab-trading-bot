"""
actions.py - Bot 功能集合

每个函数对应一个 CLI 子命令，统一处理：
  - 浏览器初始化 / 清理
  - 核心操作（读持仓、读账户、执行交易等）
  - 终端展示（rich）
  - JSON 文件输出（供 Agent 消费）

JSON 输出目录：项目根目录下的 output/ 文件夹
  output/positions.json         ← 持仓
  output/account_status.json    ← 账户+排名+持仓汇总
  output/signals.json           ← 策略选股信号
  output/last_trade.json        ← 最近一次交易执行结果
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from utils.logger import get_logger, console

logger = get_logger("actions")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"


# ============================================================
# 工具函数
# ============================================================

def _save_json(filename: str, data: dict | list) -> Path:
    """将数据写入 output/ 目录的 JSON 文件"""
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"已写入 {path}")
    return path


async def _open_browser(headless: bool = False):
    """
    获取登录后的浏览器 Page，失败时返回 None。
    调用方负责关闭 browser。
    """
    from core.login import get_authenticated_browser
    from core.browser_ops import navigate_to_competition

    result = await get_authenticated_browser(headless=headless)
    if result is None:
        console.print(
            "[error]❌ 无法获取登录状态。\n"
            "   请先运行 [bold]python main.py --login[/bold] 完成登录。[/error]"
        )
        return None, None, None, None

    pw, browser, context = result
    page = await context.new_page()
    ok = await navigate_to_competition(page)
    if not ok:
        console.print("[error]❌ 无法导航到比赛交易页面，请检查网络或 Cookie。[/error]")
        await browser.close()
        await pw.stop()
        return None, None, None, None

    return pw, browser, context, page


async def _close_browser(pw, browser) -> None:
    """关闭浏览器资源"""
    try:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()
    except Exception:
        pass


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# ACTION 1: 读取持仓
# ============================================================

async def action_positions(headless: bool = False) -> bool:
    """
    读取当前持仓，输出到终端并写入 output/positions.json

    JSON 格式：
    {
      "timestamp": "...",
      "count": N,
      "positions": [
        {"code": "000001", "name": "平安银行", "amount": 1000,
         "cost_price": 12.5, "current_price": 13.2,
         "profit_pct": 5.6, "market_value": 13200.0}
      ]
    }
    """
    console.rule("[bold cyan]📋 读取持仓[/bold cyan]")

    pw, browser, context, page = await _open_browser(headless)
    if page is None:
        return False

    try:
        from core.browser_ops import get_current_positions
        from utils.notifier import notify_positions

        positions = await get_current_positions(page)

        # 终端展示
        if positions:
            notify_positions(positions)
        else:
            console.print("[yellow]当前空仓[/yellow]")

        # 写 JSON
        payload = {
            "timestamp": _ts(),
            "count": len(positions),
            "positions": positions,
        }
        out = _save_json("positions.json", payload)
        console.print(f"\n[dim]已写入 → {out}[/dim]")
        return True

    except Exception as e:
        logger.error(f"读取持仓失败: {e}")
        console.print(f"[error]❌ 读取持仓失败: {e}[/error]")
        return False
    finally:
        await _close_browser(pw, browser)


# ============================================================
# ACTION 2: 读取账户状态（持仓 + 资金 + 排名）
# ============================================================

async def action_status(headless: bool = False) -> bool:
    """
    读取完整账户状态：账户资金 + 持仓列表 + 比赛排名。
    输出终端 + output/account_status.json

    JSON 格式：
    {
      "timestamp": "...",
      "account": {
        "total_assets": 1050000,
        "available_cash": 300000,
        "market_value": 750000,
        "profit": 50000,
        "profit_pct": 5.0
      },
      "ranking": {"rank": 12, "raw": "..."},
      "positions": [...],
      "summary": "总资产105.0万 | 收益+5.00% | 排名第12名 | 持仓3只"
    }
    """
    console.rule("[bold cyan]📊 账户状态[/bold cyan]")

    pw, browser, context, page = await _open_browser(headless)
    if page is None:
        return False

    try:
        from core.browser_ops import (
            get_current_positions,
            get_account_info,
            get_ranking,
        )
        from utils.notifier import notify_account_status, notify_positions

        # 并行读取账户和持仓（同一页面下串行，因为都依赖导航）
        account = await get_account_info(page)
        positions = await get_current_positions(page)
        ranking = await get_ranking(page)

        # 将排名注入 account dict，notifier 从 account['ranking'] 读取
        raw_rank = ranking.get("rank", 0) if ranking else 0
        account["ranking"] = raw_rank if raw_rank and raw_rank > 0 else "?"

        # 终端展示
        notify_account_status(account)
        if positions:
            notify_positions(positions)
        else:
            console.print("[yellow]当前空仓[/yellow]")

        rank_str = f"第{ranking.get('rank', '?')}名" if ranking else "未获取"
        profit_pct = account.get("profit_pct", 0)
        total = account.get("total_assets", 0)
        summary = (
            f"总资产{total/10000:.1f}万 | "
            f"收益{profit_pct:+.2f}% | "
            f"排名{rank_str} | "
            f"持仓{len(positions)}只"
        )
        console.print(f"\n[bold]{summary}[/bold]")

        # 写 JSON
        payload = {
            "timestamp": _ts(),
            "account": account,
            "ranking": ranking,
            "positions": positions,
            "summary": summary,
        }
        out = _save_json("account_status.json", payload)
        console.print(f"[dim]已写入 → {out}[/dim]")
        return True

    except Exception as e:
        logger.error(f"读取账户状态失败: {e}")
        console.print(f"[error]❌ 读取账户状态失败: {e}[/error]")
        return False
    finally:
        await _close_browser(pw, browser)


# ============================================================
# ACTION 3: 执行交易（读 advice.json）
# ============================================================

async def action_trade(
    advice_path: Optional[str] = None,
    review: bool = True,
    headless: bool = False,
) -> bool:
    """
    读取 advice.json，经 risk 护栏后执行买卖。
    执行结果写入 output/last_trade.json

    advice.json 格式（由 Agent 写入）：
    {
      "sell": [{"code": "000333", "name": "美的集团", "reason": "止损"}],
      "buy":  [{"code": "601138", "name": "工业富联", "amount": 900, "reason": "..."}],
      "reasoning": "整体决策说明"
    }
    """
    from core.executor import TradeExecutor

    console.rule("[bold magenta]🚀 执行交易[/bold magenta]")

    executor = TradeExecutor(
        review_mode=review,
        enable_ai=False,
        headless=headless,
    )

    try:
        result = await executor.run_from_advice_file(advice_path=advice_path)
        result["timestamp"] = _ts()

        # 写结果 JSON
        out = _save_json("last_trade.json", result)
        console.print(f"\n[dim]执行结果 → {out}[/dim]")

        if result.get("success"):
            console.print("[green]✅ 交易执行完成[/green]")
            return True
        else:
            console.print("[red]❌ 交易执行失败，请查看日志[/red]")
            return False

    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️ 用户中断[/yellow]")
        return False
    finally:
        await executor.cleanup()


# ============================================================
# ACTION 4: 策略选股信号（供 Agent 参考）
# ============================================================

async def action_signals(config: Optional[dict] = None) -> bool:
    """
    运行量化策略引擎，输出选股信号到终端 + output/signals.json
    仅获取市场数据，不连接浏览器，不执行任何交易。

    JSON 格式：
    {
      "timestamp": "...",
      "market": {"sentiment": "...", "limit_up_count": N, "hot_sectors": [...]},
      "buy_signals": [
        {"code": "...", "name": "...", "confidence": 0.72,
         "reason": "...", "strategy": "momentum+breakout"}
      ],
      "sell_signals": [...],
      "summary": "策略摘要"
    }
    """
    console.rule("[bold green]🧠 策略选股信号[/bold green]")

    try:
        from core.data_fetcher import get_market_overview
        from core.strategy import StrategyManager

        # 市场概览
        console.print("[cyan]📊 获取市场行情...[/cyan]")
        market = get_market_overview()
        sentiment = market.get("market_sentiment", "?")
        limit_up = market.get("limit_up_count", 0)
        console.print(f"  涨停: [bold]{limit_up}[/bold] 只  |  情绪: [bold]{sentiment}[/bold]")

        hot_sectors = market.get("hot_sectors", [])
        if hot_sectors:
            console.print("  热门板块: " + " / ".join(
                s.get("sector_name", s.get("name", "?")) for s in hot_sectors[:5]
            ))

        # 策略信号
        console.print("\n[cyan]🔍 运行策略分析...[/cyan]")
        manager = StrategyManager()
        manager.add_default_strategies(config=config)

        signals = manager.run_all(
            positions=[],
            account_info={"total_assets": 1_000_000, "available_cash": 1_000_000, "market_value": 0},
        )

        buy_signals = signals.get("buy_signals", [])
        sell_signals = signals.get("sell_signals", [])
        console.print(f"  {signals.get('summary', '')}")

        # 终端展示买入信号
        if buy_signals:
            console.print("\n[bold]📈 买入参考信号:[/bold]")
            for sig in buy_signals:
                d = sig.to_dict() if hasattr(sig, "to_dict") else sig
                console.print(
                    f"  [green]▶[/green] {d.get('stock_code','')} {d.get('stock_name','')} "
                    f"  置信度 [bold]{d.get('confidence',0):.0%}[/bold]"
                    f"  [{d.get('strategy','')}]"
                    f"\n     {d.get('reason','')}"
                )
        else:
            console.print("[yellow]  暂无买入信号[/yellow]")

        # 整理 JSON
        def _sig_list(sigs):
            return [
                (s.to_dict() if hasattr(s, "to_dict") else s)
                for s in sigs
            ]

        payload = {
            "timestamp": _ts(),
            "market": {
                "sentiment": sentiment,
                "limit_up_count": limit_up,
                "hot_sectors": [
                    {"name": s.get("sector_name", s.get("name", "")),
                     "change_pct": s.get("change_pct", 0)}
                    for s in hot_sectors[:10]
                ],
            },
            "buy_signals": _sig_list(buy_signals),
            "sell_signals": _sig_list(sell_signals),
            "summary": signals.get("summary", ""),
        }
        out = _save_json("signals.json", payload)
        console.print(f"\n[dim]已写入 → {out}[/dim]")
        return True

    except Exception as e:
        logger.error(f"策略分析失败: {e}", exc_info=True)
        console.print(f"[error]❌ 策略分析失败: {e}[/error]")
        return False


# ============================================================
# ACTION 5: 持续监控（Watch 模式）
# ============================================================

async def action_watch(
    interval_minutes: int = 5,
    headless: bool = True,
) -> None:
    """
    持续监控模式：每隔 interval_minutes 分钟刷新一次账户状态。
    每次刷新都更新 output/account_status.json，Agent 可随时读取最新快照。
    按 Ctrl+C 退出。
    """
    console.rule(f"[bold cyan]👁  监控模式  (每 {interval_minutes} 分钟刷新)[/bold cyan]")
    console.print("[dim]按 Ctrl+C 退出监控[/dim]\n")

    round_num = 0
    try:
        while True:
            round_num += 1
            console.print(f"[dim]── 第 {round_num} 轮  {_ts()} ──[/dim]")

            pw, browser, context, page = await _open_browser(headless)
            if page is None:
                console.print(f"[yellow]浏览器连接失败，{interval_minutes} 分钟后重试...[/yellow]")
            else:
                try:
                    from core.browser_ops import (
                        get_current_positions,
                        get_account_info,
                        get_ranking,
                    )
                    account = await get_account_info(page)
                    positions = await get_current_positions(page)
                    ranking = await get_ranking(page)

                    raw_rank = ranking.get("rank", 0) if ranking else 0
                    rank_str = f"第{raw_rank}名" if raw_rank and raw_rank > 0 else "?"
                    account["ranking"] = raw_rank if raw_rank > 0 else "?"
                    profit_pct = account.get("profit_pct", 0)
                    total = account.get("total_assets", 0)

                    summary = (
                        f"总资产{total/10000:.1f}万 | "
                        f"收益{profit_pct:+.2f}% | "
                        f"排名{rank_str} | "
                        f"持仓{len(positions)}只"
                    )
                    console.print(f"  [bold]{summary}[/bold]")

                    payload = {
                        "timestamp": _ts(),
                        "account": account,
                        "ranking": ranking,
                        "positions": positions,
                        "summary": summary,
                        "watch_round": round_num,
                    }
                    _save_json("account_status.json", payload)

                    # ── 自动止盈止损（条件单）检查与执行 ───────────────────────
                    from core.executor import load_config
                    config = load_config()
                    strategy_config = config.get("strategy", {})
                    stop_loss = strategy_config.get("stop_loss", -5.0)
                    take_profit = strategy_config.get("take_profit", 8.0)

                    triggered_sells = []
                    for pos in positions:
                        code = pos.get("code", "")
                        name = pos.get("name", "")
                        profit_pct = pos.get("profit_pct", 0.0)
                        amount = int(pos.get("available") or pos.get("amount") or 0)
                        
                        if profit_pct <= stop_loss:
                            triggered_sells.append({
                                "code": code,
                                "name": name,
                                "amount": amount,
                                "reason": f"🛑 触发自动止损线({stop_loss}%): 当前亏损{profit_pct:.1f}%"
                            })
                        elif profit_pct >= take_profit:
                            triggered_sells.append({
                                "code": code,
                                "name": name,
                                "amount": amount,
                                "reason": f"📈 触发自动止盈线({take_profit}%): 当前盈利{profit_pct:.1f}%"
                            })

                    if triggered_sells:
                        console.print(f"\n[warning]⚠️ 触发自动止盈止损条件单！共 {len(triggered_sells)} 笔[/warning]")
                        from core.browser_ops import sell_stock
                        from utils.notifier import notify_execution_result
                        
                        execution_results = []
                        for item in triggered_sells:
                            code = item["code"]
                            amount = item["amount"]
                            console.print(f"  [red]▶[/red] 自动卖出: {code} {item['name']} x {amount} 股 | 原因: {item['reason']}")
                            try:
                                result = await sell_stock(page, code, amount)
                                execution_results.append({
                                    "action": "卖出",
                                    "code": code,
                                    "name": item["name"],
                                    "amount": amount,
                                    "price": result.get("price", 0),
                                    "status": "成功" if result.get("success") else "失败",
                                    "message": result.get("message", ""),
                                })
                            except Exception as e:
                                logger.error(f"自动卖出 {code} 异常: {e}")
                                execution_results.append({
                                    "action": "卖出",
                                    "code": code,
                                    "name": item["name"],
                                    "amount": amount,
                                    "price": 0,
                                    "status": "失败",
                                    "message": str(e),
                                })
                        
                        if execution_results:
                            notify_execution_result(execution_results)
                            # 保存交易日志到 last_trade.json 和交易记录日志中
                            payload_trade = {
                                "timestamp": _ts(),
                                "trades_executed": execution_results,
                                "success": any(r["status"] == "成功" for r in execution_results),
                                "source": "watch_auto_risk_control"
                            }
                            _save_json("last_trade.json", payload_trade)
                            
                            # 同时也把这笔自动成交记录到交易日志中，保持日志完整性
                            try:
                                from utils.logger import get_trade_logger
                                trade_logger = get_trade_logger()
                                for r in execution_results:
                                    trade_logger.log_trade(
                                        action=r.get("action", "?"),
                                        stock_code=r.get("code", "?"),
                                        stock_name=r.get("name", ""),
                                        amount=r.get("amount", 0),
                                        price=r.get("price", 0),
                                        total=r.get("amount", 0) * r.get("price", 0),
                                        reason=r.get("message", ""),
                                        status=r.get("status", "未知"),
                                    )
                            except Exception:
                                pass

                except Exception as e:
                    logger.error(f"监控轮次 {round_num} 失败: {e}")
                    console.print(f"[red]  ✗ 本轮失败: {e}[/red]")
                finally:
                    await _close_browser(pw, browser)

            console.print(f"[dim]  下次刷新: {interval_minutes} 分钟后[/dim]\n")
            await asyncio.sleep(interval_minutes * 60)

    except KeyboardInterrupt:
        console.print("\n[yellow]监控已停止[/yellow]")
