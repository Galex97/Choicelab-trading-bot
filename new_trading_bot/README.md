# TradingBot - A股争霸赛交易机器人

东方财富 ChoiceLab 比赛专用，配合 Agent 工作流使用。

---

## 快速上手

```bash
cd new_trading_bot
.\venv\Scripts\activate

# 首次登录（必须）
python main.py --login
```

---

## 命令速查

| 命令 | 作用 | 输出文件 |
|------|------|---------|
| `python main.py --login` | 打开浏览器登录，保存 Cookie | — |
| `python main.py --positions` | 读取当前持仓 | `output/positions.json` |
| `python main.py --status` | 读取账户+持仓+排名+收益率 | `output/account_status.json` |
| `python main.py --trade` | 读 advice.json，执行买卖（需人工确认） | `output/last_trade.json` |
| `python main.py --trade --auto` | 读 advice.json，**无需确认**直接执行 | `output/last_trade.json` |
| `python main.py --signals` | 量化策略选股信号（不连浏览器） | `output/signals.json` |
| `python main.py --watch` | 每5分钟自动刷新账户状态 | `output/account_status.json` |
| `python main.py --watch --interval 3` | 每3分钟刷新 | `output/account_status.json` |
| `python main.py --analyze` | 调试：截图+导出页面DOM | `logs/screenshots/` |

### 附加选项

| 选项 | 作用 |
|------|------|
| `--headless` | 不显示浏览器窗口（适合后台运行） |
| `--advice-file PATH` | 指定 advice.json 路径（默认 `../advice.json`） |
| `--interval N` | 配合 `--watch`，设置刷新间隔（分钟） |
| `--auto` | 配合 `--trade`，跳过人工确认 |

---

## Agent 工作流

### 读取持仓（复盘/监控用）

```bash
python main.py --status --headless
# 结果在 output/account_status.json
```

`account_status.json` 格式：
```json
{
  "timestamp": "2026-06-16 09:30:15",
  "account": {
    "total_assets": 1050000,
    "available_cash": 300000,
    "market_value": 750000,
    "profit": 50000,
    "profit_pct": 5.0
  },
  "ranking": {"rank": 12},
  "positions": [
    {
      "code": "000333", "name": "美的集团",
      "amount": 1000, "cost_price": 72.5, "current_price": 75.2,
      "profit_pct": 3.72, "market_value": 75200.0
    }
  ],
  "summary": "总资产105.0万 | 收益+5.00% | 排名第12名 | 持仓3只"
}
```

### 执行买卖

Agent 将决策写入 `advice.json`：

```json
{
  "sell": [
    {"code": "000333", "name": "美的集团", "reason": "止盈8%"}
  ],
  "buy": [
    {"code": "601138", "name": "工业富联", "amount": 900, "reason": "动量信号"}
  ],
  "reasoning": "美的止盈，换仓工业富联"
}
```

然后执行：
```bash
python main.py --trade --auto --headless
```

风控护栏会自动检查：单票仓位限制、总仓位限制、资金余额等。结果写入 `output/last_trade.json`。

### 策略选股（供 Agent 参考）

```bash
python main.py --signals
# 结果在 output/signals.json
```

`signals.json` 包含动量策略、龙头股策略、突破策略的综合信号，Agent 可作为买入参考之一。

### 持续监控（Watch 模式）

```bash
# 后台每5分钟自动刷新，Agent 随时可读 account_status.json
python main.py --watch --headless --interval 5
```

---

## 目录结构

```
new_trading_bot/
├── main.py                 ← 入口，所有命令从这里调用
├── core/
│   ├── actions.py          ← 核心功能封装（持仓/状态/交易/选股/监控）
│   ├── browser_ops.py      ← Playwright 页面操作（买/卖/读持仓/读账户）
│   ├── login.py            ← Cookie 登录管理
│   ├── risk.py             ← 风控护栏（交易前过滤）
│   ├── strategy.py         ← 量化策略引擎（动量/龙头/突破）
│   ├── data_fetcher.py     ← akshare 市场数据
│   ├── ai_advisor.py       ← LLM 建议生成（可选，需配置 API key）
│   └── executor.py         ← 交易执行编排（被 actions.py 调用）
├── config/
│   ├── settings.yaml       ← 风控参数、策略配置
│   └── cookies.json        ← 登录 Cookie（自动生成，勿手动修改）
├── output/                 ← JSON 输出目录（Agent 读取）
│   ├── positions.json
│   ├── account_status.json
│   ├── signals.json
│   └── last_trade.json
└── logs/                   ← 运行日志和截图
```

---

## 配置

`config/settings.yaml` 风控关键参数：

```yaml
risk:
  max_single_position_ratio: 0.33  # 单票最大仓位 33%
  max_total_position_ratio: 0.55   # 总仓位上限 55%
  min_cash_ratio: 0.30             # 最低现金比例 30%
  max_positions: 6                 # 最多持仓只数
  allow_star_market: true          # 允许科创板（68xxxx）
  allow_chinext: false             # 不允许创业板（30xxxx）
```

---

## 常见问题

**Q: 运行报错 "无法获取登录状态"**
```bash
python main.py --login  # 重新登录，更新 Cookie
```

**Q: 买卖没有执行成功**
查看 `output/last_trade.json` 中的 `trades_executed` 字段，了解每笔交易的 `success` 和 `message`。

**Q: 策略信号慢**  
`--signals` 命令会为每只候选股拉取历史 K 线，首次运行较慢（约 3~8 分钟），
数据有 60 秒缓存，同一进程内不会重复拉取。
