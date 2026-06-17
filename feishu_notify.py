"""
飞书通知模块
通过飞书自定义机器人 Webhook 发送告警消息。
"""

import json
import urllib.request
import os
from datetime import datetime
from typing import Optional

# ══════════════ 配置 ══════════════
# 优先级：环境变量 > .env 文件

def _load_webhook_url() -> str:
    """加载飞书 Webhook URL，优先读环境变量，其次读 .env 文件"""
    url = os.environ.get("FEISHU_WEBHOOK_URL", "")
    if url:
        return url
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() == "FEISHU_WEBHOOK_URL":
                        return v.strip().strip('"').strip("'")
    return ""

FEISHU_WEBHOOK_URL = _load_webhook_url()

# ══════════════ 卡片模板 ══════════════

def _build_card(title: str, content: str, color: str) -> dict:
    """构造飞书消息卡片"""
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,  # red, yellow, green, blue, purple, orange
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": content}},
                {"tag": "hr"},
                {
                    "tag": "note",
                    "elements": [
                        {"tag": "plain_text", "content": f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"}
                    ],
                },
            ],
        },
    }


def send_stop_loss_alert(price: float, trigger_price: float, cost: float, stock: str):
    """止损触发/预警告警"""
    is_triggered = price <= trigger_price
    gap_pct = (price / trigger_price - 1) * 100

    if is_triggered:
        title = f"🔴🔴🔴 {stock} 止损触发！"
        color = "red"
        content = (
            f"**{stock}** 触发止损线\n\n"
            f"成本价：**{cost:.2f}**\n"
            f"止损线：**{trigger_price:.2f}**\n"
            f"当前价：**{price:.2f}**\n\n"
            f"⚠️ 请立即执行止损操作！"
        )
    else:
        title = f"🔴 {stock} 止损预警"
        color = "orange"
        content = (
            f"**{stock}** 逼近止损线\n\n"
            f"成本价：**{cost:.2f}**\n"
            f"止损线：**{trigger_price:.2f}**\n"
            f"当前价：**{price:.2f}**\n"
            f"距止损仅剩 **{gap_pct:.1f}%**\n\n"
            f"⚠️ 请密切关注！"
        )

    _send(_build_card(title, content, color))


def send_tp1_alert(price: float, trigger_price: float, cost: float, stock: str):
    """止盈①触发/预警告警"""
    is_triggered = price >= trigger_price
    gap_pct = (trigger_price / price - 1) * 100

    if is_triggered:
        title = f"🟡🟡🟡 {stock} 止盈①触发！"
        color = "yellow"
        content = (
            f"**{stock}** 触达止盈第一阶段\n\n"
            f"成本价：**{cost:.2f}**\n"
            f"目标价：**{trigger_price:.2f}**\n"
            f"当前价：**{price:.2f}**\n\n"
            f"📋 操作：**减仓 50%**\n"
            f"剩余仓位进入移动止盈监控"
        )
    else:
        title = f"🟡 {stock} 止盈①预警"
        color = "yellow"
        content = (
            f"**{stock}** 逼近止盈目标\n\n"
            f"成本价：**{cost:.2f}**\n"
            f"目标价：**{trigger_price:.2f}**\n"
            f"当前价：**{price:.2f}**\n"
            f"还差 **{gap_pct:.1f}%** 触及\n\n"
            f"📋 触及后将减仓 50%"
        )

    _send(_build_card(title, content, color))


def send_tp2_alert(price: float, trigger_price: float, peak: float, stock: str):
    """止盈②触发/预警告警"""
    is_triggered = price <= trigger_price
    gap_pct = (price / trigger_price - 1) * 100

    if is_triggered:
        title = f"🔻🔻🔻 {stock} 止盈②触发！"
        color = "purple"
        content = (
            f"**{stock}** 触发移动止盈清仓线\n\n"
            f"盘中峰值：**{peak:.2f}**\n"
            f"清仓线：**{trigger_price:.2f}**\n"
            f"当前价：**{price:.2f}**\n\n"
            f"📋 操作：**余仓全部清仓！**"
        )
    else:
        title = f"🔻 {stock} 止盈②预警"
        color = "purple"
        content = (
            f"**{stock}** 逼近移动止盈清仓线\n\n"
            f"盘中峰值：**{peak:.2f}**\n"
            f"清仓线：**{trigger_price:.2f}**\n"
            f"当前价：**{price:.2f}**\n"
            f"距清仓仅剩 **{gap_pct:.1f}%**\n\n"
            f"⚠️ 请密切关注！"
        )

    _send(_build_card(title, content, color))


def send_status_report(stock: str, price: float, sl_price: float, tp1_price: float, tp2_price: float,
                       entry_atr_pct: float, current_atr_pct: float, tp1_triggered: bool = False):
    """整点状态汇报"""
    title = f"📊 {stock} 盘中状态"
    color = "blue"
    active_mode = "止盈②（余仓清仓）" if tp1_triggered else "止盈①（减仓50%）"
    content = (
        f"**{stock}** 盘中三线状态\n\n"
        f"当前价：**{price:.2f}**\n"
        f"入场ATR：**{entry_atr_pct:.2f}%** | 当前ATR：**{current_atr_pct:.2f}%**\n\n"
        f"🔴 止损线：**{sl_price:.2f}**\n"
        f"🟡 止盈①：**{tp1_price:.2f}**\n"
        f"🔻 止盈②：**{tp2_price:.2f}**（峰值回撤锁定）\n\n"
        f"当前监控模式：{active_mode}"
    )
    _send(_build_card(title, content, color))


def send_error(msg: str):
    """错误通知"""
    title = "❌ 监控异常"
    color = "red"
    content = f"ATR 监控脚本运行异常：\n\n{msg}"
    _send(_build_card(title, content, color))


def _send(card: dict):
    """发送飞书消息"""
    if not FEISHU_WEBHOOK_URL:
        print("[飞书] Webhook 未配置，跳过推送")
        return
    try:
        data = json.dumps(card).encode("utf-8")
        req = urllib.request.Request(
            FEISHU_WEBHOOK_URL,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("code") == 0:
                print("[飞书] 推送成功")
            else:
                print(f"[飞书] 推送失败: {result}")
    except Exception as e:
        print(f"[飞书] 推送异常: {e}")


# ══════════════ 测试 ══════════════

if __name__ == "__main__":
    # 本地测试
    if not FEISHU_WEBHOOK_URL:
        print("请设置环境变量 FEISHU_WEBHOOK_URL 后测试")
        print("示例: export FEISHU_WEBHOOK_URL='https://open.feishu.cn/open-apis/bot/v2/hook/xxx'")
    else:
        send_status_report(
            stock="昊华科技 sh600378",
            price=63.80,
            sl_price=47.45,
            tp1_price=89.15,
            tp2_price=50.63,
            entry_atr_pct=9.65,
            current_atr_pct=10.32,
        )
        print("测试消息已发送，请检查飞书群")
