"""
ATR 止损止盈体系计算模块
来源: 交易策略_止损止盈ATR体系.md
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class DailyBar:
    """单日K线数据"""
    date: str          # 日期，格式 YYYY-MM-DD
    open: float        # 开盘价
    high: float        # 最高价
    low: float         # 最低价
    close: float       # 收盘价


@dataclass
class CalcedBar(DailyBar):
    """附加计算字段的K线"""
    tr: float = 0.0    # True Range（价格值）
    tr_pct: float = 0.0  # TR%（TR / 前日收盘价）


# ─────────────────── 1. TR 计算 ───────────────────

def calc_tr(bar: DailyBar, prev_close: float) -> float:
    """
    计算单日 True Range（价格值）。
    
    公式:
        TR = max(当日高 − 当日低, |当日高 − 前收|, |当日低 − 前收|)
    
    参数:
        bar: 当日K线
        prev_close: 前日收盘价
    
    返回:
        TR 值
    """
    return max(
        bar.high - bar.low,
        abs(bar.high - prev_close),
        abs(bar.low - prev_close),
    )


def calc_tr_pct(bar: DailyBar, prev_close: float) -> float:
    """
    计算单日 TR%。
    
    公式:
        TR% = TR / 前日收盘价
    
    注意:
        根据规则，分母"绝不用当日收盘价"。
    
    参数:
        bar: 当日K线
        prev_close: 前日收盘价（作为分母）
    
    返回:
        TR%（小数形式，如 0.035 表示 3.5%）
    """
    if prev_close <= 0:
        raise ValueError(f"前日收盘价必须大于0，实际: {prev_close}")
    tr = calc_tr(bar, prev_close)
    return tr / prev_close


def build_calced_bars(bars: List[DailyBar]) -> List[CalcedBar]:
    """
    将原始K线列表转换为带 TR/TR% 的计算K线列表。
    
    第0根K线无法计算 TR%（需要前收），TR/TR% 为 0。
    """
    result: List[CalcedBar] = []
    for i, bar in enumerate(bars):
        if i == 0:
            tr, tr_pct = 0.0, 0.0
        else:
            prev_close = bars[i - 1].close
            tr = calc_tr(bar, prev_close)
            tr_pct = tr / prev_close if prev_close > 0 else 0.0
        result.append(CalcedBar(
            date=bar.date, open=bar.open, high=bar.high, low=bar.low, close=bar.close,
            tr=tr, tr_pct=tr_pct,
        ))
    return result


# ─────────────── 2. 买入日期 ATR 计算 ───────────────

def calc_entry_atr(bars: List[DailyBar], buy_date: str) -> float:
    """
    计算入场ATR（买入日 ATR%）。
    
    规则:
        - 取买入日前14个交易日的逐日 TR% 的均值
        - 首次计算后锁定不变
    
    参数:
        bars: 按时间升序排列的K线列表
        buy_date: 买入日期 (YYYY-MM-DD)，查找该日期在 bars 中的位置
    
    返回:
        入场ATR%（小数形式，如 0.035）
    """
    calced = build_calced_bars(bars)
    
    # 找到买入日在列表中的索引
    buy_idx = _find_index(calced, buy_date)
    if buy_idx is None:
        raise ValueError(f"买入日期 {buy_date} 不在K线数据中")
    
    # 取买入日前14个交易日（索引 buy_idx-14 到 buy_idx-1）
    start = buy_idx - 14
    if start < 1:
        raise ValueError(
            f"买入日期 {buy_date} 前不足14个交易日（需要至少前14日TR%数据），"
            f"实际可用: {buy_idx - 1} 根"
        )
    
    window = calced[start:buy_idx]
    tr_pcts = [b.tr_pct for b in window if b.tr_pct > 0]
    if not tr_pcts:
        raise ValueError(f"买入日期前14日无有效TR%数据")
    
    return sum(tr_pcts) / len(tr_pcts)


# ─────────────── 3. 当前日期 ATR 计算 ───────────────

def calc_current_atr(bars: List[DailyBar], current_date: str) -> float:
    """
    计算当前ATR（当前日期 ATR%）。
    
    规则:
        - 最近14个交易日逐日 TR% 均值
        - 每日刷新
    
    参数:
        bars: 按时间升序排列的K线列表
        current_date: 当前日期 (YYYY-MM-DD)
    
    返回:
        当前ATR%（小数形式）
    """
    calced = build_calced_bars(bars)
    
    idx = _find_index(calced, current_date)
    if idx is None:
        raise ValueError(f"当前日期 {current_date} 不在K线数据中")
    
    # 最近14个交易日（当前日期及之前13日）
    start = max(1, idx - 13)
    window = calced[start:idx + 1]
    tr_pcts = [b.tr_pct for b in window if b.tr_pct > 0]
    
    if not tr_pcts:
        raise ValueError(f"{current_date} 近14日无有效TR%数据")
    
    return sum(tr_pcts) / len(tr_pcts)


# ─────────────── 4. 止损点 ───────────────

def calc_stop_loss(entry_atr: float, cost: float) -> dict:
    """
    计算止损触发价。
    
    规则:
        触发比例 = max(7%, 2 × 入场ATR)
        触发价    = 成本 × (1 − 触发比例)
    
    参数:
        entry_atr: 入场ATR%（小数，如 0.035）
        cost:      买入成本价
    
    返回:
        {
            "trigger_ratio": 触发比例（小数）,
            "trigger_price": 触发价,
            "risk_per_share": 每股风险金额,
        }
    """
    trigger_ratio = max(0.07, 2.0 * entry_atr)
    trigger_price = cost * (1.0 - trigger_ratio)
    
    return {
        "trigger_ratio": trigger_ratio,
        "trigger_price": round(trigger_price, 4),
        "risk_per_share": round(cost - trigger_price, 4),
    }


# ─────────────── 5. 止盈点 ───────────────

def calc_take_profit_stage1(cost: float, current_atr: float) -> dict:
    """
    计算第一阶段止盈（减仓50%）。
    
    规则:
        条件: 盘中最高价浮盈 ≥ 5 × 当前ATR
        触发价 = 成本 × (1 + 5 × 当前ATR)
    
    参数:
        cost:       买入成本价
        current_atr: 当前ATR%（小数）
    
    返回:
        {
            "trigger_price": 触发价,
            "profit_pct":   目标涨幅,
            "action":       "减仓50%",
        }
    """
    profit_pct = 5.0 * current_atr
    trigger_price = cost * (1.0 + profit_pct)
    
    return {
        "trigger_price": round(trigger_price, 4),
        "profit_pct": round(profit_pct, 4),
        "action": "减仓50%",
    }


def calc_take_profit_stage2(peak_high: float, current_atr: float) -> dict:
    """
    计算第二阶段止盈（余仓清仓，移动止盈）。
    
    规则:
        条件: 剩余仓位触发移动止盈
        触发价 = 盘中最高价 × (1 − 2 × 当前ATR)
        峰值: 止盈触发后的盘中最高价（非收盘价），每日更新
    
    参数:
        peak_high:   止盈触发后的盘中最高价
        current_atr: 当前ATR%（小数）
    
    返回:
        {
            "trigger_price": 触发价,
            "action":        "余仓清仓",
        }
    """
    trigger_price = peak_high * (1.0 - 2.0 * current_atr)
    
    return {
        "trigger_price": round(trigger_price, 4),
        "action": "余仓清仓",
    }


# ─────────────── 6. 仓位上限 ───────────────

def calc_position_limit(entry_atr: float) -> dict:
    """
    计算仓位上限。
    
    规则:
        仓位上限 = 0.7% / (2 × 入场ATR%)
    
    参数:
        entry_atr: 入场ATR%（小数，如 0.035）
    
    返回:
        {
            "position_limit_pct": 仓位上限百分比（如 10.0 表示10%）,
            "formula":            计算公式描述,
        }
    """
    if entry_atr <= 0:
        raise ValueError(f"入场ATR必须大于0，实际: {entry_atr}")
    
    position_limit = 0.007 / (2.0 * entry_atr)
    
    return {
        "position_limit_pct": round(position_limit * 100, 2),
        "formula": f"0.7% / (2 × {entry_atr*100:.2f}%) = {position_limit*100:.2f}%",
    }


# ─────────────── 辅助函数 ───────────────

def _find_index(bars: list, date: str) -> Optional[int]:
    """在K线列表中按日期查找索引"""
    for i, b in enumerate(bars):
        if b.date == date:
            return i
    return None


# ─────────────── 综合计算入口 ───────────────

def calc_all(bars: List[DailyBar], buy_date: str, cost: float,
             current_date: Optional[str] = None,
             peak_high: Optional[float] = None) -> dict:
    """
    一站式综合计算：止损、止盈、仓位上限。
    
    参数:
        bars:         按时间升序排列的K线列表
        buy_date:     买入日期 (YYYY-MM-DD)
        cost:         买入成本价
        current_date: 当前日期（默认取最后一天）
        peak_high:    第二阶段止盈峰值（盘中最高价）
    
    返回:
        完整计算结果字典
    """
    if current_date is None:
        current_date = bars[-1].date
    
    entry_atr = calc_entry_atr(bars, buy_date)
    current_atr = calc_current_atr(bars, current_date)
    
    result = {
        "buy_date": buy_date,
        "current_date": current_date,
        "cost": cost,
        "entry_atr_pct": round(entry_atr * 100, 4),
        "current_atr_pct": round(current_atr * 100, 4),
        "stop_loss": calc_stop_loss(entry_atr, cost),
        "take_profit_stage1": calc_take_profit_stage1(cost, current_atr),
        "position_limit": calc_position_limit(entry_atr),
    }
    
    if peak_high is not None:
        result["take_profit_stage2"] = calc_take_profit_stage2(peak_high, current_atr)
    
    return result


# ─────────────── 演示 / 测试 ───────────────

if __name__ == "__main__":
    # 构造14+天的模拟数据用于演示
    import random
    random.seed(42)
    
    base_price = 100.0
    bars = []
    for i in range(30):
        date = f"2026-05-{i+1:02d}"
        o = base_price
        h = round(base_price * (1 + random.uniform(0, 0.06)), 2)
        l = round(base_price * (1 - random.uniform(0, 0.05)), 2)
        c = round(base_price * (1 + random.uniform(-0.03, 0.04)), 2)
        bars.append(DailyBar(date=date, open=o, high=h, low=l, close=c))
        base_price = c  # 下一天基准
    
    buy_date = "2026-05-25"
    cost = bars[_find_index(bars, buy_date)].close
    
    result = calc_all(
        bars=bars,
        buy_date=buy_date,
        cost=cost,
        current_date="2026-05-30",
        peak_high=cost * 1.25,
    )
    
    print("=" * 60)
    print("ATR 止损止盈体系 — 计算结果")
    print("=" * 60)
    print(f"买入日期:       {result['buy_date']}")
    print(f"当前日期:       {result['current_date']}")
    print(f"成本价:         {result['cost']}")
    print(f"入场ATR:        {result['entry_atr_pct']}%")
    print(f"当前ATR:        {result['current_atr_pct']}%")
    print("-" * 40)
    
    sl = result["stop_loss"]
    print(f"止损触发比例:   {sl['trigger_ratio']*100:.2f}%")
    print(f"止损触发价:     {sl['trigger_price']}")
    print(f"每股风险:       {sl['risk_per_share']}")
    print("-" * 40)
    
    tp1 = result["take_profit_stage1"]
    print(f"止盈①触发价:    {tp1['trigger_price']}  ({tp1['action']})")
    print(f"止盈①目标涨幅:  {tp1['profit_pct']*100:.2f}%")
    
    if "take_profit_stage2" in result:
        tp2 = result["take_profit_stage2"]
        print(f"止盈②触发价:    {tp2['trigger_price']}  ({tp2['action']})")
    print("-" * 40)
    
    pl = result["position_limit"]
    print(f"仓位上限:       {pl['position_limit_pct']}%")
    print(f"  计算公式:     {pl['formula']}")
    print("=" * 60)
