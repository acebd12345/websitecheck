# -*- coding: utf-8 -*-
"""
分層探測每個網站「該用哪種方式抓內容」，結果寫回 Google Sheet 站清單母表(府內網站表)的
「內容抓取方式」欄。以後 monthly_check / node_check 直接照這欄決定怎麼抓，不再每次重猜。

分層(由便宜到貴)：
  code        靜態抓得到內容、且能解析出結構(找到日期項目)  → 直接 parse
  ai          靜態有內容但結構雜               → 抓文字交地端AI判讀
  playwright  靜態是 JS 空殼，內容靠JS渲染       → 需無頭瀏覽器渲染後再判
  manual      連渲染也讀不到(如3D地圖/登入牆)     → 人工檢視

用法:
  python probe_method.py            探測全部網站並寫回府內網站表
  python probe_method.py --dry      只探測、印結果，不寫回
"""
import re
import sys
import ssl
import urllib.request

import gspread
import config

KEY = config.GA_KEY_FILE
SHEET_ID = config.MASTER_SHEET_ID
SITE_LIST_WS = config.SITE_LIST_WS
COL_NAME = "內容抓取方式"

HDR = {"User-Agent": "Mozilla/5.0", "Accept": "text/html,*/*;q=0.8", "Accept-Language": "zh-TW"}
CTX_VERIFY = ssl.create_default_context()          # 預設：驗證憑證
CTX_NOVERIFY = ssl.create_default_context()        # 退回：憑證鏈不完整時用
CTX_NOVERIFY.check_hostname = False
CTX_NOVERIFY.verify_mode = ssl.CERT_NONE

# 已知特例：純地圖/視覺應用，自動化讀不到內容
MANUAL_HOSTS = {"3d.taipei"}

DATE_RE = re.compile(r"(11\d\s*[/.-]?\s*\d{1,2}\s*[/.-]\s*\d{1,2}"
                     r"|\d{4}\s*[/-]\s*\d{1,2}\s*[/-]\s*\d{1,2}"
                     r"|11\d\s*年\s*\d{1,2}\s*月|\d{1,2}\s*/\s*\d{1,2})")


def visible_text(html):
    t = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def fetch(url):
    req = urllib.request.Request(url, headers=HDR)
    try:
        r = urllib.request.urlopen(req, timeout=20, context=CTX_VERIFY)
    except ssl.SSLError:  # 憑證鏈不完整 → 退回不驗證
        r = urllib.request.urlopen(req, timeout=20, context=CTX_NOVERIFY)
    return r.read().decode("utf-8", "replace")


def probe_one(url):
    """回傳 (方式, 判定理由)"""
    host = urllib.parse_host(url) if hasattr(urllib, "parse_host") else \
        re.sub(r"^https?://([^/]+).*", r"\1", url)
    if any(host == h or host.endswith("." + h) for h in MANUAL_HOSTS):
        return "manual", "純3D/地圖應用，需人工目視"
    try:
        html = fetch(url)
    except Exception as e:
        return "manual", f"連線失敗({type(e).__name__})，需人工確認"
    text = visible_text(html)
    n = len(text)
    dates = DATE_RE.findall(text)
    # JS 空殼判定：可見內容極少
    if n < 350:
        return "playwright", f"靜態可見內容僅{n}字，疑JS渲染空殼"
    # 有內容：能找到日期項目 → code 可解析；否則交 AI
    if len(dates) >= 2:
        return "code", f"靜態{n}字、偵測到{len(dates)}個日期項目，可直接解析"
    if n >= 800:
        return "ai", f"靜態{n}字有內容但無明確日期結構，交AI判讀"
    return "ai", f"靜態{n}字內容偏少，先試AI(不足再改playwright)"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    dry = "--dry" in sys.argv
    gc = gspread.service_account(filename=KEY)
    ws = gc.open_by_key(SHEET_ID).worksheet(SITE_LIST_WS)
    rows = ws.get_all_values()
    header = rows[0]
    # 找/建「內容抓取方式」欄
    if COL_NAME in header:
        col = header.index(COL_NAME) + 1
    else:
        col = len(header) + 1
        if not dry:
            if ws.col_count < col:
                ws.add_cols(col - ws.col_count)
            ws.update_cell(1, col, COL_NAME)
    url_col = header.index("網址")

    results = []
    cells = []  # 收集要寫入的儲存格，最後一次批次寫回(避免逐格觸發 API 寫入限制)
    col_letter = gspread.utils.rowcol_to_a1(1, col).rstrip("1")
    for i, r in enumerate(rows[1:], start=2):
        urls = [u.strip() for u in r[url_col].split(";") if u.strip()]
        if not urls:
            continue
        current = r[col - 1].strip() if len(r) >= col else ""
        # playwright / manual 是人工確認的決定，不被自動探測覆寫
        if current in ("playwright", "manual"):
            results.append((r[1], current, "保留人工設定，不覆寫"))
            print(f"  {r[1][:22]:24} → {current:11} (保留人工設定)")
            continue
        method, reason = probe_one(urls[0])
        results.append((r[1], method, reason))
        print(f"  {r[1][:22]:24} → {method:11} {reason}")
        cells.append({"range": f"{col_letter}{i}", "values": [[method]]})

    if not dry and cells:
        ws.batch_update(cells)  # 一次寫回全部

    print()
    from collections import Counter
    c = Counter(m for _, m, _ in results)
    print("統計:", dict(c))
    if not dry:
        print(f"已寫回府內網站表「{COL_NAME}」欄")


if __name__ == "__main__":
    main()
