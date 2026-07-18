"""
AI 顾问模块 - 基于 LLM 的智能交易决策引擎

功能：
- 整合市场数据、持仓信息、策略信号，生成结构化交易建议
- 支持 OpenAI / DeepSeek 等兼容 API
- LLM 不可用时自动回退到纯策略信号
- 结构化 JSON 输出

输出格式：
{
    "sell": [{"code": "...", "name": "...", "reason": "..."}],
    "buy": [{"code": "...", "name": "...", "amount": N, "reason": "..."}],
    "reasoning": "整体分析逻辑"
}
"""

import json
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from utils.logger import get_logger, console

# ============================================================
# 加载环境变量
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / "config" / ".env"

# 优先加载 config/.env，其次尝试根目录 .env
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)
else:
    load_dotenv(PROJECT_ROOT / ".env", override=False)

logger = get_logger("ai_advisor")


# ============================================================
# 系统提示词
# ============================================================

SYSTEM_PROMPT = """你是一位经验丰富的A股短线交易专家，专注于模拟盘比赛（初始资金100万元）。

你的任务是根据当前市场数据、持仓情况和策略信号，给出具体的交易建议。

## 你的决策原则：
1. **风险控制第一**：单只股票仓位不超过总资产的50%，最多持有3只股票
2. **止损纪律**：亏损超过5%必须建议卖出
3. **止盈策略**：盈利超过8%考虑分批止盈
4. **追涨谨慎**：不追已涨停的股票，优先选择有回调空间的标的
5. **板块轮动**：关注资金从哪个板块流出，流入哪个板块
6. **短线为主**：持股周期1-5天为主，不做长线价值投资

## 你需要考虑的因素：
- 大盘整体走势和情绪（通过涨停数量判断）
- 板块热度和资金流向
- 个股技术面（均线、成交量）
- 策略信号的一致性（多个策略同时看好的更可靠）
- 当前持仓的盈亏状态

## 输出要求：
你必须输出严格的 JSON 格式，不要包含其他文字：
```json
{
    "sell": [
        {"code": "股票代码", "name": "股票名称", "reason": "卖出原因"}
    ],
    "buy": [
        {"code": "股票代码", "name": "股票名称", "amount": 买入股数(100的整数倍), "reason": "买入原因"}
    ],
    "reasoning": "整体分析逻辑和决策思路（简明扼要，100字以内）"
}
```

如果没有操作建议，sell 和 buy 数组留空即可。
买入数量必须是100的整数倍，根据可用资金和股价合理计算。
"""


# ============================================================
# AI 顾问类
# ============================================================

class AIAdvisor:
    """
    AI 交易顾问

    利用 LLM 分析市场数据和策略信号，
    生成结构化的交易建议。
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.3,
    ):
        """
        初始化 AI 顾问

        Args:
            api_key: API 密钥（不传则从环境变量读取）
            base_url: API Base URL（不传则从环境变量读取）
            model: 模型名称（不传则从环境变量读取）
            temperature: 生成温度（越低越稳定）
        """
        # 从环境变量获取配置（支持 OpenAI 和 DeepSeek）
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or ""
        self.base_url = base_url or os.getenv("BASE_URL") or None
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o")
        self.temperature = float(os.getenv("LLM_TEMPERATURE", str(temperature)))

        # 检查 API Key 是否可用
        self.available = bool(self.api_key and not self.api_key.startswith("sk-your-"))

        if self.available:
            logger.info(f"AI 顾问已初始化: model={self.model}, base_url={self.base_url or '默认'}")
        else:
            logger.warning("AI 顾问不可用（未配置 API Key），将使用纯策略信号回退模式")

        self._llm = None

    def _get_llm(self):
        """
        延迟初始化 LLM 客户端

        Returns:
            ChatOpenAI 实例
        """
        if self._llm is None and self.available:
            try:
                from langchain_openai import ChatOpenAI

                kwargs = {
                    "model": self.model,
                    "api_key": self.api_key,
                    "temperature": self.temperature,
                    "max_tokens": 2000,
                }
                if self.base_url:
                    kwargs["base_url"] = self.base_url

                self._llm = ChatOpenAI(**kwargs)
                logger.info("LLM 客户端初始化成功")

            except Exception as e:
                logger.error(f"LLM 客户端初始化失败: {e}")
                self.available = False

        return self._llm

    def generate_advice(
        self,
        market_overview: dict,
        positions: list[dict],
        account_info: dict,
        strategy_signals: dict,
    ) -> dict:
        """
        生成交易建议

        Args:
            market_overview: 市场概览数据（来自 data_fetcher.get_market_overview()）
            positions: 当前持仓列表
            account_info: 账户信息
            strategy_signals: 策略信号（来自 StrategyManager.run_all()）

        Returns:
            交易建议字典：
            {
                "sell": [...],
                "buy": [...],
                "reasoning": "...",
                "source": "ai" / "strategy_fallback"
            }
        """
        if self.available:
            try:
                return self._generate_with_llm(
                    market_overview, positions, account_info, strategy_signals
                )
            except Exception as e:
                logger.error(f"AI 生成建议失败，回退到策略信号: {e}")

        # 回退：直接使用策略信号
        return self._fallback_to_strategy(positions, account_info, strategy_signals)

    def _generate_with_llm(
        self,
        market_overview: dict,
        positions: list[dict],
        account_info: dict,
        strategy_signals: dict,
    ) -> dict:
        """
        使用 LLM 生成交易建议

        Args:
            market_overview: 市场概览
            positions: 持仓
            account_info: 账户信息
            strategy_signals: 策略信号

        Returns:
            交易建议字典
        """
        llm = self._get_llm()
        if llm is None:
            return self._fallback_to_strategy(positions, account_info, strategy_signals)

        # 构建用户消息（将所有数据整理成文本）
        user_message = self._build_user_message(
            market_overview, positions, account_info, strategy_signals
        )

        logger.info("正在调用 LLM 生成交易建议...")
        logger.debug(f"用户消息长度: {len(user_message)} 字符")

        try:
            from langchain_core.messages import SystemMessage, HumanMessage

            messages = [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=user_message),
            ]

            response = llm.invoke(messages)
            content = response.content.strip()

            logger.debug(f"LLM 原始回复: {content[:500]}...")

            # 解析 JSON 输出
            advice = self._parse_llm_response(content)
            advice["source"] = "ai"

            logger.info(
                f"AI 建议: 卖出{len(advice.get('sell', []))}只, "
                f"买入{len(advice.get('buy', []))}只"
            )

            return advice

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            raise

    def _build_user_message(
        self,
        market_overview: dict,
        positions: list[dict],
        account_info: dict,
        strategy_signals: dict,
    ) -> str:
        """
        构建发送给 LLM 的用户消息

        将各种数据源整理成结构化的文本描述。

        Args:
            market_overview: 市场概览
            positions: 持仓
            account_info: 账户
            strategy_signals: 策略信号

        Returns:
            格式化的用户消息文本
        """
        parts = []

        # ----- 1. 账户信息 -----
        parts.append("## 当前账户状态")
        parts.append(f"- 总资产: ¥{account_info.get('total_assets', 0):,.2f}")
        parts.append(f"- 可用资金: ¥{account_info.get('available_cash', 0):,.2f}")
        parts.append(f"- 持仓市值: ¥{account_info.get('market_value', 0):,.2f}")
        profit_pct = account_info.get('profit_pct', 0)
        parts.append(f"- 总盈亏: {'+' if profit_pct >= 0 else ''}{profit_pct:.2f}%")
        parts.append("")

        # ----- 2. 当前持仓 -----
        parts.append("## 当前持仓")
        if positions:
            for pos in positions:
                pct = pos.get('profit_pct', 0)
                parts.append(
                    f"- {pos.get('code', '?')} {pos.get('name', '?')}: "
                    f"{pos.get('amount', 0)}股, 成本{pos.get('cost_price', 0):.2f}, "
                    f"现价{pos.get('current_price', 0):.2f}, "
                    f"盈亏{'+' if pct >= 0 else ''}{pct:.1f}%"
                )
        else:
            parts.append("- 当前空仓")
        parts.append("")

        # ----- 3. 市场概览 -----
        parts.append("## 今日市场概况")
        parts.append(f"- 市场情绪: {market_overview.get('market_sentiment', '未知')}")
        parts.append(f"- 涨停数量: {market_overview.get('limit_up_count', 0)}只")

        hot_sectors = market_overview.get("hot_sectors", [])
        if hot_sectors:
            parts.append("- 热门板块:")
            for s in hot_sectors[:5]:
                name = s.get("sector_name", s.get("name", "?"))
                change = s.get("change_pct", 0)
                parts.append(f"  - {name}: {'+' if change >= 0 else ''}{change:.2f}%")
        parts.append("")

        # ----- 4. 热门股票 -----
        hot_stocks = market_overview.get("hot_stocks", [])
        if hot_stocks:
            parts.append("## 今日热门股票（成交额前10）")
            for s in hot_stocks[:10]:
                code = s.get("code", "?")
                name = s.get("name", "?")
                price = s.get("price", 0)
                change = s.get("change_pct", 0)
                parts.append(f"- {code} {name}: ¥{price:.2f}, {'+' if change >= 0 else ''}{change:.2f}%")
            parts.append("")

        # ----- 5. 策略信号 -----
        parts.append("## 量化策略信号")
        parts.append(f"策略摘要: {strategy_signals.get('summary', '无')}")

        buy_signals = strategy_signals.get("buy_signals", [])
        if buy_signals:
            parts.append("\n买入信号:")
            for sig in buy_signals[:8]:
                sig_dict = sig.to_dict() if hasattr(sig, 'to_dict') else sig
                parts.append(
                    f"- {sig_dict.get('stock_code', '?')} {sig_dict.get('stock_name', '?')}: "
                    f"置信度{sig_dict.get('confidence', 0):.2f}, "
                    f"来源[{sig_dict.get('strategy', '?')}], "
                    f"原因: {sig_dict.get('reason', '?')}"
                )

        sell_signals = strategy_signals.get("sell_signals", [])
        if sell_signals:
            parts.append("\n卖出信号:")
            for sig in sell_signals[:5]:
                sig_dict = sig.to_dict() if hasattr(sig, 'to_dict') else sig
                parts.append(
                    f"- {sig_dict.get('stock_code', '?')} {sig_dict.get('stock_name', '?')}: "
                    f"置信度{sig_dict.get('confidence', 0):.2f}, "
                    f"原因: {sig_dict.get('reason', '?')}"
                )

        parts.append("")
        parts.append("请根据以上信息，给出今天的交易建议（JSON格式）。")

        return "\n".join(parts)

    def _parse_llm_response(self, content: str) -> dict:
        """
        解析 LLM 的 JSON 响应

        处理可能包含 markdown 代码块的情况。

        Args:
            content: LLM 原始响应文本

        Returns:
            解析后的交易建议字典
        """
        # 尝试提取 JSON 块（处理 ```json ... ``` 包裹的情况）
        json_str = content

        # 移除 markdown 代码块标记
        if "```json" in json_str:
            start = json_str.find("```json") + 7
            end = json_str.find("```", start)
            if end > start:
                json_str = json_str[start:end]
        elif "```" in json_str:
            start = json_str.find("```") + 3
            end = json_str.find("```", start)
            if end > start:
                json_str = json_str[start:end]

        json_str = json_str.strip()

        try:
            advice = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失败: {e}")
            logger.error(f"原始内容: {json_str[:300]}")

            # 尝试修复常见问题
            # 移除尾部逗号
            import re
            json_str = re.sub(r",\s*}", "}", json_str)
            json_str = re.sub(r",\s*\]", "]", json_str)

            try:
                advice = json.loads(json_str)
            except json.JSONDecodeError:
                # 完全无法解析，返回空建议
                return {
                    "sell": [],
                    "buy": [],
                    "reasoning": f"LLM 输出格式异常，无法解析: {content[:100]}",
                }

        # 确保必要字段存在
        advice.setdefault("sell", [])
        advice.setdefault("buy", [])
        advice.setdefault("reasoning", "")

        # 验证买入数量为100的整数倍
        for item in advice["buy"]:
            if "amount" in item:
                amount = int(item["amount"])
                item["amount"] = max(100, (amount // 100) * 100)

        return advice

    @staticmethod
    def _fallback_to_strategy(
        positions: list[dict],
        account_info: dict,
        strategy_signals: dict,
    ) -> dict:
        """
        回退模式：直接将策略信号转换为交易建议（Bug8修复：改为staticmethod，不再依赖实例属性）

        当 LLM 不可用时，使用纯量化策略信号作为决策依据。

        Args:
            positions: 持仓
            account_info: 账户信息
            strategy_signals: 策略信号

        Returns:
            交易建议字典
        """
        from utils.logger import get_logger as _get_logger
        _logger = _get_logger("ai_advisor.fallback")
        _logger.info("使用策略信号回退模式生成建议")

        advice = {
            "sell": [],
            "buy": [],
            "reasoning": "AI顾问不可用，以下建议来自纯量化策略信号",
            "source": "strategy_fallback",
        }

        available_cash = account_info.get("available_cash", 0)

        # 卖出信号转换
        for sig in strategy_signals.get("sell_signals", []):
            sig_dict = sig.to_dict() if hasattr(sig, "to_dict") else sig
            if sig_dict.get("confidence", 0) >= 0.5:  # 只取置信度较高的
                advice["sell"].append({
                    "code": sig_dict.get("stock_code", ""),
                    "name": sig_dict.get("stock_name", ""),
                    "reason": sig_dict.get("reason", "策略卖出信号"),
                })

        # 买入信号转换（需要计算买入数量）
        for sig in strategy_signals.get("buy_signals", []):
            sig_dict = sig.to_dict() if hasattr(sig, "to_dict") else sig
            if sig_dict.get("confidence", 0) >= 0.4:

                # 获取实时价格来计算买入数量
                from core.data_fetcher import get_stock_realtime
                realtime = get_stock_realtime(sig_dict.get("stock_code", ""))
                price = realtime.get("price", 0)

                if price > 0 and available_cash > price * 100:
                    # 分配资金：可用资金的30%给单只股票
                    budget = available_cash * 0.3
                    amount = int(budget / price / 100) * 100  # 取整到100股
                    amount = max(100, amount)

                    advice["buy"].append({
                        "code": sig_dict.get("stock_code", ""),
                        "name": sig_dict.get("stock_name", ""),
                        "amount": amount,
                        "reason": sig_dict.get("reason", "策略买入信号"),
                    })

                    available_cash -= amount * price  # 扣除预算

        return advice


# ============================================================
# 模块自测
# ============================================================
if __name__ == "__main__":
    console.print("[header]🤖 AI 顾问模块测试[/header]\n")

    advisor = AIAdvisor()

    # 模拟数据
    test_market = {
        "hot_stocks": [
            {"code": "000001", "name": "平安银行", "price": 12.5, "change_pct": 3.2},
            {"code": "600519", "name": "贵州茅台", "price": 1850, "change_pct": 1.5},
        ],
        "limit_up_count": 25,
        "hot_sectors": [
            {"sector_name": "人工智能", "change_pct": 3.5},
            {"sector_name": "新能源", "change_pct": 2.1},
        ],
        "market_sentiment": "偏多",
    }

    test_positions = []
    test_account = {
        "total_assets": 1000000,
        "available_cash": 1000000,
        "market_value": 0,
        "profit": 0,
        "profit_pct": 0,
    }

    test_signals = {
        "buy_signals": [],
        "sell_signals": [],
        "summary": "测试模式",
    }

    # 生成建议
    advice = advisor.generate_advice(
        test_market, test_positions, test_account, test_signals
    )

    console.print(f"建议来源: {advice.get('source', '?')}")
    console.print(f"卖出建议: {len(advice.get('sell', []))} 只")
    console.print(f"买入建议: {len(advice.get('buy', []))} 只")
    console.print(f"分析说明: {advice.get('reasoning', '')}")

    console.print("\n[success]✅ AI 顾问模块测试完成[/success]")
