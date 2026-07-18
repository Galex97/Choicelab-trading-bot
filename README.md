# ChoiceLab 争霸赛 — AI Agent 协作操盘系统

> 基于 [ChoiceLab 金融实验室](https://choicelab.eastmoney.com/)（东方财富旗下）的 A 股 T+1 模拟交易大赛。
> 三 Agent 架构 + Python 自动交易机器人，实现从盘后分析到次日自动下单的完整自动化操盘流水线。

## 项目简介

这是一个为 **ChoiceLab 争霸赛**（A 股 T+1 模拟交易比赛）设计的人机协作操盘系统。

**运行平台**：[ChoiceLab 金融实验室](https://choicelab.eastmoney.com/) — 东方财富出品的高校金融实训平台，提供模拟交易、比赛排名、投资分析等功能。学生在平台上创建或加入比赛后，使用本系统辅助决策和自动下单。

三个 AI Agent 各司其职（独立选股、风险审计、汇总决策），最终由 Python 机器人通过 Playwright 浏览器自动化操控 ChoiceLab 网页完成实盘下单。所有买卖决策均经过独立 Agent 的交叉验证后才执行。

本系统在某高校班级争霸赛中取得了有竞争力的排名。

## B-C-A 三 Agent 架构

整个系统的核心设计理念是**决策权分散**——让不同 Agent 在互不知情的情况下独立分析，再由主操盘手交叉验证，避免"群体思维"导致的集体误判。

```
Agent B（独立分析师）              Agent C（风险审计师）
       │                                    │
       │ 基本面+技术面选股                   │ 风险审查+否决权
       │                                    │
       └────────────┬───────────────────────┘
                    │
              Agent A（主操盘手）
                    │
          交叉验证 B/C 的建议
          输出：advice.json
                    │
            Python 交易机器人
                    │
      Risk 护栏过滤 → Playwright 操控浏览器 → ChoiceLab 平台下单
```

| Agent | 角色 | 主要工作 |
|-------|------|----------|
| **Agent B** | 独立分析师 | 基本面 + 技术面选股，筛选候选标的，给出买卖建议 |
| **Agent C** | 风险审计师 | 审查 A/B 的方案，否决追高操作，标注风险点，守住纪律 |
| **Agent A** | 主操盘手 | 盘后复盘、分配任务、交叉验证 B/C 输出、写入 `advice.json` |
| **交易机器人** | 执行引擎 | 读取 `advice.json`，经 Risk 护栏过滤，自动操控浏览器下单 |

### 为什么 B 和 C 要分开？

Agent B 和 Agent C 运行在**独立的 Claude 会话中，彼此不知道对方的存在**。B 负责"进攻"（找机会），C 负责"防守"（审风险）。Agent A 只在两者意见收敛或风险可控时才采取行动。这避免了单个 AI 既当运动员又当裁判的问题。

实际运行中，C 多次否决了 B 的激进建议（如追高、仓位过重），有效避免了冲动交易。

### 重要：先决定你的策略风格

在启动 Agent 之前，你需要先想清楚一个问题：**你是要进攻还是要防守？**

这不是一个技术问题，而是你在比赛中的**定位选择**。不同的策略风格会直接影响 Agent B 的选股偏好和 Agent C 的风险容忍度。

**实战经验**：我们这个赛季偏保守，仓位长期控制在 40% 左右，现金占比很高。虽然最大回撤控制得很好（未触发过 -5% 止损线），但也因此错过了很多机会——当 AI/半导体板块暴涨时，我们的仓位不够重，导致收益率始终跑不赢排名靠前的同学。**保守 = 安全，但也 = 放弃超额收益的可能。**

#### 进攻型 vs 防守型对比

| 维度 | 🔥 进攻型（激进） | 🛡️ 防守型（保守） |
|------|------------------|-------------------|
| **总仓位** | 45% ~ 55%（顶着上限） | 30% ~ 40% |
| **现金占比** | 30% ~ 35%（踩着下限） | 50% ~ 60% |
| **单只仓位** | 允许重仓 25% ~ 33% | 分散持有，单只 ≤ 15% |
| **选股偏好** | 追动量、追热点、追涨停 | 低吸、等回调、找低估 |
| **买入时机** | RSI 60-80 也敢追 | 只在 RSI 30-50 时低吸 |
| **行业集中度** | 允许焊死一个赛道（如全仓AI） | 强制 2+ 行业分散 |
| **止损容忍** | -5% 严格执行（认错快） | -3% 就开始警觉 |
| **止盈策略** | 让利润奔跑，+15% 才考虑减仓 | +5% ~ +8% 分批锁定 |
| **适合场景** | 排名落后需要追赶、主线明确、市场强势 | 排名领先想守住、市场震荡、比赛末期 |

#### 如何在 Agent 中落实策略风格

选定风格后，在给 Agent A 的第一次对话中明确告诉它：

```
本次比赛我选择【进攻型/防守型】策略。
请你在写 task 文件给 Agent B 和 Agent C 时，
在任务开头注明这个策略风格，
让 B 在选股时、C 在审计时都会据此调整分析框架。
```

Agent A 会在每日的 task 文件中写明风格，B 和 C 的 startup 文件中也已内置了风格切换指引。

#### 风格可以动态调整

- 比赛初期 / 排名落后 / 主线明确 → 偏进攻
- 比赛末期 / 已有利润想守住 / 市场剧烈波动 → 偏防守
- 也可以混搭：进攻选股 + 防守仓位（B 大胆推荐，但 A 控制实际买入量）

**一句话总结：策略风格是你的事，Agent 只负责在你设定的框架内做到最好。先想清楚再开工。**

## Python 自动交易机器人

机器人（`new_trading_bot/`）通过 Playwright 操控 ChoiceLab 网页交易平台，替代人工点击。

### 命令速查

| 命令 | 用途 |
|------|------|
| `python main.py --status` | 读取账户状态、持仓、排名、收益率 |
| `python main.py --positions` | 仅读取持仓列表 |
| `python main.py --trade` | 执行 `advice.json`（终端展示计划，按 y 确认后下单） |
| `python main.py --trade --auto` | 直接执行，无需人工确认 |
| `python main.py --watch --headless` | 后台持续监控 + 自动止损止盈 |
| `python main.py --signals` | 输出量化策略选股信号 |
| `python main.py --login` | 首次登录（手机验证码后保存 Cookie） |
| `python main.py --analyze` | 调试页面结构（页面改版时使用） |

### 核心接口：`advice.json`

Agent A 每天盘后产出一份 `advice.json`，机器人读取后自动执行。这就是人（Agent A）和机器（Bot）之间的**唯一接口**。

```json
{
  "sell": [
    {
      "code": "000333",
      "name": "美的集团",
      "amount": 0,
      "reason": "触发止损 -5.1%，无条件卖出"
    }
  ],
  "buy": [
    {
      "code": "601138",
      "name": "工业富联",
      "amount": 900,
      "price": 75.00,
      "reason": "AgentB推荐：放量突破20日高点，RSI 62"
    }
  ],
  "reasoning": "止损美的，低吸富联，仓位从35%升至48%，现金充裕"
}
```

- `amount: 0` 表示卖出全部可用股数
- `price` 可省略，省略则机器人自动取实时市价
- 执行成功后文件自动改名为 `advice.done.json`，防止重复触发
- 当日无操作也必须写文件：`sell: []`, `buy: []`, `reasoning: "今日观望"`

## 每日工作流

```
15:30 收盘后
  ├─ Agent A：写复盘日志 → 更新账户快照 → 给 B/C 写任务文件
  ├─ Agent B：独立选股分析 → 结果写回任务文件
  ├─ Agent C：风险审计 → 审计意见写回任务文件
  └─ Agent A：交叉验证 → 汇总决策 → 写入 advice.json

次日 09:15 盘前
  └─ python main.py --trade → 执行 advice.json 中的买卖计划

09:30 ~ 15:00 盘中
  └─ python main.py --watch --headless → 每5分钟刷新数据 + 自动止损止盈
```

## 风控护栏（自动保护）

以下规则由机器人在执行前自动校验，**违规订单直接拒绝，无法绕过**：

| 规则 | 阈值 |
|------|------|
| 单只仓位上限 | ≤ 33% 总资产 |
| 总持仓上限 | ≤ 55% |
| 现金下限 | ≥ 30% |
| 最多持仓数 | 6 只 |
| 手数校验 | 必须是 100 的整数倍 |
| 科创板（688xxx） | 允许，最少 200 股 |
| 创业板（300xxx） | 默认禁用 |
| 北交所（4/8/92xxx） | 默认禁用 |

所有参数可在 `new_trading_bot/config/settings.yaml` 中调整。

## 项目结构

```
├── README.md                           # 本文件
├── LICENSE                             # MIT 开源协议
│
├── agentA/                             # Agent A（主操盘手）
│   └── AGENT_A_MANUAL.md               # ★ A 的完整操作手册（必读）
│
├── agentB/                             # Agent B（独立分析师）
│   ├── agentB_startup.md               # B 的角色说明
│   ├── task_YYYYMMDD.md                # 每日任务（AgentA 写入）
│   └── result_YYYYMMDD.md              # 每日分析结果（B 写入）
│
├── agentC/                             # Agent C（风险审计师）
│   ├── agentC_startup.md               # C 的角色说明
│   ├── task_YYYYMMDD.md                # 每日任务（AgentA 写入）
│   └── result_YYYYMMDD.md              # 每日审计结果（C 写入）
│
├── new_trading_bot/                    # ★ Python 自动交易引擎
│   ├── main.py                         # 入口，所有命令从这里运行
│   ├── requirements.txt                # Python 依赖
│   ├── core/
│   │   ├── actions.py                  # 功能封装（持仓/账户/交易/选股/监控）
│   │   ├── browser_ops.py              # Playwright 页面操作
│   │   ├── executor.py                 # 交易执行编排
│   │   ├── risk.py                     # 风控护栏引擎
│   │   ├── strategy.py                 # 量化策略信号
│   │   ├── login.py                    # Cookie 登录管理
│   │   └── data_fetcher.py             # akshare 市场数据获取
│   ├── config/
│   │   └── settings.yaml               # 风控参数配置
│   └── utils/
│       ├── logger.py                   # 日志工具
│       └── notifier.py                 # 终端美化输出
│
├── scripts/                            # 选股/分析脚本（AgentB 工具集）
│   ├── deep_dive.mjs                   # 单只股票深度诊断
│   ├── agent_b_quality_entry.mjs       # 质量估值模型
│   └── ak_data.py                      # akshare 数据工具
│
└── data/
    └── README.md                        # 本地授权数据的使用说明
```

## 数据源

| 数据类型 | 来源 | 方式 |
|----------|------|------|
| 基本面 (PE/ROE/EPS) | 用户自行取得的授权数据 | 通过 `FUNDAMENTALS_CSV` 指定本地 CSV |
| 实时报价 | 新浪财经 | `hq.sinajs.cn/list=` (HTTPS) |
| 日K线 + 技术指标 | 东方财富 | akshare `stock_zh_a_daily` |
| 涨停板 | 东方财富 | akshare `stock_zt_pool_em` |
| 龙虎榜 | 东方财富 | akshare `stock_lhb_detail_em` |
| 实时持仓/委托 | ChoiceLab 交易页 | Playwright 直接读取页面 DOM |

## 比赛规则（ChoiceLab A股 T+1）

- **T+1 制度**：当日买入的股票次日才能卖出
- **交易费**：约 0.05% ~ 0.1%，频繁进出会侵蚀收益
- **单只仓位** ≤ 1/3 总资产
- **止损线 -5%**：无条件执行
- **止盈参考 +8%**：考虑部分止盈（非强制）
- **保留 ≥ 30% 现金**：始终留有弹药

## 环境配置

### 前置要求

- **ChoiceLab 账号**：根据老师要求在 [choicelab.eastmoney.com](https://choicelab.eastmoney.com/) 注册并登录，在大赛内完成开户操作后即可使用本系统
- Python 3.10+
- Node.js 18+（选股脚本需要）
- Windows / macOS / Linux 均可

### 安装步骤

```bash
# 克隆仓库
git clone <仓库地址>
cd <项目目录>

# 安装 Python 机器人
cd new_trading_bot
python -m venv venv

# Windows:
.\venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate

pip install -r requirements.txt
playwright install chromium

# 首次登录（手动完成手机验证码，Cookie 自动保存）
python main.py --login
```

### 快速开始

```bash
# 查看账户状态
python main.py --status

# 执行 AgentA 的决策
python main.py --trade

# 启动盘中监控 + 自动止损止盈
python main.py --watch --headless --interval 5
```

### Agent 启动方式

每个 Agent 在独立的 Claude/LLM 会话中运行：

1. **Agent B**：新开会话，让它先读 `agentB/agentB_startup.md` 了解角色，再处理当日 `task_` 文件
2. **Agent C**：新开会话，让它先读 `agentC/agentC_startup.md` 了解角色，再审计当日方案（同时参考 B 的结果）
3. **Agent A**：新开会话，让它先读 `agentA/AGENT_A_MANUAL.md` 了解完整流程，按手册逐步操作

任务文件和结果文件统一使用日期命名（`task_YYYYMMDD.md`、`result_YYYYMMDD.md`），历史分析记录完整可追溯。

## 适用场景

本系统特别适合以下情况：

- **A股模拟交易比赛**：有明确的排名竞争和时间期限
- **想尝试 AI 协作决策**：不放心让单个 AI 直接操盘，希望通过多 Agent 交叉验证降低风险
- **T+1 制度下的波段操作**：不适合高频交易，但适用于"盘后分析 → 次日执行"的日频节奏
- **需要自动执行**：不想手动在网页上点来点去，希望由脚本自动完成下单、止损、止盈

## 已知局限

- **依赖 ChoiceLab 平台页面结构**：如果平台前端改版，`browser_ops.py` 中的 CSS 选择器可能需要更新
- **Cookie 有时效**：登录态过期后需重新运行 `--login`
- **不适合高频/日内交易**：整个工作流是盘后分析 → 次日执行，跟不上盘中实时变化
- **Agent 本身不执行交易**：Agent 只做分析和决策建议，实际操作由 Python 机器人在人类确认后执行

## 开源协议

MIT License — 详见 [LICENSE](LICENSE) 文件。
