#!/bin/bash
# 每日連結稽核排程進入點（Ubuntu cron 用）
# 設 PYTHONPATH 指向專案根目錄，讓 batch_audit 能讀共用 config
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT"
mkdir -p "$ROOT/private/logs"
LOG="$ROOT/private/logs/scan_$(date +%Y%m%d).log"
cd "$ROOT/daily"
# 先從主設定表同步出最新 domains.txt（失敗不中斷，沿用上次的清單）
python3 "$ROOT/monthly/sync_config.py" >> "$LOG" 2>&1 || echo "sync_config 失敗，沿用現有 domains.txt" >> "$LOG"
python3 batch_audit.py --daily >> "$LOG" 2>&1
