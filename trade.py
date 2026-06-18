#!/usr/bin/env python3
"""
交易录入工具
================================================
录入买卖交易，自动更新 holdings_data.json 并重新生成 portfolio.json。

用法:
  python3 trade.py                            # 交互模式（推荐每日录入）
  python3 trade.py BUY 159246 1.434 16300     # 命令行单笔买入
  python3 trade.py SELL 601138 74.48 300      # 命令行单笔卖出
  python3 trade.py SELL 601138 74.48 300 工业富联  # 带名称（新股票需要）
  python3 trade.py --date 20260618 BUY 159246 1.434 16300 SELL 601138 74.48 300  # 多笔+指定日期

规则:
  - 买入新股票自动加入 current；卖出清仓后自动移入 closed
  - 之前已清仓的股票重新买入，自动从 closed 移回 current
  - 金额 = 价格 × 数量，自动计算
"""

import json
import os
import sys
import subprocess
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOLDINGS_FILE = os.path.join(BASE_DIR, "holdings_data.json")


# ════════════════ 数据读写 ════════════════

def load_holdings() -> dict:
    with open(HOLDINGS_FILE) as f:
        return json.load(f)


def save_holdings(data: dict):
    with open(HOLDINGS_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ════════════════ 单笔交易录入 ════════════════

def record_trade(data: dict, date: str, op: str, code: str,
                 price: float, qty: float, name: str = None) -> bool:
    """录入一笔交易，更新 holdings_data 结构。返回是否成功。"""
    amount = round(price * qty, 2)
    current = data["current"]
    closed = data["closed"]

    # ── 确定股票名称 ──
    if name is None:
        if code in current:
            name = current[code]["name"]
        elif code in closed:
            name = closed[code]["name"]
        else:
            name = code

    # ── 卖出前检查持仓 ──
    if op == "SELL":
        if code not in current:
            print(f"  ⚠️  {name} {code} 不在当前持仓中，无法卖出，跳过")
            return False
        hold_qty = current[code]["net_qty"]
        if qty > hold_qty:
            print(f"  ⚠️  {name} {code} 卖出 {int(qty)} 股超过持仓 {int(hold_qty)} 股，请确认！")
            return False

    # ── 1) 追加交易记录 ──
    if code not in data["trades"]:
        data["trades"][code] = []
    data["trades"][code].append({
        "date": date,
        "op": op,
        "price": price,
        "qty": qty,
        "amount": amount,
    })

    # ── 2) 更新持仓汇总 ──
    if op == "BUY":
        # 之前已清仓 → 从 closed 移回 current（保留历史 total_proceeds）
        if code in closed and code not in current:
            old = closed.pop(code)
            current[code] = {
                "name": name,
                "net_qty": 0.0,
                "total_cost": 0.0,
                "total_proceeds": old["total_proceeds"],
            }
        # 全新股票
        if code not in current:
            current[code] = {
                "name": name,
                "net_qty": 0.0,
                "total_cost": 0.0,
                "total_proceeds": 0.0,
            }
        current[code]["name"] = name
        current[code]["net_qty"] = round(current[code]["net_qty"] + qty, 2)
        current[code]["total_cost"] = round(current[code]["total_cost"] + amount, 2)

    else:  # SELL
        current[code]["net_qty"] = round(current[code]["net_qty"] - qty, 2)
        current[code]["total_proceeds"] = round(current[code]["total_proceeds"] + amount, 2)
        # 清仓 → 移到 closed
        if current[code]["net_qty"] <= 0:
            closed[code] = current.pop(code)
            print(f"  📋 {name} {code} 已清仓，移至 closed")

    print(f"  ✅ {op} {name} {code}  {price} × {int(qty)}股  = ¥{amount:,.2f}  ({date})")
    return True


# ════════════════ 重新生成 portfolio ════════════════

def regenerate_portfolio():
    """调用 import_holdings.py 重新生成 portfolio.json"""
    print(f"\n  🔄 重新生成 portfolio.json ...")
    subprocess.run(
        [sys.executable, os.path.join(BASE_DIR, "import_holdings.py")],
        cwd=BASE_DIR,
    )


# ════════════════ 交互模式 ════════════════

def interactive():
    """交互模式：逐笔录入当日交易"""
    data = load_holdings()
    today = datetime.now().strftime("%Y%m%d")

    print(f"\n{'='*55}")
    print(f"  📝 交易录入（交互模式）")
    print(f"{'='*55}")

    date = input(f"  交易日期 YYYYMMDD（回车=今天 {today}）: ").strip() or today

    count = 0
    while True:
        print(f"\n  {'─'*45}")
        op = input("  操作 BUY/SELL（回车=结束录入）: ").strip().upper()
        if op == "" or op not in ("BUY", "SELL"):
            break

        code = input("  股票代码（如 601138）: ").strip()
        if not code:
            print("  ⚠️ 代码不能为空")
            continue

        # 显示已有名称供参考
        existing_name = None
        if code in data["current"]:
            existing_name = data["current"][code]["name"]
        elif code in data["closed"]:
            existing_name = data["closed"][code]["name"]
        hint = f"已有:{existing_name}" if existing_name else "输入名称"
        name = input(f"  股票名称（回车={hint}）: ").strip()
        if not name:
            name = existing_name

        try:
            price = float(input("  价格: ").strip())
            qty = float(input("  数量: ").strip())
        except ValueError:
            print("  ⚠️ 价格/数量格式错误，请重新输入")
            continue

        if price <= 0 or qty <= 0:
            print("  ⚠️ 价格和数量必须大于 0")
            continue

        # 预览确认
        amount = round(price * qty, 2)
        print(f"\n  ┌ 预览")
        print(f"  │ {op}  {name or code}  {code}")
        print(f"  │ {price} × {int(qty)} 股 = ¥{amount:,.2f}")
        print(f"  │ 日期: {date}")
        confirm = input("  └ 确认录入? (y/n): ").strip().lower()
        if confirm != "y":
            print("  已跳过")
            continue

        if record_trade(data, date, op, code, price, qty, name):
            count += 1

    # 保存
    print(f"\n{'='*55}")
    if count == 0:
        print(f"  未录入任何交易，退出")
        print(f"{'='*55}")
        return

    save_holdings(data)
    print(f"  💾 holdings_data.json 已保存（共 {count} 笔交易）")
    print(f"{'='*55}")
    regenerate_portfolio()
    print(f"\n  ✅ 录入完成\n")


# ════════════════ 命令行模式 ════════════════

def cli_mode(args: list):
    """命令行模式：解析参数录入交易"""
    data = load_holdings()
    today = datetime.now().strftime("%Y%m%d")
    date = today
    count = 0

    i = 0
    while i < len(args):
        # --date 参数
        if args[i] == "--date":
            if i + 1 >= len(args):
                print("⚠️ --date 后缺少日期值")
                return
            date = args[i + 1]
            i += 2
            continue

        # 需要 4 个参数: OP CODE PRICE QTY
        if i + 4 > len(args):
            print(f"⚠️ 参数不足，用法: trade.py <OP> <CODE> <PRICE> <QTY> [NAME]")
            break

        op = args[i].upper()
        code = args[i + 1]
        try:
            price = float(args[i + 2])
            qty = float(args[i + 3])
        except ValueError:
            print(f"⚠️ 价格/数量格式错误: {args[i+2]} / {args[i+3]}")
            break

        name = None
        i += 4
        # 检查下一个参数是否是名称（不是 OP 也不是 --date）
        if i < len(args) and args[i] not in ("BUY", "SELL", "--date"):
            name = args[i]
            i += 1

        if op not in ("BUY", "SELL"):
            print(f"⚠️ 无效操作: {op}（应为 BUY 或 SELL）")
            continue

        if record_trade(data, date, op, code, price, qty, name):
            count += 1

    if count == 0:
        print("未录入任何交易")
        return

    save_holdings(data)
    print(f"\n💾 holdings_data.json 已保存（共 {count} 笔交易）")
    regenerate_portfolio()
    print(f"\n✅ 完成")


# ════════════════ 主入口 ════════════════

def main():
    args = sys.argv[1:]
    if not args:
        interactive()
    else:
        cli_mode(args)


if __name__ == "__main__":
    main()
