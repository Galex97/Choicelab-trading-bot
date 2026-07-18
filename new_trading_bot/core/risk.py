"""
Execution risk guard for Agent A.

This module is deliberately boring: it does not predict markets. It only
normalizes a proposed trade plan and blocks orders that violate portfolio
budget, lot-size, or cash rules before Playwright can submit them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.data_fetcher import get_stock_realtime
from utils.logger import get_logger

logger = get_logger("risk")


@dataclass(frozen=True)
class RiskRules:
    max_single_position_ratio: float = 0.33
    max_total_position_ratio: float = 0.55
    min_cash_ratio: float = 0.30
    max_positions: int = 6
    min_buy_amount: int = 100
    lot_size: int = 100
    star_market_min_buy: int = 200
    allow_chinext: bool = False
    allow_star_market: bool = True
    allow_beijing: bool = False


def load_risk_rules(config: dict[str, Any]) -> RiskRules:
    risk_config = config.get("risk", {})
    strategy_config = config.get("strategy", {})
    return RiskRules(
        max_single_position_ratio=float(
            risk_config.get(
                "max_single_position_ratio",
                strategy_config.get("max_position_ratio", 0.33),
            )
        ),
        max_total_position_ratio=float(risk_config.get("max_total_position_ratio", 0.55)),
        min_cash_ratio=float(risk_config.get("min_cash_ratio", 0.30)),
        max_positions=int(risk_config.get("max_positions", strategy_config.get("max_stocks", 6))),
        min_buy_amount=int(risk_config.get("min_buy_amount", 100)),
        lot_size=int(risk_config.get("lot_size", 100)),
        star_market_min_buy=int(risk_config.get("star_market_min_buy", 200)),
        allow_chinext=bool(risk_config.get("allow_chinext", False)),
        allow_star_market=bool(risk_config.get("allow_star_market", True)),
        allow_beijing=bool(risk_config.get("allow_beijing", False)),
    )


def normalize_trade_plan(
    advice: dict[str, Any],
    positions: list[dict[str, Any]],
    account_info: dict[str, Any],
    rules: RiskRules,
) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "sell": [],
        "buy": [],
        "reasoning": advice.get("reasoning", ""),
        "source": advice.get("source", "unknown"),
        "blocked": [],
        "risk_summary": "",
    }

    position_by_code = {str(p.get("code", "")): p for p in positions}
    total_assets = float(account_info.get("total_assets") or 0) or 1_000_000.0
    cash = float(account_info.get("available_cash") or 0)
    market_value = float(account_info.get("market_value") or 0)

    if market_value <= 0 and positions:
        market_value = sum(float(p.get("market_value") or 0) for p in positions)

    # Sells go through first and increase the estimated cash budget.
    for item in advice.get("sell", []):
        code = str(item.get("code", "")).strip()
        if not code:
            continue
        pos = position_by_code.get(code, {})
        amount = _normalize_sell_amount(item.get("amount"), pos)
        if amount <= 0:
            _block(normalized, code, "sell", "no sellable amount was found")
            continue

        price = _estimate_price(code, item, pos)
        cash += amount * price
        market_value = max(0.0, market_value - amount * price)
        normalized["sell"].append({**item, "code": code, "amount": amount, "price": price})

    held_codes_after_sell = {
        code
        for code, pos in position_by_code.items()
        if int(pos.get("amount") or 0) > sum(
            int(s.get("amount") or 0) for s in normalized["sell"] if s.get("code") == code
        )
    }

    # Buys are admitted only if the portfolio remains inside the hard budget.
    for item in advice.get("buy", []):
        code = str(item.get("code", "")).strip()
        if not code:
            continue

        allowed, reason = _is_board_allowed(code, rules)
        if not allowed:
            _block(normalized, code, "buy", reason)
            continue

        price = _estimate_price(code, item, {})
        if price <= 0:
            _block(normalized, code, "buy", "missing usable price")
            continue

        amount = _normalize_buy_amount(item.get("amount"), code, rules)
        if amount <= 0:
            _block(normalized, code, "buy", "invalid buy amount")
            continue

        cost = amount * price
        current_value = float(position_by_code.get(code, {}).get("market_value") or 0)
        if current_value + cost > total_assets * rules.max_single_position_ratio:
            _block(normalized, code, "buy", "single-position limit exceeded")
            continue

        if market_value + cost > total_assets * rules.max_total_position_ratio:
            _block(normalized, code, "buy", "total-position limit exceeded")
            continue

        if cash - cost < total_assets * rules.min_cash_ratio:
            _block(normalized, code, "buy", "cash floor would be violated")
            continue

        new_position_count = len(held_codes_after_sell | {b["code"] for b in normalized["buy"]} | {code})
        if code not in held_codes_after_sell and new_position_count > rules.max_positions:
            _block(normalized, code, "buy", "max position count exceeded")
            continue

        cash -= cost
        market_value += cost
        normalized["buy"].append({**item, "code": code, "amount": amount, "price": price})

    normalized["risk_summary"] = (
        f"Risk guard: {len(normalized['sell'])} sell, {len(normalized['buy'])} buy, "
        f"{len(normalized['blocked'])} blocked. Est. cash {cash:,.2f}, "
        f"est. position ratio {market_value / total_assets:.1%}."
    )
    if normalized["blocked"]:
        logger.warning(normalized["risk_summary"])
    else:
        logger.info(normalized["risk_summary"])

    return normalized


def _normalize_sell_amount(raw_amount: Any, position: dict[str, Any]) -> int:
    try:
        amount = int(float(raw_amount))
    except (TypeError, ValueError):
        amount = 0
    if amount > 0:
        return amount
    return int(position.get("available") or position.get("amount") or 0)


def _normalize_buy_amount(raw_amount: Any, code: str, rules: RiskRules) -> int:
    try:
        amount = int(float(raw_amount))
    except (TypeError, ValueError):
        return 0
    amount = (amount // rules.lot_size) * rules.lot_size
    minimum = rules.star_market_min_buy if code.startswith("68") else rules.min_buy_amount
    return amount if amount >= minimum else 0


def _estimate_price(code: str, item: dict[str, Any], position: dict[str, Any]) -> float:
    for value in (item.get("price"), position.get("current_price"), position.get("cost_price")):
        try:
            price = float(value)
            if price > 0:
                return price
        except (TypeError, ValueError):
            pass
    realtime = get_stock_realtime(code)
    try:
        return float(realtime.get("price") or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_board_allowed(code: str, rules: RiskRules) -> tuple[bool, str]:
    if code.startswith("30") and not rules.allow_chinext:
        return False, "ChiNext/GEM board is disabled by risk rules"
    if code.startswith("68") and not rules.allow_star_market:
        return False, "STAR Market is disabled by risk rules"
    if code.startswith(("4", "8", "92")) and not rules.allow_beijing:
        return False, "Beijing Stock Exchange is disabled by risk rules"
    return True, ""


def _block(plan: dict[str, Any], code: str, action: str, reason: str) -> None:
    plan["blocked"].append({"code": code, "action": action, "reason": reason})
    logger.warning(f"Blocked {action} {code}: {reason}")
