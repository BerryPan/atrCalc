#!/bin/bash
# ATR止损止盈 统一盘中监控 — crontab入口
# 用法: */5 9-15 * * 1-5 /root/atrCalc/cron_monitor.sh >> /root/atrCalc/cron.log 2>&1
cd /root/atrCalc
[ -f .env ] && export $(grep -v '^#' .env | grep FEISHU_WEBHOOK_URL | xargs)
python3 monitor.py
