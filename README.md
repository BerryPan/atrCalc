# ATR 止损止盈监控系统

基于 ATR（Average True Range）的止损、止盈、仓位管理一体化盘中监控系统。

- 📊 自动拉取行情数据，计算入场 ATR / 当前 ATR
- 🔴🟡🔻 实时监控止损线、止盈①（减半仓）、止盈②（清仓）是否触发
- 📱 飞书机器人实时推送告警
- 📁 每日结果快照存档

## 策略规则

| 指标 | 公式 |
|------|------|
| **TR** | `max(当日高−当日低, \|当日高−前收\|, \|当日低−前收\|)` |
| **TR%** | `TR / 前日收盘价`（分母绝不用当日收盘价） |
| **入场 ATR** | 首次建仓日前 14 日 TR% 均值，**首次计算后锁定不变** |
| **当前 ATR** | 最近 14 日 TR% 均值，**每日刷新** |
| **止损** | `max(7%, 2×入场ATR) × 加权成本` |
| **止盈①** | `加权成本 × (1 + 5×当前ATR)` → 减仓 50% |
| **止盈②** | `盘中峰值 × (1 − 2×当前ATR)` → 余仓清仓 |
| **仓位上限** | `0.7% / (2×入场ATR%)` |

> 加仓后成本取加权平均，入场 ATR 不变。

## 项目结构

```
atrCalc/
├── portfolio.json                       # 持仓配置（唯一需要手动编辑的文件）
├── monitor.py                           # 统一盘中监控脚本
├── atr_calc.py                          # ATR 核心计算模块
├── feishu_notify.py                     # 飞书机器人推送模块
├── cron_monitor.sh                      # crontab 入口
├── .env                                 # 飞书 Webhook URL（不入仓库）
├── .gitignore
├── 交易策略_止损止盈ATR体系.md            # 策略规则文档
├── state/                               # 各股票状态（TP1触发、峰值等，不入仓库）
│   └── sh600378.json
└── results/                             # 每日结果快照（不入仓库）
    └── 2026-06-17.json
```

## 快速开始

### 1. 配置飞书推送

```bash
# 编辑 .env，填入飞书机器人 Webhook URL
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxx
```

### 2. 配置持仓

编辑 `portfolio.json`：

```json
{
  "total_asset": 500000,
  "alert_threshold": 0.03,
  "stocks": [
    {
      "code": "sh600378",
      "name": "昊华科技",
      "first_buy_date": "2026-06-16",
      "trades": [
        {"date": "2026-06-16", "price": 58.80, "shares": 200}
      ]
    }
  ]
}
```

- `code`: A 股代码，沪市 `sh` 前缀，深市 `sz` 前缀
- `first_buy_date`: 首次建仓日期（入场 ATR 以此锁定）
- `trades`: 所有买入记录，加仓只需追加一笔

### 3. 部署定时任务

```bash
crontab -e
# 加入：
*/5 9-15 * * 1-5 /root/atrCalc/cron_monitor.sh >> /root/atrCalc/cron.log 2>&1
```

盘中每 5 分钟自动执行一次，非交易时段自动跳过。

### 4. 手动运行

```bash
cd /root/atrCalc && python3 monitor.py
```

## 飞书推送策略

| 场景 | 频率 | 卡片 |
|------|------|------|
| 🔴 止损触发 | 即时 | 红色卡片 |
| 🔴 止损预警（≤3%） | 每小时 1 次 | 橙色卡片 |
| 🟡 止盈①触发 | 即时 | 黄色卡片 |
| 🟡 止盈①预警 | 每小时 1 次 | 黄色卡片 |
| 🔻 止盈②触发 | 即时 | 紫色卡片 |
| 🔻 止盈②预警 | 每小时 1 次 | 紫色卡片 |
| 📊 整点状态 | 每日 1 次 | 蓝色卡片 |

## 依赖

- Python 3
- Node.js 18+（`westock-data` 数据源）
- `westock-data` 插件

## License

MIT
