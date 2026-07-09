# -*- coding: utf-8 -*-
"""每站頁數自適應(存在 Google Sheet「頁數」欄,非另開檔)。

概念(使用者設計):
- 每站實際爬到幾頁,記在主試算表「TCGweb466站清單」的「頁數」欄(單一事實來源)
- 下次掃描上限 = 該站頁數 + BUFFER(100);沒記錄過的新站用 first_default
- 若這次實際爬到 > 登記值 → 更新「頁數」欄(網站長大,上限跟著長)
- 搭配 audit_links 的分頁跳過,陷阱頁不計入,登記值不會被灌大

寫回一律以「網址」對鍵、逐列對準(遵守 Sheet 鐵律,不按列序)。
"""
import datetime

SHEET_TAB = "TCGweb466站清單"
BUFFER = 100


def get_cap(reg, url, first_default):
    """回傳該站這次的頁數上限。已知站=頁數+BUFFER;新站=first_default。"""
    p = reg.get(url, 0)
    return int(p) + BUFFER if p and int(p) > 0 else first_default


def _ws():
    import config, gspread
    gc = gspread.service_account(filename=config.GA_KEY_FILE)
    return gc.open_by_key(config.MASTER_SHEET_ID).worksheet(SHEET_TAB)


def refresh_csv(path):
    """Sheet「TCGweb466站清單」整張導出 → 原子性覆寫本機快照 CSV。
    讓 Sheet 維持唯一事實來源:改站清單/網址/抓取方式只改 Sheet,掃描前自動下載。
    內容異常(空表/缺網址欄)時丟例外、不覆寫,由呼叫端決定沿用舊快照。回傳資料列數。"""
    import csv, os
    vals = _ws().get_all_values()
    if not vals or "網址" not in vals[0]:
        raise ValueError("分頁內容異常(空表或無「網址」欄),不覆寫快照")
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(vals)
    os.replace(tmp, path)
    return len(vals) - 1


def read_sheet():
    """讀 Sheet「頁數」欄 → {url: pages_int}。沒有該欄則回空。"""
    ws = _ws()
    vals = ws.get_all_values()
    hdr = vals[0]
    reg = {}
    if "頁數" in hdr:
        iu, ip = hdr.index("網址"), hdr.index("頁數")
        for r in vals[1:]:
            if len(r) > ip and str(r[ip]).strip().isdigit() and len(r) > iu:
                reg[r[iu].strip()] = int(r[ip])
    return reg


def write_sheet(url_pages):
    """把新頁數寫回 Sheet「頁數」欄(+「頁數更新日」),URL 對鍵、只在變大時更新。
    回傳更新筆數。"""
    import gspread
    ws = _ws()
    vals = ws.get_all_values()
    hdr = vals[0]
    for col in ["頁數", "頁數更新日"]:
        if col not in hdr:
            if ws.col_count < len(hdr) + 1:
                ws.add_cols(1)
            ws.update_cell(1, len(hdr) + 1, col)
            vals = ws.get_all_values(); hdr = vals[0]
    iu, ip, idt = hdr.index("網址"), hdr.index("頁數"), hdr.index("頁數更新日")
    today = datetime.date.today().isoformat()
    colp, cold = [], []
    changed = 0
    for r in vals[1:]:
        u = r[iu].strip() if len(r) > iu else ""
        cur = int(r[ip]) if len(r) > ip and str(r[ip]).strip().isdigit() else 0
        new = url_pages.get(u)
        if new and new > cur:
            colp.append([new]); cold.append([today]); changed += 1
        else:
            colp.append([r[ip] if len(r) > ip else ""])
            cold.append([r[idt] if len(r) > idt else ""])
    clp = gspread.utils.rowcol_to_a1(1, ip + 1).rstrip("1")
    cld = gspread.utils.rowcol_to_a1(1, idt + 1).rstrip("1")
    ws.update(values=colp, range_name=f"{clp}2:{clp}{len(vals)}")
    ws.update(values=cold, range_name=f"{cld}2:{cld}{len(vals)}")
    return changed
