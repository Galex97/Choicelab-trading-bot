"""
交易执行器 - 全流程编排引擎

功能：
- 编排完整的交易工作流（数据→策略→AI→审核→执行→记录）
- 早盘分析与开盘交易
- 盘中定时监控
- 尾盘总结
- 支持人工审核模式和全自动模式

工作流：
1. 获取市场数据
2. 运行量化策略
3. 获取 AI 建议
4. 若为审核模式：在终端展示计划，等待用户确认
5. 执行交易操作（通过浏览器）
6. 记录交易结果
"""

import asyncio
import json
import yaml
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Optional

from utils.logger import get_logger, get_trade_logger, console
from utils.notifier import (
    notify_trade_plan,
    notify_execution_result,
    notify_account_status,
    notify_positions,
)
from core.risk import load_risk_rules, normalize_trade_plan

logger = get_logger("executor")

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ============================================================
# 配置加载
# ============================================================

def load_config() -> dict:
    """
    加载配置文件

    Returns:
        配置字典
    """
    config_path = PROJECT_ROOT / "config" / "settings.yaml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        logger.info("配置文件加载成功")
        return config or {}
    except Exception as e:
        logger.error(f"配置文件加载失败: {e}")
        return {}


# ============================================================
# 交易执行器
# ============================================================

class TradeExecutor:
    """
    交易执行器 - 核心编排引擎

    负责协调数据获取、策略分析、AI 决策、交易执行的完整流程。
    """

    def __init__(
        self,
        review_mode: bool = True,
        enable_ai: bool = True,
        headless: bool = False,
    ):
        """
        初始化交易执行器

        Args:
            review_mode: 是否启用人工审核模式（True=每笔交易需确认）
            enable_ai: 是否启用 AI 顾问
            headless: 浏览器是否无头模式
        """
        self.review_mode = review_mode
        self.enable_ai = enable_ai
        self.headless = headless

        # 加载配置
        self.config = load_config()
        strategy_config = self.config.get("strategy", {})
        self.stop_loss = strategy_config.get("stop_loss", -5.0)
        self.take_profit = strategy_config.get("take_profit", 8.0)
        self.max_position_ratio = strategy_config.get("max_position_ratio", 0.5)
        self.max_stocks = strategy_config.get("max_stocks", 3)
        self.risk_rules = load_risk_rules(self.config)

        # 交易日志
        self.trade_logger = get_trade_logger()

        # 浏览器资源（延迟初始化）
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

        # 策略管理器（延迟初始化）
        self._strategy_manager = None

        # AI 顾问（延迟初始化）
        self._ai_advisor = None

        mode_str = "审核模式" if review_mode else "全自动模式"
        ai_str = "启用" if enable_ai else "禁用"
        logger.info(f"执行器初始化: {mode_str}, AI={ai_str}")

    # ============================================================
    # 浏览器管理
    # ============================================================

    async def _ensure_browser(self) -> bool:
        """
        确保浏览器已连接并登录

        Returns:
            bool - 浏览器是否就绪
        """
        if self._page is not None:
            try:
                # 简单检查页面是否仍可用
                await self._page.title()
                return True
            except Exception:
                logger.warning("浏览器连接已断开，尝试重新连接...")
                self._page = None

        try:
            from core.login import get_authenticated_browser

            # Bug13修复：全自动模式（review_mode=False）下 Cookie 过期时不阻塞等待
            # auto_relogin=False 让 login 模块直接返回 None 而非弹出浏览器
            result = await get_authenticated_browser(
                headless=self.headless,
                auto_relogin=self.review_mode,  # 审核模式允许交互重登，自动模式不允许
            )
            if result is None:
                logger.error("无法获取认证浏览器")
                return False

            self._pw, self._browser, self._context = result
            self._page = await self._context.new_page()

            # 导航到比赛页面
            from core.browser_ops import navigate_to_competition
            await navigate_to_competition(self._page)

            logger.info("浏览器已就绪")
            return True

        except Exception as e:
            logger.error(f"浏览器初始化失败: {e}")
            return False

    async def _cleanup_browser(self) -> None:
        """清理浏览器资源"""
        try:
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
            logger.info("浏览器资源已清理")
        except Exception as e:
            logger.warning(f"浏览器清理时出错: {e}")
        finally:
            self._pw = None
            self._browser = None
            self._context = None
            self._page = None

    # ============================================================
    # 策略和AI初始化
    # ============================================================

    def _ensure_strategy_manager(self):
        """确保策略管理器已初始化"""
        if self._strategy_manager is None:
            from core.strategy import StrategyManager

            weights = self.config.get("strategy", {}).get("weights", {})
            self._strategy_manager = StrategyManager(
                weights=weights,
                max_buy_signals=self.max_stocks,
            )
            self._strategy_manager.add_default_strategies(config=self.config)

        return self._strategy_manager

    def _ensure_ai_advisor(self):
        """确保 AI 顾问已初始化"""
        if self._ai_advisor is None and self.enable_ai:
            from core.ai_advisor import AIAdvisor
            self._ai_advisor = AIAdvisor()

        return self._ai_advisor

    # ============================================================
    # 核心工作流
    # ============================================================

    async def run_full_cycle(self) -> dict:
        """
        运行一轮完整的交易分析与执行流程

        流程：
        1. 获取市场数据
        2. 获取持仓和账户信息
        3. 运行策略分析
        4. 获取 AI 建议
        5. 风控检查
        6. 审核（如启用）
        7. 执行交易
        8. 记录结果

        Returns:
            执行结果字典
        """
        cycle_result = {
            "timestamp": datetime.now().isoformat(),
            "trades_executed": [],
            "advice": {},
            "success": False,
        }

        try:
            console.rule("[header]🔄 开始新一轮交易分析[/header]", style="magenta")

            # ----- 1. 获取市场数据 -----
            console.print("\n[cyan]📊 步骤1: 获取市场数据...[/cyan]")
            from core.data_fetcher import get_market_overview
            market_overview = get_market_overview()
            logger.info(f"市场情绪: {market_overview.get('market_sentiment', '?')}")

            # ----- 2. 获取持仓和账户信息 -----
            console.print("[cyan]💰 步骤2: 获取账户和持仓...[/cyan]")
            positions, account_info = await self._fetch_account_data()

            # 展示账户状态
            notify_account_status(account_info)
            if positions:
                notify_positions(positions)

            # ----- 3. 风控检查（止损/止盈） -----
            console.print("[cyan]🛡️ 步骤3: 风控检查...[/cyan]")
            risk_actions = self._check_risk_control(positions)

            # ----- 4. 运行策略 -----
            console.print("[cyan]🧠 步骤4: 运行量化策略...[/cyan]")
            strategy_manager = self._ensure_strategy_manager()
            strategy_result = strategy_manager.run_all(positions, account_info)
            logger.info(strategy_result["summary"])

            # ----- 5. AI 建议 -----
            console.print("[cyan]🤖 步骤5: AI 分析决策...[/cyan]")
            if self.enable_ai:
                ai_advisor = self._ensure_ai_advisor()
                if ai_advisor:
                    advice = ai_advisor.generate_advice(
                        market_overview, positions, account_info, strategy_result
                    )
                else:
                    advice = self._strategy_to_advice(strategy_result, account_info)
            else:
                advice = self._strategy_to_advice(strategy_result, account_info)

            # 合并风控强制操作
            if risk_actions:
                advice.setdefault("sell", []).extend(risk_actions)

            cycle_result["advice"] = advice

            # ----- 6. 审核确认（Bug7修复：log_plan 移到确认之后，避免记录被取消的计划）-----
            if self.review_mode:
                console.print("[cyan]👀 步骤6: 等待人工审核...[/cyan]")
                confirmed = notify_trade_plan(advice)
                if not confirmed:
                    console.print("[yellow]⏸️ 交易已取消[/yellow]")
                    cycle_result["success"] = True
                    return cycle_result
            else:
                # Bug6修复：自动模式也展示交易计划，但不阻塞等待确认
                console.print("[yellow]🤖 自动模式，跳过审核直接执行[/yellow]")
                notify_trade_plan(advice)

            # ----- 7. 记录交易计划（已通过审核或自动模式）-----
            self.trade_logger.log_plan(advice)

            # ----- 8. 执行交易 -----
            console.print("[cyan]🚀 步骤7: 执行交易...[/cyan]")
            execution_results = await self._execute_trades(advice)
            cycle_result["trades_executed"] = execution_results

            # 记录执行结果
            for r in execution_results:
                self.trade_logger.log_trade(
                    action=r.get("action", "?"),
                    stock_code=r.get("code", "?"),
                    stock_name=r.get("name", ""),
                    amount=r.get("amount", 0),
                    price=r.get("price", 0),
                    total=r.get("amount", 0) * r.get("price", 0),
                    reason=r.get("message", ""),
                    status=r.get("status", "未知"),
                )

            # 展示结果
            if execution_results:
                notify_execution_result(execution_results)

            cycle_result["success"] = True
            console.print("\n[success]✅ 本轮交易分析完成[/success]\n")

        except Exception as e:
            logger.error(f"交易流程执行出错: {e}", exc_info=True)
            cycle_result["success"] = False

        return cycle_result

    # ============================================================
    # 数据获取辅助方法
    # ============================================================

    async def _fetch_account_data(self) -> tuple[list[dict], dict]:
        """
        获取持仓和账户信息

        Returns:
            (持仓列表, 账户信息字典)
        """
        positions = []
        account_info = {
            "total_assets": 1000000.0,
            "available_cash": 1000000.0,
            "market_value": 0.0,
            "profit": 0.0,
            "profit_pct": 0.0,
        }

        # 尝试从浏览器获取真实数据
        if self._page is not None:
            try:
                from core.browser_ops import get_current_positions, get_account_info

                positions = await get_current_positions(self._page)
                account_info = await get_account_info(self._page)
            except Exception as e:
                logger.warning(f"从浏览器获取账户数据失败: {e}")

        return positions, account_info

    # ============================================================
    # 风控检查
    # ============================================================

    def _check_risk_control(self, positions: list[dict]) -> list[dict]:
        """
        风控检查：检查持仓是否触发止损/止盈

        Args:
            positions: 当前持仓

        Returns:
            需要强制执行的卖出操作列表
        """
        forced_sells = []

        for pos in positions:
            code = pos.get("code", "")
            name = pos.get("name", "")
            profit_pct = pos.get("profit_pct", 0.0)

            # 止损检查
            if profit_pct <= self.stop_loss:
                logger.warning(f"⚠️ 触发止损: {code} {name}, 亏损 {profit_pct:.1f}%")
                forced_sells.append({
                    "code": code,
                    "name": name,
                    "amount": int(pos.get("available") or pos.get("amount") or 0),
                    "reason": f"🛑 触发止损线({self.stop_loss}%): 当前亏损{profit_pct:.1f}%",
                })

            # 止盈检查
            elif profit_pct >= self.take_profit:
                logger.info(f"📈 触发止盈: {code} {name}, 盈利 {profit_pct:.1f}%")
                forced_sells.append({
                    "code": code,
                    "name": name,
                    "amount": int(pos.get("available") or pos.get("amount") or 0),
                    "reason": f"📈 触发止盈线({self.take_profit}%): 当前盈利{profit_pct:.1f}%",
                })

        if forced_sells:
            logger.info(f"风控检查: {len(forced_sells)} 只股票需要强制操作")

        return forced_sells

    # ============================================================
    # 交易执行
    # ============================================================

    async def _execute_trades(self, advice: dict) -> list[dict]:
        """
        执行交易计划

        Args:
            advice: 交易建议字典（包含 sell 和 buy）

        Returns:
            执行结果列表
        """
        results = []

        # 确保浏览器可用
        if not await self._ensure_browser():
            logger.error("浏览器不可用，无法执行交易")
            return [{
                "action": "系统",
                "code": "-",
                "name": "-",
                "amount": 0,
                "price": 0,
                "status": "失败",
                "message": "浏览器连接失败",
            }]

        from core.browser_ops import buy_stock, sell_stock

        # 先执行卖出（释放资金）
        for item in advice.get("sell", []):
            code = item.get("code", "")
            if not code:
                continue

            try:
                sell_price = item.get("price") or None  # Bug1修复：传入 advice 指定的限价
                logger.info(f"执行卖出: {code} {item.get('name', '')}" + (f" @ {sell_price}" if sell_price else ""))
                result = await sell_stock(self._page, code, item.get("amount", 0), price=sell_price)
                results.append({
                    "action": "卖出",
                    "code": code,
                    "name": item.get("name", ""),
                    "amount": result.get("amount", 0),
                    "price": result.get("price", 0),
                    "status": "成功" if result.get("success") else "失败",
                    "message": result.get("message", ""),
                })
            except Exception as e:
                logger.error(f"卖出 {code} 执行异常: {e}")
                results.append({
                    "action": "卖出",
                    "code": code,
                    "name": item.get("name", ""),
                    "amount": 0,
                    "price": 0,
                    "status": "失败",
                    "message": str(e),
                })

        # 然后执行买入
        for item in advice.get("buy", []):
            code = item.get("code", "")
            amount = item.get("amount", 100)
            if not code:
                continue

            try:
                buy_price = item.get("price") or None  # Bug1修复：传入 advice 指定的限价
                logger.info(f"执行买入: {code} {item.get('name', '')} x {amount}股" + (f" @ {buy_price}" if buy_price else ""))
                result = await buy_stock(self._page, code, amount, price=buy_price)
                results.append({
                    "action": "买入",
                    "code": code,
                    "name": item.get("name", ""),
                    "amount": amount,
                    "price": result.get("price", 0),
                    "status": "成功" if result.get("success") else "失败",
                    "message": result.get("message", ""),
                })
            except Exception as e:
                logger.error(f"买入 {code} 执行异常: {e}")
                results.append({
                    "action": "买入",
                    "code": code,
                    "name": item.get("name", ""),
                    "amount": amount,
                    "price": 0,
                    "status": "失败",
                    "message": str(e),
                })

        return results

    # ============================================================
    # 策略信号转建议（回退用）
    # ============================================================

    def _strategy_to_advice(
        self, strategy_result: dict, account_info: dict
    ) -> dict:
        """
        将策略信号直接转换为交易建议（不经过AI）

        Args:
            strategy_result: 策略管理器输出
            account_info: 账户信息

        Returns:
            交易建议字典
        """
        from core.ai_advisor import AIAdvisor

        # Bug8修复：直接调用静态方法，不再通过 __new__ 创建裸实例
        return AIAdvisor._fallback_to_strategy(
            [], account_info, strategy_result
        )

    # ============================================================
    # ★ 三 Agent 整合接口：从 advice 文件执行
    # ============================================================

    async def run_from_advice_file(
        self,
        advice_path: str | None = None,
    ) -> dict:
        """
        读取 AgentA 输出的 advice.json，走
          risk 护栏 → 人工审核 → Playwright 执行
        完全跳过策略分析和 AI 决策层。

        文件格式（agentA 写入）:
        {
            "sell": [{"code": "000333", "name": "美的集团", "reason": "止损"}],
            "buy":  [{"code": "601138", "name": "工业富联",
                       "amount": 900, "price": 75.0, "reason": "AgentB推荐"}],
            "reasoning": "三 Agent 汇总决策..."
        }

        Args:
            advice_path: advice.json 文件路径，默认 <project>/advice.json

        Returns:
            执行结果字典
        """
        import json as _json
        from pathlib import Path as _Path

        # 默认路径：项目根目录下的 advice.json
        if advice_path is None:
            advice_path = str(PROJECT_ROOT.parent / "advice.json")

        cycle_result = {
            "timestamp": datetime.now().isoformat(),
            "trades_executed": [],
            "advice": {},
            "success": False,
            "source": "agent_advice_file",
        }

        # ── 读取文件 ──────────────────────────────────────────────
        path = _Path(advice_path)
        if not path.exists():
            console.print(f"[error]❌ 未找到 advice 文件: {path}[/error]")
            console.print(
                "[dim]请让 AgentA 把今日决策写入该文件后再运行。[/dim]\n"
                "[dim]文件格式参考: new_trading_bot/advice_template.json[/dim]"
            )
            return cycle_result

        try:
            with open(path, encoding="utf-8") as f:
                raw_advice = _json.load(f)
            logger.info(f"已读取 advice 文件: {path}")
        except Exception as e:
            console.print(f"[error]❌ advice 文件解析失败: {e}[/error]")
            return cycle_result

        raw_advice.setdefault("source", "agent_advice_file")

        console.rule("[header]📋 三 Agent 决策执行模式[/header]", style="magenta")
        console.print(f"[dim]读取文件: {path}[/dim]")
        console.print(
            f"  卖出: {len(raw_advice.get('sell', []))} 只  "
            f"买入: {len(raw_advice.get('buy', []))} 只"
        )
        if raw_advice.get("reasoning"):
            console.print(f"  决策摘要: [dim]{raw_advice['reasoning']}[/dim]")
        console.print()

        try:
            # ── 1. 获取账户 & 持仓（用于 risk 护栏计算） ─────────────
            console.print("[cyan]💰 步骤1: 获取账户持仓...[/cyan]")
            # BugC修复：检查浏览器初始化结果，失败时优雅退出而非崩溃
            browser_ready = await self._ensure_browser()
            if not browser_ready:
                console.print(
                    "[error]❌ 浏览器未就绪（Cookie 可能已过期）。\n"
                    "   请运行 `python main.py --login` 重新登录后再试。[/error]"
                )
                cycle_result["success"] = False
                return cycle_result

            positions, account_info = await self._fetch_account_data()
            notify_account_status(account_info)
            if positions:
                notify_positions(positions)
            else:
                # 空持仓时风控仍有效：买入上限、资金比例等护栏正常工作；
                # 但卖出订单若无持仓数据将被 normalize_sell_amount 拦截为 0 股
                logger.info("当前持仓为空，卖出订单将被风控拦截（无可卖数量）")

            # ── 2. risk 护栏过滤 ──────────────────────────────────────
            console.print("[cyan]🛡️  步骤2: risk 护栏验证...[/cyan]")
            from core.risk import normalize_trade_plan
            normalized = normalize_trade_plan(
                raw_advice, positions, account_info, self.risk_rules
            )

            # 展示护栏结果
            if normalized["blocked"]:
                console.print("[warning]⚠️  以下订单被护栏拦截:[/warning]")
                for blk in normalized["blocked"]:
                    console.print(
                        f"   [red]✗[/red] {blk['action'].upper()} "
                        f"{blk['code']} — {blk['reason']}"
                    )
            console.print(f"[dim]{normalized['risk_summary']}[/dim]\n")

            # ── 3. 叠加盘中止损/止盈强制卖出 ─────────────────────────
            console.print("[cyan]🛡️  步骤3: 止损/止盈检查...[/cyan]")
            risk_sells = self._check_risk_control(positions)
            if risk_sells:
                # 避免重复：跳过 advice 里已有的卖出代码
                existing_sell_codes = {s["code"] for s in normalized["sell"]}
                for rs in risk_sells:
                    if rs["code"] not in existing_sell_codes:
                        normalized["sell"].append(rs)
                        console.print(
                            f"[warning]  ↳ 强制加入止损/止盈: "
                            f"{rs['code']} {rs.get('name','')} — {rs['reason']}[/warning]"
                        )

            cycle_result["advice"] = normalized

            # 如果护栏后无任何操作，提前退出
            if not normalized["sell"] and not normalized["buy"]:
                console.print(
                    "[yellow]⏸️  护栏过滤后无可执行操作，本次跳过。[/yellow]"
                )
                cycle_result["success"] = True
                return cycle_result

            # ── 4. 记录计划 ───────────────────────────────────────────
            self.trade_logger.log_plan(normalized)

            # ── 5. 人工审核（与标准流程相同） ─────────────────────────
            if self.review_mode:
                console.print("[cyan]👀 步骤4: 等待人工审核...[/cyan]")
                confirmed = notify_trade_plan(normalized)
                if not confirmed:
                    console.print("[yellow]⏸️  交易已取消[/yellow]")
                    cycle_result["success"] = True
                    return cycle_result

            # ── 6. 执行交易 ───────────────────────────────────────────
            console.print("[cyan]🚀 步骤5: 执行交易...[/cyan]")
            execution_results = await self._execute_trades(normalized)
            cycle_result["trades_executed"] = execution_results

            for r in execution_results:
                self.trade_logger.log_trade(
                    action=r.get("action", "?"),
                    stock_code=r.get("code", "?"),
                    stock_name=r.get("name", ""),
                    amount=r.get("amount", 0),
                    price=r.get("price", 0),
                    total=r.get("amount", 0) * r.get("price", 0),
                    reason=r.get("message", ""),
                    status=r.get("status", "未知"),
                )

            if execution_results:
                notify_execution_result(execution_results)

            # ── 7. 执行成功后把文件重命名为 .done，防止重复触发 ──────
            done_path = path.with_suffix(".done.json")
            try:
                path.replace(done_path)
            except Exception as e:
                logger.warning(f"归档 advice 文件失败: {e}")
            console.print(f"[dim]advice 文件已归档为: {done_path.name}[/dim]")

            cycle_result["success"] = True
            console.print("\n[success]✅ 三 Agent 决策执行完成[/success]\n")

        except Exception as e:
            logger.error(f"三 Agent 决策执行出错: {e}", exc_info=True)
            cycle_result["success"] = False

        return cycle_result

    # ============================================================
    # 会话运行方法
    # ============================================================

    async def run_morning_session(self) -> None:
        """
        早盘会话

        流程：
        - 盘前数据分析
        - 开盘交易执行
        """
        console.print("\n")
        console.rule("[header]🌅 早盘交易会话[/header]", style="magenta")
        console.print(f"[dim]时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]\n")

        # 确保浏览器就绪
        if not await self._ensure_browser():
            console.print("[error]❌ 浏览器初始化失败，早盘会话中止[/error]")
            return

        # 运行完整分析流程
        result = await self.run_full_cycle()

        if result["success"]:
            console.print("[success]✅ 早盘会话完成[/success]")
        else:
            console.print("[error]❌ 早盘会话执行异常[/error]")

    async def run_monitoring(self) -> None:
        """
        盘中监控

        定时检查持仓状态和市场变化，
        在触发风控条件时发出提醒或自动操作。
        """
        console.print("\n")
        console.rule("[header]👁️ 盘中监控[/header]", style="cyan")
        console.print(f"[dim]时间: {datetime.now().strftime('%H:%M:%S')}[/dim]\n")

        try:
            # 获取最新持仓状态
            positions, account_info = await self._fetch_account_data()

            # 展示状态
            notify_account_status(account_info)
            if positions:
                notify_positions(positions)

            # 风控检查
            risk_actions = self._check_risk_control(positions)

            if risk_actions:
                console.print("[warning]⚠️ 检测到风控触发信号！[/warning]")
                # 如果有风控触发，运行完整流程
                await self.run_full_cycle()
            else:
                console.print("[dim]📊 持仓状态正常，无需操作[/dim]")

        except Exception as e:
            logger.error(f"盘中监控出错: {e}")

    async def run_closing(self) -> None:
        """
        尾盘总结

        汇总当日交易记录、盈亏情况。
        """
        console.print("\n")
        console.rule("[header]🌆 日终总结[/header]", style="magenta")
        console.print(f"[dim]时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]\n")

        try:
            # 获取最终账户状态
            positions, account_info = await self._fetch_account_data()

            # 获取排名
            ranking_info = {}
            if self._page:
                from core.browser_ops import get_ranking
                ranking_info = await get_ranking(self._page)

            # 汇总信息
            summary = {
                "日期": datetime.now().strftime("%Y-%m-%d"),
                "总资产": f"¥{account_info.get('total_assets', 0):,.2f}",
                "可用资金": f"¥{account_info.get('available_cash', 0):,.2f}",
                "持仓市值": f"¥{account_info.get('market_value', 0):,.2f}",
                "当日盈亏": f"{account_info.get('profit_pct', 0):+.2f}%",
                "当前排名": f"第{ranking_info.get('rank', '?')}名",
                "持仓数量": f"{len(positions)}只",
            }

            # 展示和记录
            notify_account_status({**account_info, "ranking": ranking_info.get("rank", "?")})
            if positions:
                notify_positions(positions)
            self.trade_logger.log_summary(summary)

            console.print("[success]✅ 日终总结完成[/success]")

        except Exception as e:
            logger.error(f"日终总结出错: {e}")

    # ============================================================
    # 交易时间判断
    # ============================================================

    @staticmethod
    def is_trading_time() -> bool:
        """
        判断当前是否在交易时间段内

        Returns:
            bool - 是否在交易时间
        """
        now = datetime.now().time()

        # 上午交易时段 9:25 - 11:30
        morning_start = dt_time(9, 25)
        morning_end = dt_time(11, 30)

        # 下午交易时段 13:00 - 15:00
        afternoon_start = dt_time(13, 0)
        afternoon_end = dt_time(15, 0)

        is_morning = morning_start <= now <= morning_end
        is_afternoon = afternoon_start <= now <= afternoon_end

        return is_morning or is_afternoon

    @staticmethod
    def is_weekday() -> bool:
        """
        判断今天是否为工作日（周一至周五）

        Returns:
            bool - 是否工作日
        """
        return datetime.now().weekday() < 5

    # ============================================================
    # 主运行循环
    # ============================================================

    async def run_trading_loop(self) -> None:
        """
        主交易循环

        在交易日的交易时间段内，按设定间隔运行监控。
        """
        config = self.config
        check_interval = config.get("mode", {}).get("check_interval_minutes", 5)

        console.print(f"\n[header]🔄 交易循环已启动[/header]")
        console.print(f"[dim]检查间隔: {check_interval} 分钟[/dim]")
        console.print(f"[dim]模式: {'审核' if self.review_mode else '自动'}[/dim]\n")

        try:
            # 首次运行早盘分析
            first_run = True
            closing_done = False  # Bug16修复：记录今日是否已执行日终总结

            while True:
                if not self.is_weekday():
                    console.print("[dim]今天是周末，跳过交易[/dim]")
                    await asyncio.sleep(3600)  # 1小时后重试
                    continue

                if self.is_trading_time():
                    closing_done = False  # 进入交易时段，重置日终标志
                    if first_run:
                        await self.run_morning_session()
                        first_run = False
                    else:
                        await self.run_monitoring()
                else:
                    now = datetime.now().time()
                    # Bug16修复：只要过了收盘时间（>=15:00）且今日未做总结，就执行一次
                    if now >= dt_time(15, 0) and not closing_done:
                        await self.run_closing()
                        closing_done = True
                        console.print("[dim]今日交易结束，程序退出[/dim]")
                        break

                    console.print(f"[dim]{now.strftime('%H:%M')} - 非交易时间，等待中...[/dim]")

                # 等待指定间隔
                await asyncio.sleep(check_interval * 60)

        except KeyboardInterrupt:
            console.print("\n[yellow]⚠️ 用户中断，正在清理...[/yellow]")
        finally:
            await self._cleanup_browser()

    # ============================================================
    # 清理
    # ============================================================

    async def cleanup(self) -> None:
        """清理所有资源"""
        await self._cleanup_browser()
        logger.info("执行器资源已清理")


# ============================================================
# 模块自测
# ============================================================
if __name__ == "__main__":
    async def _test():
        """测试执行器基本功能（不连接浏览器）"""
        console.print("[header]🚀 执行器模块测试[/header]\n")

        executor = TradeExecutor(review_mode=True, enable_ai=False)

        # 测试时间判断
        console.print(f"当前是否交易时间: {executor.is_trading_time()}")
        console.print(f"当前是否工作日: {executor.is_weekday()}")

        # 测试配置加载
        config = executor.config
        console.print(f"止损线: {executor.stop_loss}%")
        console.print(f"止盈线: {executor.take_profit}%")
        console.print(f"最大持仓: {executor.max_stocks}只")

        # 测试风控
        test_positions = [
            {"code": "000001", "name": "平安银行", "profit_pct": -6.0},
            {"code": "600519", "name": "贵州茅台", "profit_pct": 9.0},
            {"code": "300750", "name": "宁德时代", "profit_pct": 2.0},
        ]
        risk_actions = executor._check_risk_control(test_positions)
        console.print(f"\n风控触发: {len(risk_actions)} 只")
        for action in risk_actions:
            console.print(f"  - {action['code']} {action['name']}: {action['reason']}")

        console.print("\n[success]✅ 执行器模块测试完成[/success]")

    asyncio.run(_test())
