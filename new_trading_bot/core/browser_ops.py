"""
浏览器操作模块 - 东方财富A股争霸赛页面自动化操作

功能：
- 导航到比赛交易页面
- 股票搜索、买入、卖出
- 获取持仓、账户信息、排名
- 页面结构分析（用于调试和适配选择器）

页面结构要点（基于实际 DOM 分析）：
    - 左侧导航栏：买入 / 卖出 / 撤单 / 查询（含子菜单：资金股份, 当日成交 等）
    - 买入表单字段: input[name='zqdm'], input[name='mrjg'], input[name='mrsl']
    - 卖出表单字段: input[name='zqdm'], input[name='mcjg'], input[name='mcsl']
    - 确认弹窗: button.btntrue.btntrue2  取消: button.btnfalse
    - 资金表格: table.zj  股份(持仓)表格: table.gf
    - 顶部搜索框: input#search
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import Page, BrowserContext, ElementHandle

from utils.logger import get_logger, console, SCREENSHOT_DIR

# ============================================================
# 常量
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 目标 URL
COMPETITION_URL = "https://choicelab.eastmoney.com/trade/?code=ca06c5e8-fa7a-4208-8728-7260a210a63c&type=2"
TRADING_URL = "https://choicelab.eastmoney.com/trade/?code=ca06c5e8-fa7a-4208-8728-7260a210a63c&type=2"

logger = get_logger("browser_ops")


# ============================================================
# 页面导航
# ============================================================

async def navigate_to_competition(page: Page) -> bool:
    """
    导航到A股争霸赛交易页面

    Args:
        page: Playwright 页面实例

    Returns:
        bool - 是否成功导航
    """
    try:
        logger.info(f"正在导航到比赛页面: {COMPETITION_URL}")
        await page.goto(COMPETITION_URL, wait_until="domcontentloaded", timeout=30000)

        # 等待交易面板核心元素加载（搜索框 + 股票代码输入框）
        await page.wait_for_selector("input#search", timeout=15000)
        logger.info("搜索框已加载，页面就绪")

        # 额外等待动态内容渲染完毕
        await page.wait_for_timeout(2000)

        logger.info(f"当前页面: {page.url}")
        return True

    except Exception as e:
        logger.error(f"导航到比赛页面失败: {e}")
        await _save_error_screenshot(page, "navigate_error")
        return False


# ============================================================
# 股票搜索
# ============================================================

async def search_stock(page: Page, stock_code: str) -> bool:
    """
    在顶部搜索框中搜索股票（用于在股票列表中定位）

    Args:
        page: Playwright 页面实例
        stock_code: 股票代码（如 "000001"、"600519"）

    Returns:
        bool - 是否成功搜索到股票
    """
    try:
        logger.info(f"搜索股票: {stock_code}")

        # 搜索框: input#search，placeholder='代码/拼音首字母/简称'
        search_selector = "input#search"
        await page.wait_for_selector(search_selector, timeout=5000)

        # 清空并输入股票代码
        await page.fill(search_selector, "")
        await page.fill(search_selector, stock_code)
        await page.wait_for_timeout(500)  # 等待搜索建议出现

        # 按回车确认搜索
        await page.press(search_selector, "Enter")
        await page.wait_for_timeout(1000)  # 等待搜索结果刷新

        logger.info(f"股票 {stock_code} 搜索完成")
        return True

    except Exception as e:
        logger.error(f"搜索股票 {stock_code} 失败: {e}")
        await _save_error_screenshot(page, f"search_error_{stock_code}")
        return False


# ============================================================
# 买入操作
# ============================================================

async def buy_stock(page: Page, stock_code: str, amount: int, price: Optional[float] = None) -> dict:
    """
    执行股票买入操作

    Args:
        page: Playwright 页面实例
        stock_code: 股票代码
        amount: 买入数量（股，必须为100的整数倍）
        price: 买入价格，None 则使用页面默认价格（通常为现价）

    Returns:
        dict - 执行结果:
            {
                "success": bool,
                "code": str,
                "amount": int,
                "price": float,
                "message": str
            }
    """
    result = {
        "success": False,
        "code": stock_code,
        "amount": amount,
        "price": 0.0,
        "message": "",
    }

    try:
        logger.info(f"准备买入: {stock_code} x {amount}股" + (f" @ {price}" if price else ""))

        # ---- 步骤1: 点击左侧导航栏"买入"，切换到买入视图 ----
        buy_nav = page.locator("text=买入").first
        await buy_nav.click()
        await page.wait_for_timeout(800)
        logger.info("已切换到买入视图")

        # ---- 步骤2: 填写股票代码 ----
        # 买入表单中股票代码输入框：第一个 input[name='zqdm']
        code_input = page.locator("input[name='zqdm']").first
        await code_input.click(click_count=3)
        await page.keyboard.press("Backspace")
        await page.wait_for_timeout(200)
        # 用 press_sequentially 模拟键盘逐字输入以触发 keyup 事件
        await code_input.press_sequentially(stock_code, delay=100)
        
        # 等待下拉菜单中包含该股票代码的选项并点击它
        dropdown_item = page.locator(f"ul.zqdmList:visible li:has-text('{stock_code}')").first
        try:
            await dropdown_item.wait_for(state="visible", timeout=5000)
            await dropdown_item.click()
            logger.info(f"已点击下拉菜单选项，选择股票: {stock_code}")
        except Exception as e:
            logger.warning(f"未能匹配或点击下拉菜单选项: {e}，尝试按 Tab 作为后备方案")
            await code_input.press("Tab")
            
        await page.wait_for_timeout(1500)  # 等待系统返回股票信息（名称、价格等）
        logger.info(f"已输入并选择股票代码: {stock_code}")

        # ---- 步骤3: 填写买入价格（可选） ----
        if price is not None:
            price_input = page.locator("input[name='mrjg']")
            await price_input.click(click_count=3)  # 三击全选
            await page.keyboard.press("Backspace")
            await price_input.fill(str(price))
            logger.info(f"已设置买入价格: {price}")
        else:
            # 读取系统默认价格用于日志
            price_input = page.locator("input[name='mrjg']")
            default_price = await price_input.input_value()
            if default_price:
                try:
                    result["price"] = float(default_price)
                    logger.info(f"使用默认买入价格: {default_price}")
                except ValueError:
                    pass

        # ---- 步骤4: 填写买入数量 ----
        amount_input = page.locator("input[name='mrsl']")
        await amount_input.click(click_count=3)
        await amount_input.fill(str(amount))
        logger.info(f"已设置买入数量: {amount}")

        # ---- 步骤5: 点击"买入下单"按钮 ----
        buy_btn = page.locator("a.btn.buy").filter(has_text="买入下单").first
        await buy_btn.click()
        await page.wait_for_timeout(1000)
        logger.info("已点击买入下单按钮，等待确认弹窗")

        # ---- 步骤6: 处理确认弹窗 ---- Bug10修复：追踪确认是否成功
        confirm_clicked = False
        try:
            confirm_btn = page.locator("button.btntrue.btntrue2")
            await confirm_btn.wait_for(timeout=5000)
            await confirm_btn.click()
            await page.wait_for_timeout(1000)
            logger.info("已点击确认按钮")
            confirm_clicked = True
        except Exception:
            # 尝试备选确认按钮
            try:
                alt_confirm = page.locator("button.btntrue.btntrue1")
                await alt_confirm.wait_for(timeout=2000)
                await alt_confirm.click()
                await page.wait_for_timeout(1000)
                logger.info("已点击备选确认按钮")
                confirm_clicked = True
            except Exception:
                logger.error("未能点击确认弹窗（btntrue2/btntrue1 均未找到），委托可能未成功提交")

        # ---- 步骤7: 读取最终价格并标记结果 ----
        if price is not None:
            result["price"] = price
        else:
            try:
                final_price = await page.locator("input[name='mrjg']").input_value()
                result["price"] = float(final_price) if final_price else 0.0
            except Exception:
                pass

        if confirm_clicked:
            result["success"] = True
            result["message"] = "买入委托已提交"
            logger.info(f"买入委托完成: {stock_code} x {amount}股 @ {result['price']}")
        else:
            result["success"] = False
            result["message"] = f"买入委托 {stock_code} 失败：确认弹窗未响应"

        return result

    except Exception as e:
        error_msg = f"买入 {stock_code} 出错: {e}"
        logger.error(error_msg)
        result["message"] = error_msg
        await _save_error_screenshot(page, f"buy_error_{stock_code}")
        return result


# ============================================================
# 卖出操作
# ============================================================

async def sell_stock(page: Page, stock_code: str, amount: int, price: Optional[float] = None) -> dict:
    """
    执行股票卖出操作

    通过直接调用 spoTradeOrder API 提交卖单（绕过不可靠的 modal 确认流程）。

    Args:
        page: Playwright 页面实例
        stock_code: 股票代码
        amount: 卖出数量（股）
        price: 卖出价格，None 则使用页面默认价格（通常为现价）

    Returns:
        dict - 执行结果
    """
    result = {
        "success": False,
        "code": stock_code,
        "amount": amount,
        "price": 0.0,
        "message": "",
    }

    try:
        logger.info(f"准备卖出: {stock_code} x {amount}股" + (f" @ {price}" if price else ""))

        # ---- 步骤1: 切换到卖出视图 ----
        sell_nav = page.locator("text=卖出").first
        await sell_nav.click()
        await page.wait_for_timeout(800)
        logger.info("已切换到卖出视图")

        # ---- 步骤2: 填写股票代码 ----
        code_input = page.locator("input[name='zqdm']:visible").first
        await code_input.click(click_count=3)
        await page.keyboard.press("Backspace")
        await page.wait_for_timeout(200)
        await code_input.press_sequentially(stock_code, delay=100)

        dropdown_item = page.locator(f"ul.zqdmList:visible li:has-text('{stock_code}')").first
        try:
            await dropdown_item.wait_for(state="visible", timeout=5000)
            await dropdown_item.click()
            logger.info(f"已选择股票: {stock_code}")
        except Exception as e:
            logger.warning(f"下拉菜单选择失败: {e}，尝试 Tab")
            await code_input.press("Tab")

        await page.wait_for_timeout(1500)
        logger.info(f"已输入股票代码: {stock_code}")

        # ---- 步骤3: 获取卖出价格 ----
        if price is not None:
            price_str = str(price)
            price_input = page.locator("input[name='mcjg']")
            await price_input.click(click_count=3)
            await page.keyboard.press("Backspace")
            await price_input.fill(price_str)
            logger.info(f"已设置卖出价格: {price}")
        else:
            price_input = page.locator("input[name='mcjg']")
            price_str = await price_input.input_value()
            if price_str:
                try:
                    result["price"] = float(price_str)
                except ValueError:
                    pass
            logger.info(f"使用页面默认卖出价格: {price_str}")

        # ---- 步骤4: 调用 spoTradeOrder API 直接提交卖单 ----
        # 绕过不可靠的 modal 确认流程。modal 的 btntrue2 handler 使用
        # .one('click') 绑定，强制显示 modal 不会触发 Bootstrap 的
        # shown.bs.modal 事件，导致 handler 未绑定，点击确认按钮无效果。
        mkt_code = "1" if stock_code[0] in ("6", "5") else "0"

        js_code = f"""(function() {{
            var params = {{
                uToken: config.A.ut,
                cToken: config.A.ct,
                uid: Cookies.get('UID'),
                accId: config.A.zjzh,
                mktCode: '{mkt_code}',
                stkCode: '{stock_code}',
                price: '{price_str}',
                volume: {amount},
                orderDrt: '2',
                orderType: '1',
                stkType: window.stkType || ''
            }};
            console.log('SELL_PARAMS:', JSON.stringify(params));
            if (typeof spoTradeOrder !== 'function') {{
                return JSON.stringify({{error: 'spoTradeOrder not found'}});
            }}
            spoTradeOrder(params,
                function(res) {{
                    window.__sell_api_result = {{success: true, data: res}};
                }},
                function(err) {{
                    window.__sell_api_result = {{success: false, error: JSON.stringify(err)}};
                }}
            );
            return 'called';
        }})()"""
        logger.info(f"调用 spoTradeOrder: {stock_code} x {amount} @ {price_str}")
        call_result = await page.evaluate(js_code)
        logger.info(f"spoTradeOrder 调用结果: {call_result}")
        await page.wait_for_timeout(3000)

        # ---- 步骤5: 读取 API 响应 ----
        api_result = await page.evaluate("""() => {
            var r = window.__sell_api_result;
            window.__sell_api_result = null;
            if (!r) return {success: false, error: 'no response'};
            return r;
        }""")

        logger.info(f"卖出API响应: {api_result}")

        if api_result.get("success") and api_result.get("data"):
            data = api_result["data"]
            code = data.get("Code", -1)
            msg = data.get("Message", "")
            if code == 0:
                result["success"] = True
                result["message"] = "卖出委托已提交"
                result["price"] = float(price_str) if price_str else 0.0
                logger.info(f"卖出委托成功: {stock_code} x {amount} @ {price_str}")
            else:
                result["success"] = False
                result["message"] = f"卖出委托失败: {msg} (Code={code})"
                result["price"] = float(price_str) if price_str else 0.0
                logger.warning(f"卖出委托被拒绝: {msg} (Code={code})")
        else:
            result["success"] = False
            result["message"] = f"卖出API调用失败: {api_result.get('error', 'unknown')}"
            logger.error(result["message"])

        return result

    except Exception as e:
        error_msg = f"卖出 {stock_code} 出错: {e}"
        logger.error(error_msg)
        result["message"] = error_msg
        await _save_error_screenshot(page, f"sell_error_{stock_code}")
        return result


# ============================================================
# 撤单操作
# ============================================================

async def cancel_order(page: Page, order_id: str, stock_code: str) -> dict:
    """
    撤销指定订单（通过直接调用 spoCancel API）

    Args:
        page: Playwright 页面实例
        order_id: 订单ID
        stock_code: 股票代码

    Returns:
        dict - {success, order_id, message}
    """
    result = {"success": False, "order_id": order_id, "message": ""}

    try:
        mkt_code = "1" if stock_code[0] in ("6", "5") else "0"

        js_code = f"""(function() {{
            var params = {{
                uToken: config.A.ut,
                cToken: config.A.ct,
                accId: config.A.zjzh,
                mktCode: '{mkt_code}',
                stkCode: '{stock_code}',
                orderId: '{order_id}',
                uid: Cookies.get('UID')
            }};
            if (typeof spoCancel !== 'function') {{
                return JSON.stringify({{error: 'spoCancel not found'}});
            }}
            spoCancel(params,
                function(res) {{
                    window.__cancel_result = {{success: true, data: res}};
                }},
                function(err) {{
                    window.__cancel_result = {{success: false, error: JSON.stringify(err)}};
                }}
            );
            return 'called';
        }})()"""
        logger.info(f"撤单: order_id={order_id}, stock={stock_code}")
        await page.evaluate(js_code)
        await page.wait_for_timeout(2000)

        api_result = await page.evaluate("""() => {
            var r = window.__cancel_result;
            window.__cancel_result = null;
            if (!r) return {success: false, error: 'no response'};
            return r;
        }""")

        if api_result.get("success") and api_result.get("data"):
            data = api_result["data"]
            code = data.get("Code", -1)
            msg = data.get("Message", "")
            if code == 0:
                result["success"] = True
                result["message"] = f"撤单成功: {order_id}"
                logger.info(result["message"])
            else:
                result["message"] = f"撤单失败: {msg} (Code={code})"
                logger.warning(result["message"])
        else:
            result["message"] = f"撤单API调用失败: {api_result.get('error', 'unknown')}"
            logger.error(result["message"])

        return result

    except Exception as e:
        result["message"] = f"撤单异常: {e}"
        logger.error(result["message"])
        return result


async def cancel_all_sell_orders(page: Page, stock_code: str) -> list[dict]:
    """
    撤销指定股票的所有 pending 卖单。

    先查询当日委托中该股票的卖单，再逐一撤单。

    Args:
        page: Playwright 页面实例
        stock_code: 股票代码

    Returns:
        list[dict] - 每个订单的撤单结果
    """
    results = []

    try:
        # 先获取当前订单列表（通过 API 拦截的方式不可靠，用 JS 查询页面数据）
        # 切换到 当日委托 tab 获取 orderList
        await page.evaluate('''() => {
            var lis = document.querySelectorAll('li');
            for (var i = 0; i < lis.length; i++) {
                if (lis[i].textContent.trim().indexOf('当日委托') >= 0) {
                    lis[i].click();
                    break;
                }
            }
        }''')
        await page.wait_for_timeout(2000)

        # 从 orderList 中过滤出该股票的卖单
        sell_orders = await page.evaluate(f'''(stock) => {{
            if (typeof orderList === 'undefined') return [];
            var result = [];
            for (var i = 0; i < orderList.length; i++) {{
                var item = orderList[i];
                if (item.secCode === stock && item.drt == 2) {{
                    result.push({{
                        orderId: item.orderId,
                        secCode: item.secCode,
                        orderPrice: item.orderPrice,
                        orderCount: item.orderCount,
                        status: item.status
                    }});
                }}
            }}
            return result;
        }}''', stock_code)

        logger.info(f"找到 {stock_code} 的 {len(sell_orders)} 个pending卖单")

        for order in sell_orders:
            oid = order.get("orderId")
            if oid:
                cancel_result = await cancel_order(page, oid, stock_code)
                results.append(cancel_result)
                await page.wait_for_timeout(500)

    except Exception as e:
        logger.error(f"批量撤单异常: {e}")

    return results


# ============================================================
# 持仓查询
# ============================================================

async def get_current_positions(page: Page) -> list[dict]:
    """
    获取当前持仓信息

    通过左侧导航栏"查询 > 资金股份"进入，然后读取 table.gf（股份表）

    Args:
        page: Playwright 页面实例

    Returns:
        持仓列表，每个元素包含：
        {
            "code": "股票代码",
            "name": "股票名称",
            "amount": 持仓数量,
            "available": 可卖数量,
            "cost_price": 成本价,
            "current_price": 当前价,
            "profit_pct": 盈亏百分比,
            "market_value": 市值
        }
    """
    positions = []

    try:
        logger.info("查询当前持仓...")

        # ---- 步骤1: 点击左侧导航"资金股份"（查询子菜单） ----
        # 先点"查询"展开子菜单，再点"资金股份"
        query_nav = page.locator("text=查询").first
        await query_nav.click()
        await page.wait_for_timeout(500)

        funds_nav = page.locator("text=资金股份").first
        await funds_nav.click()
        await page.wait_for_timeout(1500)  # 等待数据加载
        logger.info("已切换到资金股份页面")

        # ---- 步骤2: 等待股份表格加载 ----
        try:
            await page.wait_for_selector("table.gf", timeout=5000)
        except Exception:
            logger.warning("未找到 table.gf，可能暂无持仓")
            return positions

        # ---- 步骤3: 解析股份表格 ---- Bug12修复：动态匹配列名，不再硬编码列索引
        rows = await page.query_selector_all("table.gf tr")

        # 先解析表头，建立列名→索引的映射
        col_map: dict[str, int] = {}
        if rows:
            header_cells = await rows[0].query_selector_all("th, td")
            for idx, cell in enumerate(header_cells):
                col_map[(await cell.inner_text()).strip()] = idx

        # 列名别名映射（应对不同版本的列名）
        ALIAS: dict[str, list[str]] = {
            "code":          ["证券代码", "股票代码", "代码"],
            "name":          ["证券名称", "股票名称", "名称"],
            "amount":        ["持股数量", "股份数量", "持仓数量", "数量"],
            "cost_price":    ["成本价", "买入成本", "持仓成本"],
            "current_price": ["现价", "最新价", "当前价"],
            "market_value":  ["市値", "持仓市値", "总市値"],
            "profit_pct":    ["盈亏比例", "盈亏%", "涨跌幅", "盈亏率"],
        }
        FALLBACK = {"code": 0, "name": 1, "amount": 2, "cost_price": 5,
                    "current_price": 6, "market_value": 7, "profit_pct": 9}

        def _col_idx(field: str) -> int | None:
            for alias in ALIAS.get(field, []):
                if alias in col_map:
                    return col_map[alias]
            return None

        use_dynamic = bool(col_map)

        for row in rows:
            try:
                cols = await row.query_selector_all("td")
                if len(cols) < 2:
                    continue  # 跳过表头行或空行

                col_texts = [(await col.inner_text()).strip() for col in cols]

                if len(col_texts) >= 4:
                    if col_texts[0] == "证券代码" or not col_texts[0].isdigit():
                        continue  # 跳过表头行

                    def _get(field: str, is_pct: bool = False) -> str:
                        idx = (_col_idx(field) if use_dynamic else None)
                        if idx is None:
                            idx = FALLBACK.get(field)
                        if idx is None or idx >= len(col_texts):
                            return "0"
                        val = col_texts[idx]
                        return val.replace("%", "") if is_pct else val

                    position = {
                        "code":          _get("code"),
                        "name":          _get("name"),
                        "amount":        _parse_int(_get("amount")),
                        "available":     _parse_int(_get("amount")),  # 比赛环境可卖=持仓
                        "cost_price":    _parse_float(_get("cost_price")),
                        "current_price": _parse_float(_get("current_price")),
                        "profit_pct":    _parse_float(_get("profit_pct", is_pct=True)),
                        "market_value":  _parse_float(_get("market_value")),
                    }
                    positions.append(position)
            except (ValueError, IndexError) as e:
                logger.warning(f"解析持仓行失败: {e}")

        logger.info(f"获取到 {len(positions)} 条持仓记录")
        return positions

    except Exception as e:
        logger.error(f"获取持仓信息失败: {e}")
        await _save_error_screenshot(page, "positions_error")
        return positions


# ============================================================
# 账户信息
# ============================================================

async def get_account_info(page: Page) -> dict:
    """
    获取账户信息（总资产、可用资金、盈亏等）

    通过左侧导航栏"查询 > 资金股份"进入，然后读取 table.zj（资金表）

    Args:
        page: Playwright 页面实例

    Returns:
        账户信息字典：
        {
            "total_assets": 总资产,
            "available_cash": 可用资金,
            "market_value": 持仓市值,
            "profit": 盈亏金额,
            "profit_pct": 盈亏百分比,
            "initial_assets": 初始资金
        }
    """
    account = {
        "total_assets": 0.0,
        "available_cash": 0.0,
        "market_value": 0.0,
        "profit": 0.0,
        "profit_pct": 0.0,
        "initial_assets": 1000000.0,  # 比赛初始资金，通常100万
    }

    try:
        logger.info("查询账户信息...")

        # ---- 步骤1: 点击左侧导航"资金股份"（查询子菜单） ----
        query_nav = page.locator("text=查询").first
        await query_nav.click()
        await page.wait_for_timeout(500)

        funds_nav = page.locator("text=资金股份").first
        await funds_nav.click()
        await page.wait_for_timeout(1500)
        logger.info("已切换到资金股份页面")

        # ---- 步骤2: 等待资金表格加载 ----
        try:
            await page.wait_for_selector("table.zj", timeout=5000)
        except Exception:
            logger.warning("未找到 table.zj 资金表格")
            return account

        # ---- 步骤3: 等待并解析资金表格 ----
        # table.zj 结构为键值对平铺
        # 第1行: 总资产, <val>, 持仓盈亏, <val>, 锁定金额, <val>
        # 第2行: 总市值, <val>, 盈亏比例, <val>, 可用余额, <val>
        # 由于数据可能是动态加载的，部分值初始可能为 "-"
        for attempt in range(5):
            rows = await page.query_selector_all("table.zj tr")
            if len(rows) >= 2:
                cells_0 = await rows[0].query_selector_all("th, td")
                texts_0 = [(await cell.inner_text()).strip() for cell in cells_0]
                
                cells_1 = await rows[1].query_selector_all("th, td")
                texts_1 = [(await cell.inner_text()).strip() for cell in cells_1]
                
                if len(texts_0) >= 6 and len(texts_1) >= 6:
                    total_assets_str = texts_0[1]
                    if total_assets_str != "-":
                        account["total_assets"] = _parse_float(total_assets_str)
                        account["profit"] = _parse_float(texts_0[3])
                        
                        account["market_value"] = _parse_float(texts_1[1])
                        account["profit_pct"] = _parse_float(texts_1[3].replace("%", ""))
                        account["available_cash"] = _parse_float(texts_1[5])
                        break
                        
            await page.wait_for_timeout(1000)

        # 如果未通过表头匹配到，也可以直接从买入表单读取可用资金
        if account["available_cash"] == 0.0:
            try:
                kyzj = await page.locator("input[name='kyzj']").input_value()
                if kyzj:
                    account["available_cash"] = _parse_float(kyzj)
            except Exception:
                pass

        # Bug9修复：只在网页解析的 profit/profit_pct 为 0 时才回退到计算值
        # 原来无条件覆盖，导致花了几十行解析的 P&L 被简单减法替代
        if account["total_assets"] > 0 and account["initial_assets"] > 0:
            if account["profit"] == 0.0:  # 表格解析失败，回退到计算值
                account["profit"] = account["total_assets"] - account["initial_assets"]
            if account["profit_pct"] == 0.0:  # 表格解析失败，回退到计算值
                account["profit_pct"] = account["profit"] / account["initial_assets"] * 100

        logger.info(
            f"账户信息: 总资产={account['total_assets']:.2f}, "
            f"可用={account['available_cash']:.2f}, "
            f"市值={account['market_value']:.2f}"
        )
        return account

    except Exception as e:
        logger.error(f"获取账户信息失败: {e}")
        await _save_error_screenshot(page, "account_error")
        return account


# ============================================================
# 排名查询
# ============================================================

async def get_ranking(page: Page) -> dict:
    """
    获取当前比赛排名

    注意: 排名信息可能不在交易页面中，而是在比赛主页或排行榜页面。
    当前交易页面 DOM 中未发现排名相关元素。
    后续可通过访问比赛排行榜页面来实现。

    Args:
        page: Playwright 页面实例

    Returns:
        排名信息：
        {
            "rank": 当前排名,
            "total_participants": 总参赛人数,
            "profit_pct": 收益率
        }
    """
    ranking = {
        "rank": 0,
        "total_participants": 0,
        "profit_pct": 0.0,
    }

    try:
        logger.info("查询比赛排名...")

        # 尝试从账户信息中获取收益率作为参考
        account = await get_account_info(page)
        if account["total_assets"] > 0:
            ranking["profit_pct"] = account["profit_pct"]

        # 获取竞赛代码以构造排行榜 URL
        from urllib.parse import urlparse, parse_qs
        parsed_url = urlparse(COMPETITION_URL)
        qs = parse_qs(parsed_url.query)
        code = qs.get("code", [""])[0]

        if not code:
            logger.warning("无法从 COMPETITION_URL 提取比赛 code")
            return ranking

        # 修正排名页 URL（原 competition/ranking 为错误路径）
        ranking_url = f"https://choicelab.eastmoney.com/comCourse/aCom?code={code}"
        logger.info(f"正在导航至排行榜页面: {ranking_url}")

        # 在新标签页中打开以不影响交易主页面
        context = page.context
        new_page = await context.new_page()

        try:
            await new_page.goto(ranking_url, wait_until="domcontentloaded", timeout=15000)
            await new_page.wait_for_timeout(3000)  # 等待动态数据渲染（排行榜页面较重）

            # 尝试通过 JavaScript 提取排名，适配 comCourse/aCom 页面
            rank_text = await new_page.evaluate('''() => {
                const all = Array.from(document.querySelectorAll("*"));

                // 1. 优先查找含"我的排名" / "当前排名" / "名次"的元素
                for (let el of all) {
                    const t = el.innerText || "";
                    if (t && (
                        t.includes("我的排名") ||
                        t.includes("当前排名") ||
                        t.includes("我的名次") ||
                        t.includes("排名:")  ||
                        t.includes("排名：")
                    )) {
                        return t.trim();
                    }
                }

                // 2. 查找高亮行（当前用户行，通常有特殊 class）
                const highlighted = document.querySelector(
                    ".my-row, .current-user, .self-row, .highlight-row, tr.active"
                );
                if (highlighted && highlighted.innerText) {
                    return highlighted.innerText.trim();
                }

                // 3. 查找包含"我"的表格行
                const rows = Array.from(document.querySelectorAll("tr, .rank-row, .list-item"));
                for (let row of rows) {
                    const t = row.innerText || "";
                    if (t.includes("我") && /\\d+/.test(t)) {
                        return t.trim();
                    }
                }

                // 4. 回退：返回页面 body 前 500 字符供调试
                return document.body ? document.body.innerText.slice(0, 500) : "";
            }''')

            if rank_text:
                import re
                # 匹配：第N名 / 排名N / 名次N / 第 N 名
                matches = re.findall(r'(?:第|排名|名次)[\s:：]*(\d+)', rank_text)
                if not matches:
                    # 如果含"我"且有数字，取第一个数字作为排名
                    matches = re.findall(r'(\d+)', rank_text)

                if matches:
                    ranking["rank"] = int(matches[0])
                    logger.info(f"获取到排名: 第 {ranking['rank']} 名")
                else:
                    logger.warning(f"未能解析排名数字，页面文本片段: {rank_text[:200]}")
            else:
                logger.warning("排行榜页面无文本内容，可能需要登录态或页面结构已变化")

        except Exception as inner_e:
            logger.warning(f"访问排行榜页面出错: {inner_e}")
        finally:
            await new_page.close()

        return ranking

    except Exception as e:
        logger.error(f"获取排名失败: {e}")
        return ranking


# ============================================================
# 页面结构分析（调试工具）
# ============================================================

async def analyze_page_structure(page: Page, label: str = "analysis") -> dict:
    """
    分析页面结构，用于适配选择器

    截取页面截图并导出关键 DOM 信息，帮助开发者
    了解页面结构以编写正确的选择器。

    Args:
        page: Playwright 页面实例
        label: 分析标签（用于文件命名）

    Returns:
        分析结果字典，包含页面关键信息
    """
    analysis = {
        "url": page.url,
        "title": await page.title(),
        "timestamp": datetime.now().isoformat(),
        "inputs": [],
        "buttons": [],
        "tables": [],
        "key_elements": [],
    }

    try:
        logger.info(f"开始分析页面结构: {page.url}")

        # ----- 截图 -----
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        screenshot_path = SCREENSHOT_DIR / f"{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)
        logger.info(f"页面截图已保存: {screenshot_path}")

        # ----- 分析输入框 -----
        inputs = await page.query_selector_all("input, textarea")
        for inp in inputs[:20]:  # 限制数量避免过多
            try:
                info = {
                    "tag": await inp.evaluate("el => el.tagName"),
                    "type": await inp.get_attribute("type") or "",
                    "name": await inp.get_attribute("name") or "",
                    "placeholder": await inp.get_attribute("placeholder") or "",
                    "class": await inp.get_attribute("class") or "",
                    "id": await inp.get_attribute("id") or "",
                }
                analysis["inputs"].append(info)
            except Exception:
                pass

        # ----- 分析按钮 -----
        buttons = await page.query_selector_all("button, [role='button'], a.btn, .btn")
        for btn in buttons[:30]:
            try:
                info = {
                    "text": (await btn.inner_text()).strip()[:50],
                    "class": await btn.get_attribute("class") or "",
                    "id": await btn.get_attribute("id") or "",
                    "type": await btn.get_attribute("type") or "",
                }
                analysis["buttons"].append(info)
            except Exception:
                pass

        # ----- 分析表格 -----
        tables = await page.query_selector_all("table")
        for i, table in enumerate(tables[:10]):
            try:
                headers = await table.query_selector_all("th")
                header_texts = []
                for h in headers[:15]:
                    text = (await h.inner_text()).strip()
                    if text:
                        header_texts.append(text)

                row_count = len(await table.query_selector_all("tr"))

                analysis["tables"].append({
                    "index": i,
                    "headers": header_texts,
                    "row_count": row_count,
                    "class": await table.get_attribute("class") or "",
                })
            except Exception:
                pass

        # ----- 分析关键元素（买入/卖出/搜索等） -----
        key_keywords = ["买入", "卖出", "搜索", "持仓", "委托", "交易", "下单",
                        "排名", "收益", "资产", "可用", "成交", "撤单"]
        for keyword in key_keywords:
            try:
                elements = await page.query_selector_all(f"text={keyword}")
                for el in elements[:3]:
                    tag = await el.evaluate("el => el.tagName")
                    cls = await el.get_attribute("class") or ""
                    parent_cls = await el.evaluate(
                        "el => el.parentElement ? el.parentElement.className : ''"
                    )
                    analysis["key_elements"].append({
                        "keyword": keyword,
                        "tag": tag,
                        "class": cls,
                        "parent_class": parent_cls,
                    })
            except Exception:
                pass

        # ----- 导出完整 DOM 结构（精简版） -----
        dom_path = SCREENSHOT_DIR / f"{label}_dom_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        dom_content = await page.content()
        with open(dom_path, "w", encoding="utf-8") as f:
            f.write(dom_content)
        logger.info(f"DOM 结构已保存: {dom_path}")

        # ----- 保存分析结果 JSON -----
        json_path = SCREENSHOT_DIR / f"{label}_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(analysis, f, ensure_ascii=False, indent=2)
        logger.info(f"分析结果已保存: {json_path}")

        # ----- 控制台摘要 -----
        console.print(f"\n[header]📋 页面分析摘要[/header]")
        console.print(f"  URL: {analysis['url']}")
        console.print(f"  标题: {analysis['title']}")
        console.print(f"  输入框: {len(analysis['inputs'])} 个")
        console.print(f"  按钮: {len(analysis['buttons'])} 个")
        console.print(f"  表格: {len(analysis['tables'])} 个")
        console.print(f"  关键元素: {len(analysis['key_elements'])} 个")
        console.print(f"  截图: {screenshot_path}")
        console.print(f"  DOM: {dom_path}")
        console.print(f"  JSON: {json_path}\n")

        return analysis

    except Exception as e:
        logger.error(f"页面分析失败: {e}")
        return analysis


# ============================================================
# 工具函数
# ============================================================

async def _save_error_screenshot(page: Page, label: str) -> None:
    """
    保存错误截图

    Args:
        page: 页面实例
        label: 错误标签（用于文件命名）
    """
    try:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOT_DIR / f"error_{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        await page.screenshot(path=str(path))
        logger.info(f"错误截图已保存: {path}")
    except Exception as e:
        logger.warning(f"保存错误截图失败: {e}")


def _parse_float(text: str) -> float:
    """安全地将文本解析为浮点数（去除逗号、货币符号等）"""
    try:
        cleaned = text.replace(",", "").replace("¥", "").replace(" ", "").strip()
        return float(cleaned) if cleaned and cleaned != "--" else 0.0
    except (ValueError, AttributeError):
        return 0.0


def _parse_int(text: str) -> int:
    """安全地将文本解析为整数（去除逗号等）"""
    try:
        cleaned = text.replace(",", "").replace(" ", "").strip()
        return int(float(cleaned)) if cleaned and cleaned != "--" else 0
    except (ValueError, AttributeError):
        return 0


async def safe_click(page: Page, selector: str, timeout: int = 5000) -> bool:
    """
    安全点击元素（带超时和错误处理）

    Args:
        page: 页面实例
        selector: CSS 选择器
        timeout: 超时时间（毫秒）

    Returns:
        bool - 是否成功点击
    """
    try:
        await page.wait_for_selector(selector, timeout=timeout)
        await page.click(selector)
        return True
    except Exception as e:
        logger.warning(f"点击元素失败 [{selector}]: {e}")
        return False


async def safe_fill(page: Page, selector: str, value: str, timeout: int = 5000) -> bool:
    """
    安全填充输入框（带超时和错误处理）

    Args:
        page: 页面实例
        selector: CSS 选择器
        value: 要填入的值
        timeout: 超时时间（毫秒）

    Returns:
        bool - 是否成功填充
    """
    try:
        await page.wait_for_selector(selector, timeout=timeout)
        await page.fill(selector, value)
        return True
    except Exception as e:
        logger.warning(f"填充输入框失败 [{selector}]: {e}")
        return False


async def get_element_text(page: Page, selector: str, default: str = "") -> str:
    """
    安全获取元素文本

    Args:
        page: 页面实例
        selector: CSS 选择器
        default: 默认值（元素不存在时返回）

    Returns:
        元素文本内容
    """
    try:
        el = await page.query_selector(selector)
        if el:
            return (await el.inner_text()).strip()
        return default
    except Exception:
        return default


# ============================================================
# 模块自测
# ============================================================
if __name__ == "__main__":
    async def _test():
        """测试页面分析功能"""
        from playwright.async_api import async_playwright

        console.print("[header]🌐 浏览器操作模块测试[/header]")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(viewport={"width": 1280, "height": 800})
            page = await context.new_page()

            await page.goto(COMPETITION_URL, wait_until="domcontentloaded")
            await asyncio.sleep(3)

            # 分析页面结构
            await analyze_page_structure(page, "test_analysis")

            console.print("[success]✅ 浏览器操作模块测试完成[/success]")
            await browser.close()

    asyncio.run(_test())
