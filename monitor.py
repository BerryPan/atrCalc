#!/usr/bin/env python3
"""
ATR 止损止盈 — 统一盘中监控
================================================
- 读取 portfolio.json 获取持仓配置
- 遍历所有股票：拉行情 → 算ATR/三线/仓位 → 检查触发 → 飞书推送
- 结果写入 results/YYYY-MM-DD.json（每天只保留最后一次）
- 每只股票状态独立保存在 state/{code}.json

规则:
  入场ATR  = 首次建仓日前14日TR%均值（锁定不变）
  成本     = 加仓后加权平均成本（做T不影响成本）
  止损     = max(7%, 2×入场ATR) × 加权成本
  止盈①    = 加权成本 × (1 + 5×当前ATR)，当前ATR每日刷新
  止盈②    = 盘中峰值 × (1 - 2×当前ATR)
  仓位上限 = 0.7% / (2×入场ATR)
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from typing import Optional

# ── 路径 ──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from atr_calc import (
    DailyBar,
    calc_entry_atr, calc_current_atr,
    calc_stop_loss, calc_take_profit_stage1, calc_take_profit_stage2,
    calc_position_limit,
)
import feishu_notify as feishu

PORTFOLIO_FILE = os.path.join(BASE_DIR, "portfolio.json")
STATE_DIR = os.path.join(BASE_DIR, "state")
RESULTS_DIR = os.path.join(BASE_DIR, "results")
ALERT_FILE = os.path.join(BASE_DIR, "alert.log")

WESTOK_SCRIPT = "/root/.codebuddy/plugins/marketplaces/cb_teams_marketplace/plugins/finance-data/skills/westock-data/scripts/index.js"
NODE_BIN = "/root/.workbuddy/binaries/node/versions/20.18.0/bin/node"

os.makedirs(STATE_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)


# ══════════════════ 工具函数 ══════════════════

def is_trading_time() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return (9 * 60 + 30 <= t <= 11 * 60 + 30) or (13 * 60 <= t <= 15 * 60)


def fetch_kline(code: str, limit: int = 100) -> list[DailyBar]:
    cmd = [NODE_BIN, WESTOK_SCRIPT, "kline", code, "--period", "day", "--limit", str(limit)]
    out = subprocess.check_output(cmd, text=True, timeout=30)
    bars = []
    for line in out.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("| date") or line.startswith("| ---"):
            continue
        parts = [p.strip() for p in line.split("|") if p.strip()]
        if len(parts) < 5:
            continue
        try:
            d, o, c, h, l = parts[0], float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
            bars.append(DailyBar(date=d, open=o, high=h, low=l, close=c))
        except (ValueError, IndexError):
            continue
    bars.reverse()
    return bars


def fetch_realtime_price(code: str) -> Optional[float]:
    cmd = [NODE_BIN, WESTOK_SCRIPT, "quote", code]
    out = subprocess.check_output(cmd, text=True, timeout=15)
    for line in out.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("| ---") or line.startswith("| code"):
            continue
        parts = [p.strip() for p in line.split("|") if p.strip()]
        if len(parts) >= 6 and parts[0] == code:
            try:
                return float(parts[5])
            except ValueError:
                pass
    bars = fetch_kline(code, 1)
    return bars[-1].close if bars else None


def load_portfolio() -> dict:
    with open(PORTFOLIO_FILE) as f:
        return json.load(f)


def load_state(code: str) -> dict:
    path = os.path.join(STATE_DIR, f"{code}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {
        "tp1_triggered": False,
        "peak_high": 0.0,
        "last_fs_sl": "",
        "last_fs_tp1": "",
        "last_fs_tp2": "",
        "last_fs_status": "",
    }


def save_state(code: str, state: dict):
    path = os.path.join(STATE_DIR, f"{code}.json")
    with open(path, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def log_alert(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(ALERT_FILE, "a") as f:
        f.write(line + "\n")


def save_daily_result(result: dict):
    """保存每日结果，覆盖当天已有文件（只保留最后一次）"""
    today = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(RESULTS_DIR, f"{today}.json")
    with open(path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)


# ══════════════════ 单股票处理 ══════════════════

def process_stock(stock_cfg: dict, alert_threshold: float, total_asset: float,
                  now: datetime) -> Optional[dict]:
    """处理单只股票，返回计算结果字典"""
    code = stock_cfg["code"]
    name = stock_cfg["name"]
    first_buy_date = stock_cfg["first_buy_date"]
    trades = stock_cfg["trades"]

    try:
        bars = fetch_kline(code, 100)
        price = fetch_realtime_price(code)
    except Exception as e:
        log_alert(f"❌ {name} {code} 数据获取失败: {e}")
        return None

    if not bars or price is None:
        log_alert(f"❌ {name} {code} 无法获取行情")
        return None

    kline_date = bars[-1].date

    # 1) 入场ATR（锁定）
    entry_atr = calc_entry_atr(bars, first_buy_date)

    # 2) 加权成本
    total_shares = sum(t["shares"] for t in trades)
    total_cost = sum(t["price"] * t["shares"] for t in trades)
    avg_cost = total_cost / total_shares

    # 3) 当前ATR（每日刷新）
    current_atr = calc_current_atr(bars, kline_date)

    # 4) 三线
    sl = calc_stop_loss(entry_atr, avg_cost)
    tp1 = calc_take_profit_stage1(avg_cost, current_atr)

    # 5) 状态 & 峰值
    state = load_state(code)
    if price > state["peak_high"]:
        state["peak_high"] = price
    effective_peak = max(state["peak_high"], price)
    tp2 = calc_take_profit_stage2(effective_peak, current_atr)
    pl = calc_position_limit(entry_atr)

    cur_value = price * total_shares
    pnl = cur_value - total_cost
    max_amount = total_asset * pl["position_limit_pct"] / 100
    position_ok = cur_value <= max_amount

    # 距离计算
    dist_sl = (price / sl["trigger_price"] - 1) * 100 if sl["trigger_price"] > 0 else 0
    dist_tp1 = (tp1["trigger_price"] / price - 1) * 100 if price > 0 else 0
    dist_tp2 = (price / tp2["trigger_price"] - 1) * 100 if tp2["trigger_price"] > 0 else 0

    # ── 显示 ──
    print(f"\n  📌 {name} {code}")
    print(f"     数据日期: {kline_date}  |  实时价: {price:.2f}")
    print(f"     入场ATR: {entry_atr*100:.2f}% (锁定)  |  当前ATR: {current_atr*100:.2f}%")
    print(f"     加权成本: {avg_cost:.2f} ({total_shares}股, ¥{total_cost:,.0f})")
    print(f"     🔴止损: {sl['trigger_price']:.2f} ({dist_sl:+.1f}%)  "
          f"🟡止盈①: {tp1['trigger_price']:.2f} ({dist_tp1:+.1f}%)  "
          f"🔻止盈②: {tp2['trigger_price']:.2f} ({dist_tp2:+.1f}%)")
    print(f"     📦仓位: {pl['position_limit_pct']}% (上限¥{max_amount:,.0f}) "
          f"| 当前¥{cur_value:,.0f} {'✅' if position_ok else '❌超标'}")
    print(f"     💵浮盈: ¥{pnl:,.0f} ({pnl/total_cost*100:+.1f}%)")
    if state["tp1_triggered"]:
        print(f"     ⚠️ 止盈①已触发，监控止盈②")

    # ── 触发判断 + 飞书推送 ──
    minute_key = now.strftime("%Y-%m-%d %H:%M")
    hour_key = now.strftime("%Y-%m-%d %H:00")
    alerts = []
    stock_label = f"{name} {code}"

    # 止损
    if price <= sl["trigger_price"]:
        alerts.append(f"🔴🔴🔴 {stock_label} 止损触发！{price:.2f} ≤ {sl['trigger_price']:.2f}")
        if state["last_fs_sl"] != minute_key:
            feishu.send_stop_loss_alert(price, sl['trigger_price'], avg_cost, stock_label)
            state["last_fs_sl"] = minute_key
    elif price <= sl["trigger_price"] * (1 + alert_threshold):
        alerts.append(f"🔴 {stock_label} 止损预警 (仅剩{dist_sl:.1f}%)")
        if state["last_fs_sl"] != hour_key:
            feishu.send_stop_loss_alert(price, sl['trigger_price'], avg_cost, stock_label)
            state["last_fs_sl"] = hour_key

    # 止盈①
    if not state["tp1_triggered"]:
        if price >= tp1["trigger_price"]:
            alerts.append(f"🟡🟡🟡 {stock_label} 止盈①触发！减仓50%")
            state["tp1_triggered"] = True
            feishu.send_tp1_alert(price, tp1['trigger_price'], avg_cost, stock_label)
            state["last_fs_tp1"] = minute_key
        elif price >= tp1["trigger_price"] * (1 - alert_threshold):
            alerts.append(f"🟡 {stock_label} 止盈①预警 (还差{dist_tp1:.1f}%)")
            if state["last_fs_tp1"] != hour_key:
                feishu.send_tp1_alert(price, tp1['trigger_price'], avg_cost, stock_label)
                state["last_fs_tp1"] = hour_key

    # 止盈②
    if state["tp1_triggered"]:
        if price <= tp2["trigger_price"]:
            alerts.append(f"🔻🔻🔻 {stock_label} 止盈②触发！余仓清仓")
            if state["last_fs_tp2"] != minute_key:
                feishu.send_tp2_alert(price, tp2['trigger_price'], effective_peak, stock_label)
                state["last_fs_tp2"] = minute_key
        elif price <= tp2["trigger_price"] * (1 + alert_threshold):
            alerts.append(f"🔻 {stock_label} 止盈②预警 (仅剩{dist_tp2:.1f}%)")
            if state["last_fs_tp2"] != hour_key:
                feishu.send_tp2_alert(price, tp2['trigger_price'], effective_peak, stock_label)
                state["last_fs_tp2"] = hour_key

    if alerts:
        for a in alerts:
            log_alert(a)
    else:
        print(f"     ✅ 安全")

    # 每日状态推送（无告警时，每只股票每天推一次）
    today_key = now.strftime("%Y-%m-%d")
    if not alerts and state.get("last_fs_status") != today_key:
        card = feishu._build_card(
            title=f"📊 {name} 盘中状态",
            content=(
                f"**{name}** {code}\n\n"
                f"当前价：**{price:.2f}**\n"
                f"加权成本：**{avg_cost:.2f}**（{total_shares}股）\n"
                f"入场ATR：**{entry_atr*100:.2f}%** | 当前ATR：**{current_atr*100:.2f}%**\n\n"
                f"🔴 止损：**{sl['trigger_price']:.2f}**（{dist_sl:+.1f}%）\n"
                f"🟡 止盈①：**{tp1['trigger_price']:.2f}**（{dist_tp1:+.1f}%）\n"
                f"🔻 止盈②：**{tp2['trigger_price']:.2f}**（峰值{effective_peak:.2f}）\n\n"
                f"浮盈：**¥{pnl:,.0f}**（{pnl/total_cost*100:+.1f}%）"
            ),
            color="blue",
        )
        feishu._send(card)
        state["last_fs_status"] = today_key

    state["last_run"] = now.strftime("%Y-%m-%d %H:%M:%S")
    save_state(code, state)

    # 返回结构化结果
    return {
        "code": code,
        "name": name,
        "kline_date": kline_date,
        "price": round(price, 2),
        "first_buy_date": first_buy_date,
        "trades": trades,
        "total_shares": total_shares,
        "avg_cost": round(avg_cost, 4),
        "total_cost": round(total_cost, 2),
        "entry_atr_pct": round(entry_atr * 100, 2),
        "current_atr_pct": round(current_atr * 100, 2),
        "stop_loss": {
            "trigger_ratio_pct": round(sl["trigger_ratio"] * 100, 2),
            "trigger_price": sl["trigger_price"],
            "distance_pct": round(dist_sl, 1),
        },
        "take_profit_1": {
            "trigger_price": tp1["trigger_price"],
            "profit_pct": round(tp1["profit_pct"] * 100, 2),
            "distance_pct": round(dist_tp1, 1),
        },
        "take_profit_2": {
            "trigger_price": tp2["trigger_price"],
            "peak_high": round(effective_peak, 2),
            "distance_pct": round(dist_tp2, 1),
        },
        "position_limit_pct": pl["position_limit_pct"],
        "max_amount": round(max_amount, 2),
        "current_value": round(cur_value, 2),
        "position_ok": position_ok,
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl / total_cost * 100, 1),
        "tp1_triggered": state["tp1_triggered"],
        "alerts": alerts,
    }


# ══════════════════ 主入口 ══════════════════

def main():
    now = datetime.now()
    portfolio = load_portfolio()
    total_asset = portfolio["total_asset"]
    alert_threshold = portfolio.get("alert_threshold", 0.03)
    stocks = portfolio["stocks"]

    print(f"\n{'='*60}")
    print(f" ATR止损止盈 盘中监控  {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f" 持仓 {len(stocks)} 只 | 总资产 ¥{total_asset:,}")
    print(f"{'='*60}")

    if not is_trading_time():
        print("  ⏸  非交易时段，跳过检查")
        return

    all_results = []
    total_pnl = 0
    total_value = 0
    total_invested = 0
    has_alert = False

    for stock_cfg in stocks:
        result = process_stock(stock_cfg, alert_threshold, total_asset, now)
        if result:
            all_results.append(result)
            total_pnl += result["pnl"]
            total_value += result["current_value"]
            total_invested += result["total_cost"]
            if result["alerts"]:
                has_alert = True

    # 汇总
    print(f"\n  {'─'*50}")
    print(f"  📊 组合汇总")
    print(f"     总投入: ¥{total_invested:,.0f} | 总市值: ¥{total_value:,.0f} | 总浮盈: ¥{total_pnl:,.0f} ({total_pnl/total_invested*100:+.1f}%)")
    print(f"     仓位: {total_value/total_asset*100:.1f}% (¥{total_value:,.0f} / ¥{total_asset:,})")
    if has_alert:
        print(f"     ⚠️  有告警触发！")
    else:
        print(f"     ✅ 全部安全")

    # 保存每日结果
    daily_result = {
        "run_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "total_asset": total_asset,
        "total_invested": round(total_invested, 2),
        "total_value": round(total_value, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl / total_invested * 100, 1),
        "position_pct": round(total_value / total_asset * 100, 1),
        "has_alert": has_alert,
        "stocks": all_results,
    }
    save_daily_result(daily_result)
    print(f"     📁 结果已保存: results/{now.strftime('%Y-%m-%d')}.json")
    print(f"  {'─'*50}\n")


if __name__ == "__main__":
    main()
