#!/usr/bin/env python3
"""
从 holdings_data.json 读取交易记录，识别清仓后重建仓，
只保留最近一个建仓周期的交易，FIFO匹配后生成 portfolio.json。

浮盈 = 当前市值 - (建仓周期内总买入 - 总卖出)
"""
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def to_westock_code(code: str) -> str:
    """6开头→sh, 0/2/3开头→sz, 5开头→sh(ETF)"""
    if code.startswith(("60", "51", "56", "58")):
        return f"sh{code}"
    return f"sz{code}"


def format_date(d: str) -> str:
    """20260302 → 2026-03-02"""
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def find_latest_position_cycle(trades: list) -> list:
    """
    从交易记录中找出最近一个建仓周期（清仓后重新建仓开始）。
    
    从前向后累加净持仓，当净持仓回到0时标记清仓点，
    最后一个清仓点之后的交易就是当前持仓周期。
    """
    sorted_trades = sorted(trades, key=lambda t: t["date"])
    
    net = 0.0
    last_zero_idx = -1  # 最后一次净持仓归0的交易索引
    for i, t in enumerate(sorted_trades):
        delta = t["qty"] if t["op"] == "BUY" else -t["qty"]
        net += delta
        if net <= 0:
            last_zero_idx = i
            net = 0.0
    
    # 从最后一次清仓的下一条交易开始
    return sorted_trades[last_zero_idx + 1:]


def fifo_match(trades: list) -> list[dict]:
    """
    FIFO 匹配，返回当前仍持有的买入批次。
    返回: [{date, price, shares}]
    """
    sorted_trades = sorted(trades, key=lambda t: t["date"])
    buys = []
    for t in sorted_trades:
        if t["op"] == "BUY":
            buys.append([t["date"], t["price"], t["qty"]])
        else:
            sell_qty = t["qty"]
            while sell_qty > 0 and buys:
                if buys[0][2] <= sell_qty:
                    sell_qty -= buys[0][2]
                    buys.pop(0)
                else:
                    buys[0][2] -= sell_qty
                    sell_qty = 0
    
    return [
        {"date": format_date(b[0]), "price": b[1], "shares": int(b[2])}
        for b in buys
    ]


def main():
    with open(os.path.join(BASE_DIR, "holdings_data.json")) as f:
        data = json.load(f)
    
    stocks = []
    
    for code, info in data["current"].items():
        name = info["name"]
        net_qty = info["net_qty"]
        trades_raw = data["trades"].get(code, [])
        
        if net_qty <= 0 or not trades_raw:
            continue
        
        # 找出最近一个建仓周期的交易
        cycle_trades = find_latest_position_cycle(trades_raw)
        
        # FIFO 匹配
        remaining = fifo_match(cycle_trades)
        if not remaining:
            print(f"⚠️  {name} {code}: FIFO匹配后无持仓，跳过")
            continue
        
        total_shares = sum(r["shares"] for r in remaining)
        first_buy_date = min(r["date"] for r in remaining)
        
        # 计算建仓周期内的总买入和总卖出（用于浮盈计算）
        total_buy = sum(t["amount"] for t in cycle_trades if t["op"] == "BUY")
        total_sell = sum(t["amount"] for t in cycle_trades if t["op"] == "SELL")
        net_invested = total_buy - total_sell
        
        stocks.append({
            "code": to_westock_code(code),
            "name": name,
            "first_buy_date": first_buy_date,
            "trades": remaining,
            "net_invested": round(net_invested, 2),
            "cycle_start": format_date(cycle_trades[0]["date"]),
        })
        
        print(f"  {name} {code}")
        print(f"    建仓周期: {format_date(cycle_trades[0]['date'])} 起")
        print(f"    首笔建仓(FIFO): {first_buy_date}")
        print(f"    持仓 {total_shares} 股（{len(remaining)} 笔）")
        print(f"    周期内买入: ¥{total_buy:,.0f} | 卖出: ¥{total_sell:,.0f} | 净投入: ¥{net_invested:,.0f}")
        for r in remaining:
            print(f"      {r['date']}  {r['price']:.2f} x {r['shares']}股")
    
    stocks.sort(key=lambda s: s["first_buy_date"])
    
    portfolio = {
        "total_asset": 500000,
        "alert_threshold": 0.03,
        "stocks": stocks,
    }
    
    out_path = os.path.join(BASE_DIR, "portfolio.json")
    with open(out_path, "w") as f:
        json.dump(portfolio, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ 已生成 portfolio.json，共 {len(stocks)} 只股票")


if __name__ == "__main__":
    main()
