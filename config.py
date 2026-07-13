# -*- coding: utf-8 -*-
"""集中設定與路徑。
- 真實設定與金鑰、個資、產出全部放在 private/（不上 Git）。
- 公開範本見 config.example.json / sites.example.json。
"""
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRIVATE_DIR = os.path.join(BASE_DIR, "private")  # 機敏/個資/產出 集中地（gitignore）
os.makedirs(PRIVATE_DIR, exist_ok=True)

# config.json 優先讀 private/，找不到再退回根目錄、最後範本
_CANDIDATES = [os.path.join(PRIVATE_DIR, "config.json"),
               os.path.join(BASE_DIR, "config.json"),
               os.path.join(BASE_DIR, "config.example.json")]
_PATH = next((p for p in _CANDIDATES if os.path.exists(p)), _CANDIDATES[-1])
_cfg = json.load(open(_PATH, encoding="utf-8"))


def get(key, default=None):
    return os.environ.get(key.upper(), _cfg.get(key, default))


def _abspath(v):
    if not v:
        return v
    return v if os.path.isabs(v) else os.path.join(BASE_DIR, v)


# ── 設定值 ──
AI_BASE_URL = get("ai_base_url")
AI_MODEL = get("ai_model")
AI_API_KEY = get("ai_api_key", "none")
GA_KEY_FILE = _abspath(get("ga_key_file", "private/ga-service-account.json"))
MASTER_SHEET_ID = get("master_sheet_id")
# 唯一站清單母表(原 TCGweb466站清單;原「主設定表」已併入退役)。
# 月度合規檢核的站 = 此表中「合規檢核」欄=是 的子集。
SITE_LIST_WS = get("site_list_ws", "府內網站表")
COMPLIANCE_FLAG_COL = "合規檢核"
LINK_AUDIT_DIR = _abspath(get("link_audit_dir", ""))  # 轉絕對路徑，避免 cwd 在 monthly/ 時找不到

# ── 標準路徑（產出與個資一律落在 private/）──
SITES_JSON = os.path.join(PRIVATE_DIR, "sites.json")
NODES_MAP = os.path.join(PRIVATE_DIR, "nodes_map.json")
REPORTS_DIR = os.path.join(PRIVATE_DIR, "reports")
CHECKLIST_DIR = os.path.join(PRIVATE_DIR, "檢核表")
