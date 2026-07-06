#!/usr/bin/env python3
"""输出持仓及ATR止损止盈信息（非交易时段用最近收盘价）"""

import json
import os
import subprocess
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from atr_calc import (
    DailyBar,
    calc_entry_atr, calc_current_atr,
    calc_stop_loss, calc_take_profit_stage1, calc_take_profit_stage2,
    calc_position_limit,
)

WESTOK_SCRIPT = "/root/.codebuddy/plugins/marketplaces/cb_teams_marketplace/plugins/finance-data/skills/westock-data/scripts/index.js"
NODE_BIN = "/root/.workbuddy/binaries/node/versions/20.18.0/bin/node"


def fetch_kline(code: str, limit: int = 100) -> list:
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


def fetch_realtime_price(code: str) -> float:
    """获取实时价，非交易时段返回最近收盘价"""
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


def main():
    with open(os.path.join(BASE_DIR, "portfolio.json")) as f:
        portfolio = json.load(f)

    stocks = portfolio["stocks"]
    total_asset = portfolio["total_asset"]

    print(f"\n{'='*90}")
    print(f"  持仓 ATR 止损止盈一览  ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    print(f"{'='*90}")

    all_results = []

    for s in stocks:
        code = s["code"]
        name = s["name"]
        first_buy = s["first_buy_date"]
        trades = s["trades"]
        net_invested = s["net_invested"]

        total_shares = sum(t["shares"] for t in trades)
        total_cost = sum(t["price"] * t["shares"] for t in trades)
        avg_cost = total_cost / total_shares

        try:
            bars = fetch_kline(code, 200)
            price = fetch_realtime_price(code)
        except Exception as e:
            print(f"  ❌ {name} {code} 数据获取失败: {e}")
            continue

        if not bars or price is None:
            print(f"  ❌ {name} {code} 无行情数据")
            continue

        kline_date = bars[-1].date

        try:
            entry_atr = calc_entry_atr(bars, first_buy)
            current_atr = calc_current_atr(bars, kline_date)
        except Exception as e:
            print(f"  ❌ {name} {code} ATR计算失败: {e}")
            continue
        sl = calc_stop_loss(entry_atr, avg_cost)
        tp1 = calc_take_profit_stage1(avg_cost, current_atr)
        tp2 = calc_take_profit_stage2(price, current_atr)
        pl = calc_position_limit(entry_atr)

        cur_value = price * total_shares
        pnl = cur_value - net_invested
        pnl_pct = pnl / net_invested * 100 if net_invested > 0 else 0
        max_amount = total_asset * pl["position_limit_pct"] / 100

        dist_sl = (price / sl["trigger_price"] - 1) * 100
        dist_tp1 = (tp1["trigger_price"] / price - 1) * 100
        dist_tp2 = (price / tp2["trigger_price"] - 1) * 100

        all_results.append({
            "code": code, "name": name,
            "price": price, "avg_cost": avg_cost,
            "shares": total_shares,
            "net_invested": net_invested,
            "pnl": pnl, "pnl_pct": pnl_pct,
            "entry_atr": entry_atr, "current_atr": current_atr,
            "sl": sl, "tp1": tp1, "tp2": tp2,
            "pl": pl, "max_amount": max_amount,
            "cur_value": cur_value,
            "dist_sl": dist_sl, "dist_tp1": dist_tp1, "dist_tp2": dist_tp2,
        })

        status = "⚠️止损" if price <= sl["trigger_price"] else "✅ 安全"
        print(f"\n  ┌─ {name} {code}")
        print(f"  ├ 现价: {price:.2f}  |  加权成本: {avg_cost:.2f}  |  {total_shares}股  |  净投入: ¥{net_invested:,.0f}")
        print(f"  ├ 浮盈: ¥{pnl:,.0f} ({pnl_pct:+.1f}%)  |  仓位: {cur_value/total_asset*100:.1f}%  |  上限: {pl['position_limit_pct']}% (¥{max_amount:,.0f})")
        print(f"  ├ 入场ATR: {entry_atr*100:.2f}% (锁定)  |  当前ATR: {current_atr*100:.2f}%")
        print(f"  ├ 🔴止损: {sl['trigger_price']:.2f}  ({dist_sl:+.1f}%)")
        print(f"  ├ 🟡止盈①: {tp1['trigger_price']:.2f}  ({dist_tp1:+.1f}%)")
        print(f"  └ 🔻止盈②: {tp2['trigger_price']:.2f}  ({dist_tp2:+.1f}%)  |  {status}")

    # 汇总
    if all_results:
        total_pnl = sum(r["pnl"] for r in all_results)
        total_value = sum(r["cur_value"] for r in all_results)
        total_invested = sum(r["net_invested"] for r in all_results)
        print(f"\n{'─'*90}")
        print(f"  📊 组合汇总")
        print(f"     总投入: ¥{total_invested:,.0f}  |  总市值: ¥{total_value:,.0f}  |  总浮盈: ¥{total_pnl:,.0f} ({total_pnl/total_invested*100:+.1f}%)")
        print(f"     仓位: {total_value/total_asset*100:.1f}%  (¥{total_value:,.0f} / ¥{total_asset:,})")
        print(f"{'─'*90}\n")


if __name__ == "__main__":
    main()
