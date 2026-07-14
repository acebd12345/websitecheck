# -*- coding: utf-8 -*-
"""月度掃描排程：讀取 Google Sheet「月度掃描排程」分頁 + 自動同步母表。

排程邏輯：
  - 巨站（頁數 ≥ 5000）各佔一個獨立 Day（1~30）
  - 一般站用貪婪裝箱平均分到剩餘天，讓每天「總頁數」盡量接近
  - 站名直接取自母表 → 永遠同步；只納入母表現有站 → 下線站自動移除

用法:
  python -m engine.schedule --rebuild        # 重算排程寫回分頁
  python -m engine.schedule --today          # 印今天該掃哪些站(dry, 不掃)
"""
import argparse
import datetime
import os
import sys
from urllib.parse import urlparse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import config

SCHEDULE_WS = "月度掃描排程"
GIANT_THRESHOLD = 5000
DEFAULT_PAGES = 500
SKIP_METHODS = {"manual", "疑似失效"}
SKIP_HOSTS = {"3d.taipei"}


def _open_wb():
    import gspread
    gc = gspread.service_account(filename=config.GA_KEY_FILE)
    return gc.open_by_key(config.MASTER_SHEET_ID)


def _load_scannable_sites(wb):
    """從府內網站表讀可掃站(排除 manual/疑似失效/3d.taipei)。"""
    ws = wb.worksheet(config.SITE_LIST_WS)
    data = ws.get_all_records()
    sites = []
    for r in data:
        url = str(r.get("網址", "")).strip()
        if not url:
            continue
        method = str(r.get("內容抓取方式", "")).strip()
        if method in SKIP_METHODS:
            continue
        host = urlparse(url).hostname or ""
        if host in SKIP_HOSTS:
            continue
        name = str(r.get("網站名稱", "")).strip()
        pages_str = str(r.get("頁數", "")).strip()
        pages = int(pages_str) if pages_str.isdigit() else 0
        sites.append({"name": name, "url": url, "pages": pages})
    return sites


def rebuild():
    """從府內網站表重算 30 天排程，寫回「月度掃描排程」分頁。"""
    wb = _open_wb()
    sites = _load_scannable_sites(wb)

    giants = sorted([s for s in sites if s["pages"] >= GIANT_THRESHOLD],
                    key=lambda s: s["pages"], reverse=True)
    normals = [s for s in sites if s["pages"] < GIANT_THRESHOLD]

    n_giant = min(len(giants), 29)   # 至少留 1 天給一般批
    giants = giants[:n_giant]
    n_normal_days = 30 - n_giant

    # 貪婪裝箱：一般站按頁數降序，每次分配到目前最輕的 bin
    normals.sort(key=lambda s: s["pages"] or DEFAULT_PAGES, reverse=True)
    bins = [[] for _ in range(n_normal_days)]
    weights = [0] * n_normal_days
    for s in normals:
        w = s["pages"] if s["pages"] > 0 else DEFAULT_PAGES
        i = weights.index(min(weights))
        bins[i].append(s)
        weights[i] += w

    # 組 30 列：Day|型態|站數|總頁數|估時(分)|備註|站名清單
    rows = []
    day = 1
    for s in giants:
        p = s["pages"] or DEFAULT_PAGES
        rows.append([day, "巨站", 1, p, max(1, p // 100),
                     s["name"][:20], s["name"]])
        day += 1
    for b, w in zip(bins, weights):
        names = "、".join(s["name"] for s in b)
        rows.append([day, "一般批", len(b), int(w), max(1, int(w) // 100),
                     "", names])
        day += 1

    # 寫回分頁：清舊資料列內容，保留表頭
    ws = wb.worksheet(SCHEDULE_WS)
    old = ws.get_all_values()
    if len(old) > 1:
        ws.batch_clear([f"A2:G{max(len(old), 32)}"])
    ws.update(values=rows, range_name=f"A2:G{1 + len(rows)}")

    print(f"排程已重建：巨站 {n_giant} 個獨立日 + 一般批 {n_normal_days} 天")
    print(f"總站數 {len(sites)}（排除 manual/疑似失效/3d.taipei）")
    if weights:
        print(f"一般批每日頁數：{min(weights):.0f}~{max(weights):.0f}（目標均衡）")
    return {"giants": n_giant, "normal_days": n_normal_days,
            "total_sites": len(sites)}


def today_batch(day_override=None):
    """回傳今天該掃的站。Returns [(name, url, org, method), ...]。"""
    wb = _open_wb()
    day_n = day_override if day_override is not None else ((datetime.date.today().day - 1) % 30) + 1

    # 讀排程分頁 Day==N 那列的站名清單
    ws = wb.worksheet(SCHEDULE_WS)
    vals = ws.get_all_values()
    if len(vals) < 2:
        print("排程分頁為空，請先跑 --rebuild")
        return []

    hdr = vals[0]
    di = hdr.index("Day") if "Day" in hdr else 0
    ni = hdr.index("站名清單") if "站名清單" in hdr else len(hdr) - 1

    target_names = []
    for row in vals[1:]:
        if len(row) > di and str(row[di]).strip() == str(day_n):
            if len(row) > ni and row[ni].strip():
                target_names = [n.strip() for n in row[ni].split("、") if n.strip()]
            break

    if not target_names:
        print(f"Day {day_n} 沒有站")
        return []

    # 用母表對照名稱 → URL/org/method
    mws = wb.worksheet(config.SITE_LIST_WS)
    mdata = mws.get_all_records()
    name_map = {}
    for r in mdata:
        n = str(r.get("網站名稱", "")).strip()
        if n:
            name_map[n] = {
                "url": str(r.get("網址", "")).strip(),
                "org": str(r.get("局處", "")).strip(),
                "method": str(r.get("內容抓取方式", "")).strip(),
            }

    batch = []
    for name in target_names:
        info = name_map.get(name)
        if not info or not info["url"]:
            print(f"[警告] 排程站名「{name}」在母表找不到，跳過")
            continue
        batch.append((name, info["url"], info["org"], info["method"]))
    return batch


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="月度掃描排程管理")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--rebuild", action="store_true", help="重算排程寫回分頁")
    grp.add_argument("--today", action="store_true", help="印今天該掃哪些站")
    args = ap.parse_args()

    if args.rebuild:
        rebuild()
    elif args.today:
        day_n = ((datetime.date.today().day - 1) % 30) + 1
        print(f"今天 {datetime.date.today()} → Day {day_n}")
        batch = today_batch()
        print(f"共 {len(batch)} 站:")
        for name, url, org, method in batch:
            print(f"  {name[:30]:32} {url[:50]}")


if __name__ == "__main__":
    main()
