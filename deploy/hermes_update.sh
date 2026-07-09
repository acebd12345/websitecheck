#!/bin/bash
# Hermes 主機拉取式部署(CD):排程掃描前呼叫,或每日 cron 獨立跑。
# 流程:fetch → 有新版才 ff-only 合併+裝依賴 → 冒煙測試當部署閘門。
# 冒煙不過會以非零退出——排程腳本應該據此中止當次掃描並告警,不要帶病上陣。
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

git fetch origin main
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
if [ "$LOCAL" != "$REMOTE" ]; then
    git merge --ff-only origin/main
    pip install -r requirements.txt -q
    echo "[deploy] 已更新 ${LOCAL:0:7} -> ${REMOTE:0:7}"
else
    echo "[deploy] 已是最新 ${LOCAL:0:7}"
fi

# 部署閘門:19 項全 PASS 才放行(需 private/ 設定與金鑰已就位)
PYTHONPATH="$ROOT" python3 "$ROOT/monthly/smoke_test.py"
