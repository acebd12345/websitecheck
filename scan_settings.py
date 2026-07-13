# -*- coding: utf-8 -*-
"""掃描設定單一來源:Google Sheet「掃描設定」分頁 → 本機快取 → 內建預設。

詞庫(賭博/色情/停放頁關鍵字)與分頁參數以前散在 audit_links/full_overnight/crawl
各自維護一份,改一邊另一邊不會同步(漂移)。改為:
  Sheet「掃描設定」分頁(承辦可調) → private/scan_settings_cache.json(快取) → DEFAULTS(內建)

用法:
  - 主流程(full_overnight)啟動時呼叫一次 refresh():
    讀 Sheet 成功 → 更新快取;失敗 → 沿用既有快取或內建預設(印警示,不中斷)。
  - 其他任何地方(含 ProcessPoolExecutor 子行程)只呼叫 get(key):
    讀快取檔,絕不打 Sheet API(466 個子行程各打一次會撞配額)。
  - get() 保證回非空清單:Sheet 該列缺漏/清空時退回內建預設——
    詞庫變空 = 整夜掃描零候選、默默漏光,比程式掛掉更糟。

比對「語意」(整字邊界、善意詞剔除、裸搜計數)留在使用端程式,Sheet 只管「詞」。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

SHEET_WS = "掃描設定"
CACHE_PATH = os.path.join(config.PRIVATE_DIR, "scan_settings_cache.json")

DEFAULTS = {
    # 第一關圈候選的內容詞(audit_links 整字邊界比對+善意詞剔除;也供第三關定性)
    "suspicious_keywords": [
        "娛樂城", "百家樂", "博弈", "博彩", "賭場", "老虎機", "捕魚機", "六合彩",
        "casino", "baccarat", "slot", "betting", "poker", "jackpot",
        "色情", "成人影片", "成人視訊", "情色", "約砲", "av女優", "無碼",
        "porn", "hentai", "xvideo", "live sex", "escort",
        "виагра", "viagra", "cialis",
    ],
    # 停放/出售頁詞(只給第一關;第三關的停放判定走 AI verdict B,不吃這組)
    "parked_keywords": [
        "domain is for sale", "buy this domain", "此網域可供出售", "域名出售",
        "parked domain", "sedoparking", "godaddy.com/domainsearch",
    ],
    # 第三關定性補充詞:短詞/品牌片段(dewa77、situs judi),只適合裸搜,
    # 整字邊界比對反而抓不到(dewa 後面接 77 會被邊界規則擋掉),故不併入 suspicious
    "characterize_extra_keywords": ["sex", "dewa", "judi", "gacor", "situs"],
    # 比對前先剔除的善意詞(白色情人節撞「色情」、註冊商把 .casino 當商品名賣)
    "benign_phrases": ["白色情人節", ".casino", ".poker", ".bet", ".slot", ".xxx", ".sexy", ".porn"],
    # 分頁/月曆參數:URL 帶這些參數視為分頁,不再往下挖(月曆無限頁陷阱)
    "pagination_params": [
        "page", "pagesize", "offset", "limit", "start", "count", "p", "pn",
        "pageindex", "pageno", "cid", "date", "month", "year", "yy", "mm",
    ],
}

_cache_mem = None  # 每行程讀一次快取檔


def _parse_rows(rows):
    """從分頁的原始列(參數|值|型別|用途)撈出本模組認得的參數;值=逗號分隔。"""
    out = {}
    for row in rows:
        if len(row) >= 2 and row[0].strip() in DEFAULTS:
            vals = [v.strip() for v in str(row[1]).split(",") if v.strip()]
            if vals:
                out[row[0].strip()] = vals
    return out


def refresh():
    """讀 Sheet「掃描設定」→ 寫本機快取。回 (成功?, 訊息)。失敗不丟例外、不中斷主流程。"""
    global _cache_mem
    try:
        import gspread
        gc = gspread.service_account(filename=config.GA_KEY_FILE)
        ws = gc.open_by_key(config.MASTER_SHEET_ID).worksheet(SHEET_WS)
        found = _parse_rows(ws.get_all_values())
        data = {"fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"), "values": found}
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        _cache_mem = found
        missing = [k for k in DEFAULTS if k not in found]
        msg = f"掃描設定:Sheet 讀到 {len(found)} 組參數"
        if missing:
            msg += f",缺 {','.join(missing)}(用內建預設)"
        return True, msg
    except Exception as e:
        src = "既有快取" if os.path.exists(CACHE_PATH) else "內建預設"
        return False, f"掃描設定:讀 Sheet 失敗({type(e).__name__}),沿用{src}"


def _load_cache():
    global _cache_mem
    if _cache_mem is None:
        try:
            _cache_mem = json.load(open(CACHE_PATH, encoding="utf-8")).get("values", {})
        except Exception:
            _cache_mem = {}
    return _cache_mem


def get(key):
    """取設定值(list)。快取 → 內建預設,保證非空。"""
    v = _load_cache().get(key)
    return list(v) if v else list(DEFAULTS[key])
