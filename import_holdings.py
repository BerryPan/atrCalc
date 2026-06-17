#!/usr/bin/env python3
"""
从 holdings_data.json 读取交易记录，FIFO 匹配后生成 portfolio.json
"""
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 代码前缀映射：6xxxxx→sh, 0xxxxx/2xxxxx/3xxxxx→sz, 5xxxxx→sh(ETF)
def to_westock_code(code: str) -> str:
    if code.startswith(("60", "51", "56", "58")):
        return f"sh{code}"
    return f"sz{code}"


def fifo_match(trades: list) -> list[dict]:
    """
    FIFO 匹配，返回当前仍持有的买入批次。
    每笔 match 返回: {date, price, qty}（qty > 0）
    """
    # 按日期排序
    sorted_trades = sorted(trades, key=lambda t: t["date"])
    # 用队列模拟 FIFO：每个元素 [date, price, qty]
    buys = []
    for t in sorted_trades:
        if t["op"] == "BUY":
            buys.append([t["date"], t["price"], t["qty"]])
        else:  # SELL — 从最早的买入开始扣
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


def format_date(d: str) -> str:
    """20260302 → 2026-03-02"""
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


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
        
        remaining = fifo_match(trades_raw)
        if not remaining:
            print(f"⚠️  {name} {code}: FIFO匹配后无持仓，跳过")
            continue
        
        total_shares = sum(r["shares"] for r in remaining)
        first_buy_date = min(r["date"] for r in remaining)
        
        stocks.append({
            "code": to_westock_code(code),
            "name": name,
            "first_buy_date": first_buy_date,
            "trades": remaining,
        })
        
        print(f"  {name} {code}")
        print(f"    首笔建仓日: {first_buy_date}")
        print(f"    持仓 {total_shares} 股（{len(remaining)} 笔建仓）")
        for r in remaining:
            print(f"      {r['date']}  {r['price']:.2f} x {r['shares']}股")
    
    # 按首笔建仓日期排序
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
