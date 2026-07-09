# -*- coding: utf-8 -*-
"""部署冒煙測試：確認新電腦上環境/設定/模組基本健全，不寫任何檔、不改線上資料。

用法: python smoke_test.py
全部 PASS 才代表這台可以跑 每月檢核.bat。
"""
import importlib
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ok = True


def check(name, cond, hint=""):
    global ok
    mark = "PASS" if cond else "FAIL"
    if not cond:
        ok = False
    print(f"[{mark}] {name}" + (f"  → {hint}" if not cond and hint else ""))


# 1. 第三方套件
for pkg in ["openpyxl", "gspread", "requests", "bs4", "google.auth"]:
    try:
        importlib.import_module(pkg)
        check(f"套件 {pkg}", True)
    except ImportError:
        check(f"套件 {pkg}", False, "請執行 pip install -r requirements.txt")

# 2. 本專案模組
for mod in ["config", "scan_settings", "webcheck_ai", "ga_traffic", "sync_config",
            "monthly_check", "update_excel", "node_check", "deep_check", "probe_method"]:
    try:
        importlib.import_module(mod)
        check(f"模組 {mod}", True)
    except Exception as e:
        check(f"模組 {mod}", False, f"{type(e).__name__}: {e}")

# 3. 設定與金鑰
import config
check("config.json 已設定(非範本佔位)", config.MASTER_SHEET_ID
      and "YOUR_" not in str(config.MASTER_SHEET_ID),
      "請複製 config.example.json → private/config.json 並填值")
check("AI 端點已設定", config.AI_BASE_URL and "YOUR-" not in str(config.AI_BASE_URL),
      "請在 private/config.json 填 ai_base_url")
check("GA 金鑰檔存在", os.path.exists(config.GA_KEY_FILE),
      f"請放置金鑰於 {config.GA_KEY_FILE}")
check("private/ 資料夾存在", os.path.isdir(config.PRIVATE_DIR))

print()
print("結果:", "全部通過，可部署 ✅" if ok else "有項目未通過，請依上方提示修正 ❌")
sys.exit(0 if ok else 1)
