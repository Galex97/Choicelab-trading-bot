"""
登录模块 - 东方财富金融实验室登录管理

功能：
- Cookie 持久化保存与加载
- 手动登录引导（短信验证码登录）
- 登录状态校验
- 一键获取已认证的浏览器上下文
"""

import json
import asyncio
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
)

from utils.logger import get_logger, console

# ============================================================
# 常量
# ============================================================

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Cookie 存储路径
COOKIE_PATH = PROJECT_ROOT / "config" / "cookies.json"

# 目标网站
TARGET_URL = "https://choicelab.eastmoney.com/mainPage"
LOGIN_SUCCESS_INDICATOR = "choicelab.eastmoney.com"

# 日志
logger = get_logger("login")


# ============================================================
# Cookie 管理
# ============================================================

async def save_login_state(headless: bool = False) -> bool:
    """
    引导用户手动登录并保存 Cookie

    流程：
    1. 打开浏览器，导航到东方财富登录页
    2. 用户手动完成短信验证码登录
    3. 检测到登录成功后，保存 Cookie 到本地文件

    Args:
        headless: 是否无头模式（登录时必须为 False）

    Returns:
        bool - 是否成功保存登录状态
    """
    console.print("\n[header]🔐 东方财富金融实验室 - 手动登录[/header]\n")
    console.print("即将打开浏览器，请在浏览器中完成以下操作：")
    console.print("  1️⃣  点击「登录」按钮")
    console.print("  2️⃣  输入手机号，获取短信验证码")
    console.print("  3️⃣  输入验证码完成登录")
    console.print("  4️⃣  登录成功后，程序会自动保存登录状态\n")
    console.print("[dim]提示：登录完成后请不要关闭浏览器，程序会自动检测并保存。[/dim]\n")

    async with async_playwright() as p:
        # 启动浏览器（登录时必须显示界面）
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        try:
            # 导航到目标页面
            logger.info(f"正在打开页面: {TARGET_URL}")
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)

            # 等待用户完成登录
            console.print("[yellow]⏳ 等待登录完成（最长等待5分钟）...[/yellow]")

            # 轮询检测登录状态（通过检测页面上是否出现登录后才有的元素）
            max_wait_seconds = 300  # 5分钟
            check_interval = 3     # 每3秒检查一次
            elapsed = 0

            while elapsed < max_wait_seconds:
                await asyncio.sleep(check_interval)
                elapsed += check_interval

                # 检测是否登录成功
                # 检查 URL 变化（成功登录后通常会离开 passport 或 login 页面）
                current_url = page.url
                
                try:
                    # 尝试查找东方财富页面登录后常见的元素（用户名、退出按钮等）
                    user_element = await page.query_selector(
                        ".user-name, .user-info, .avatar, a:has-text('退出'), a:has-text('注销')"
                    )
                    if user_element and "passport" not in current_url.lower() and "login" not in current_url.lower():
                        logger.info("检测到用户相关元素或离开登录页，登录成功！")
                        break
                except Exception:
                    pass

                # 方法3：检查 Cookie 中是否有登录凭证
                cookies = await context.cookies()
                # 东方财富常用的认证 Cookie (pi, ut, sid, uid)
                auth_cookies = [c for c in cookies if any(
                    keyword in c.get("name", "").lower()
                    for keyword in ["pi", "ut", "sid", "uid", "passport"]
                )]
                if auth_cookies:
                    logger.info(f"检测到认证 Cookie ({len(auth_cookies)} 个)，登录成功！")
                    break

                # 进度提示
                if elapsed % 30 == 0:
                    console.print(f"[dim]已等待 {elapsed} 秒，继续等待登录...[/dim]")

            else:
                # 超时
                console.print("[error]❌ 登录等待超时（5分钟）[/error]")
                logger.error("登录等待超时")
                return False

            # 登录成功，等待页面完全加载
            await asyncio.sleep(3)

            # 保存 Cookie
            cookies = await context.cookies()
            _save_cookies(cookies)

            console.print(f"\n[success]✅ 登录状态已保存！（共 {len(cookies)} 个 Cookie）[/success]")
            console.print(f"[dim]Cookie 文件: {COOKIE_PATH}[/dim]\n")
            logger.info(f"Cookie 已保存到 {COOKIE_PATH}，共 {len(cookies)} 个")

            return True

        except Exception as e:
            logger.error(f"登录过程出错: {e}")
            console.print(f"[error]❌ 登录出错: {e}[/error]")
            return False

        finally:
            await browser.close()


def _save_cookies(cookies: list[dict]) -> None:
    """
    将 Cookie 保存到 JSON 文件

    Args:
        cookies: Playwright 返回的 Cookie 列表
    """
    COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(COOKIE_PATH, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)


def _load_cookies() -> Optional[list[dict]]:
    """
    从 JSON 文件加载 Cookie

    Returns:
        Cookie 列表，文件不存在则返回 None
    """
    if not COOKIE_PATH.exists():
        logger.warning(f"Cookie 文件不存在: {COOKIE_PATH}")
        return None

    try:
        with open(COOKIE_PATH, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        logger.info(f"已加载 {len(cookies)} 个 Cookie")
        return cookies
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Cookie 文件读取失败: {e}")
        return None


# ============================================================
# 浏览器上下文管理
# ============================================================

async def load_login_state(
    playwright_instance: Playwright,
    headless: bool = False,
) -> Optional[tuple[Browser, BrowserContext]]:
    """
    使用保存的 Cookie 创建已认证的浏览器上下文

    Args:
        playwright_instance: Playwright 实例
        headless: 是否无头模式

    Returns:
        (Browser, BrowserContext) 元组，失败返回 None
    """
    cookies = _load_cookies()
    if cookies is None:
        return None

    try:
        browser = await playwright_instance.chromium.launch(headless=headless)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        # 注入保存的 Cookie
        await context.add_cookies(cookies)
        logger.info("Cookie 已注入浏览器上下文")

        return browser, context

    except Exception as e:
        logger.error(f"创建浏览器上下文失败: {e}")
        return None


async def check_login_valid(context: BrowserContext) -> bool:
    """
    检查当前登录状态是否有效

    通过导航到目标页面，检测是否被重定向到登录页来判断

    Args:
        context: 浏览器上下文

    Returns:
        bool - 登录是否仍然有效
    """
    page = None
    try:
        page = await context.new_page()
        logger.info("正在验证登录状态...")

        # 导航到需要登录才能访问的页面
        response = await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=20000)

        # 等待页面稳定
        await asyncio.sleep(2)

        current_url = page.url

        # 检查方法1: URL 是否包含登录相关路径（被重定向到登录页）
        login_keywords = ["login", "signin", "auth", "passport"]
        if any(keyword in current_url.lower() for keyword in login_keywords):
            logger.warning("登录已过期（被重定向到登录页）")
            return False

        # 检查方法2: 页面上是否有登录按钮（说明未登录）
        login_button = await page.query_selector(
            ".login-btn, a:has-text('登录'), button:has-text('登录')"
        )
        if login_button:
            # 进一步确认是否有退出按钮或用户名，避免误判
            user_info = await page.query_selector(
                ".user-name, .user-info, a:has-text('退出')"
            )
            if not user_info:
                logger.warning("登录已过期（检测到登录按钮且无用户信息）")
                return False

        # 检查方法3: 检查 Cookie 是否仍存在
        cookies = await context.cookies()
        auth_cookies = [c for c in cookies if any(
            keyword in c.get("name", "").lower()
            for keyword in ["pi", "ut", "sid", "uid", "passport"]
        )]
        if not auth_cookies:
            logger.warning("登录已过期（认证 Cookie 丢失）")
            return False

        logger.info("✅ 登录状态有效")
        return True

    except Exception as e:
        logger.error(f"登录状态验证出错: {e}")
        return False

    finally:
        if page:
            await page.close()


async def get_authenticated_browser(
    headless: bool = False,
    auto_relogin: bool = True,
) -> Optional[tuple[Playwright, Browser, BrowserContext]]:
    """
    获取已认证的浏览器实例（主入口函数）

    流程：
    1. 尝试加载已保存的 Cookie
    2. 验证 Cookie 是否有效
    3. 如果无效且 auto_relogin=True，提示用户重新登录（交互式）
       如果无效且 auto_relogin=False（自动模式），记录错误并返回 None
    4. 返回可用的浏览器实例

    Args:
        headless: 是否无头模式
        auto_relogin: Cookie 过期时是否自动弹出浏览器引导重新登录。
                      全自动模式（--auto）应传 False，避免无人值守时阻塞等待。

    Returns:
        (Playwright, Browser, BrowserContext) 元组
        失败返回 None
    """
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()

    # 第一步：尝试加载 Cookie
    result = await load_login_state(pw, headless=headless)

    if result is not None:
        browser, context = result

        # 第二步：验证登录状态
        if await check_login_valid(context):
            console.print("[success]✅ 登录状态有效，已就绪[/success]")
            return pw, browser, context
        else:
            # Cookie 过期，关闭旧的浏览器
            console.print("[warning]⚠️ 登录已过期，需要重新登录[/warning]")
            await browser.close()

    # Bug13修复：auto_relogin=False 时（全自动模式），不弹出浏览器等待，直接返回错误
    if not auto_relogin:
        console.print(
            "[error]❌ Cookie 已过期且当前为自动模式，无法进行交互式登录。"
            "请手动运行 `python main.py --login` 重新登录后再启动自动模式。[/error]"
        )
        logger.error("Cookie 过期，auto_relogin=False，中止自动模式启动")
        await pw.stop()
        return None

    # 第三步：需要重新登录（交互式）
    console.print("\n[yellow]需要重新登录东方财富金融实验室[/yellow]")

    # 先停止当前 playwright 实例
    await pw.stop()

    # 执行手动登录
    success = await save_login_state()
    if not success:
        console.print("[error]❌ 登录失败，无法继续[/error]")
        return None

    # 重新加载 Cookie
    pw = await async_playwright().start()
    result = await load_login_state(pw, headless=headless)

    if result is None:
        console.print("[error]❌ 加载登录状态失败[/error]")
        await pw.stop()
        return None

    browser, context = result
    console.print("[success]✅ 重新登录成功，已就绪[/success]")
    return pw, browser, context



# ============================================================
# 模块自测
# ============================================================
if __name__ == "__main__":
    async def _test():
        """测试登录流程"""
        console.print("[header]🔐 登录模块测试[/header]")

        # 测试手动登录
        result = await get_authenticated_browser(headless=False)
        if result:
            pw, browser, context = result
            console.print("[success]✅ 认证浏览器获取成功[/success]")

            # 打开一个页面验证
            page = await context.new_page()
            await page.goto(TARGET_URL, wait_until="domcontentloaded")
            console.print(f"当前页面: {page.url}")

            # 截图保存
            screenshot_path = PROJECT_ROOT / "logs" / "screenshots" / "login_test.png"
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(screenshot_path))
            console.print(f"截图已保存: {screenshot_path}")

            await browser.close()
            await pw.stop()
        else:
            console.print("[error]❌ 登录失败[/error]")

    asyncio.run(_test())
