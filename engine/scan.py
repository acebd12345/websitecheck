# -*- coding: utf-8 -*-
"""統一引擎驅動(整併 stage 4:一次爬取 → 雙剖面)。

一次靜態優先抓取,上層產兩種剖面:
  health     全站健康掃描:最新消息日期、時效(停更)、抓取方式
  compliance 合規檢核:14 站送地端 AI 判讀(首頁是否有最新消息區塊等)

清單來源:Google Sheet 站清單母表「府內網站表」(單一事實來源);
         無法連 Sheet 時用 --csv 讀本機對照清單。

用法:
  python -m engine.scan --profile health --limit 10 --csv <path>
  python -m engine.scan --profile compliance          (14 站,需地端 AI)
  python -m engine.scan --profile both --sheet         (全量,讀 Sheet)
"""
import argparse
import csv as csvmod
import datetime
import re
import sys

import os
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "monthly"))

from engine.fetch_layered import fetch_layered
from engine import dates as dates_mod
import webcheck_ai
from bs4 import BeautifulSoup

TODAY = datetime.date.today()
ONEYR = TODAY - datetime.timedelta(days=365)
CSV_DEFAULT = os.path.join(_ROOT, "private", "TCGweb_466站對照清單_v2.csv")
GENERIC_DATE = re.compile(r"(20\d{2})[/.\-年](\d{1,2})[/.\-月](\d{1,2})|(1[01]\d)[/.\-年](\d{1,2})[/.\-月](\d{1,2})")

AI_QUESTION = "這個網頁首頁是否有最新消息/公告區塊?若有,請列出最近三筆的標題與日期;若無或看不出來,請說明。"


def load_sites(use_sheet, csv_path, only_14=False):
    """回傳 [{url, name, is14, method}]。優先 Sheet,退回 CSV。"""
    rows = []
    if use_sheet:
        import config, gspread
        gc = gspread.service_account(filename=config.GA_KEY_FILE)
        ws = gc.open_by_key(config.MASTER_SHEET_ID).worksheet(config.SITE_LIST_WS)
        data = ws.get_all_records()
        for r in data:
            rows.append({"url": str(r.get("網址", "")).strip(), "name": str(r.get("網站名稱", "")).strip(),
                         "is14": str(r.get("合規檢核", "")).strip() == "是",
                         "method": str(r.get("內容抓取方式", "")).strip()})
    else:
        for r in csvmod.DictReader(open(csv_path, encoding="utf-8-sig")):
            rows.append({"url": (r.get("網址") or "").strip(), "name": (r.get("網站名稱") or "").strip(),
                         "is14": (r.get("合規檢核") or "").strip() == "是",
                         "method": (r.get("內容抓取方式") or "").strip()})
    rows = [r for r in rows if r["url"]]
    if only_14:
        rows = [r for r in rows if r["is14"]]
    return rows


def latest_date_from(fetch_res):
    """健康剖面:從抓取結果取最新更新日期。有 html 用 TCGweb 日期抽取器,否則 regex on text。"""
    def _ok(iso):
        """過濾未來日期(§8 已知:頁面數字常被誤當日期,如 2083)"""
        try:
            return datetime.date.fromisoformat(iso) <= TODAY + datetime.timedelta(days=3)
        except Exception:
            return False
    html = fetch_res.get("html")
    if html:
        try:
            soup = BeautifulSoup(html, "html.parser")
            d = dates_mod.extract_last_updated(soup, log_func=lambda m: None)
            if d and d != "[無日期]" and _ok(d):
                return d
        except Exception:
            pass
    # 渲染路徑(只有 text)或抽取器無果 → regex 後援,取 <= 今天的最大值
    cands = []
    for m in GENERIC_DATE.finditer(fetch_res.get("text", "")):
        if m.group(1):
            y, mo, da = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            y, mo, da = int(m.group(4)) + 1911, int(m.group(5)), int(m.group(6))
        try:
            dd = datetime.date(y, mo, da)
            if dd <= TODAY + datetime.timedelta(days=3):
                cands.append(dd)
        except ValueError:
            pass
    return max(cands).isoformat() if cands else ""


def health_profile(site, fr):
    latest = latest_date_from(fr)
    verdict = "有更新"
    if fr["method"] == "manual":
        verdict = "人工"
    elif fr["method"] == "error":
        verdict = "連線失敗"
    elif fr["need_render"] and fr["method"] != "playwright":
        verdict = "需渲染"
    elif not latest:
        verdict = "無日期"
    else:
        d = datetime.date.fromisoformat(latest)
        verdict = "停更" if d < ONEYR else "有更新"
    return {"url": site["url"], "name": site["name"], "method": fr["method"],
            "escalated": fr["escalated"], "latest": latest, "verdict": verdict,
            "chars": fr["chars"], "reason": fr["reason"]}


def compliance_profile(site, fr):
    """14 站合規剖面:送地端 AI 判讀。需 config 的 AI 端點可用。"""
    if not fr["text"]:
        return {"url": site["url"], "name": site["name"], "ai_answer": f"[未判讀] {fr['reason']}", "ai_ok": False}
    try:
        ans = webcheck_ai.ask_ai(AI_QUESTION, fr["text"], site["url"])
        return {"url": site["url"], "name": site["name"], "ai_answer": ans, "ai_ok": True}
    except Exception as e:
        return {"url": site["url"], "name": site["name"],
                "ai_answer": f"[AI呼叫失敗:{type(e).__name__}] 地端端點未啟動?", "ai_ok": False}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=["health", "compliance", "both"], default="health")
    ap.add_argument("--sheet", action="store_true", help="從 Google Sheet 讀清單(否則讀 --csv)")
    ap.add_argument("--csv", default=CSV_DEFAULT)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 站(0=全部)")
    ap.add_argument("--render", action="store_true", help="允許升級 Playwright(預設只標記不渲染)")
    ap.add_argument("--workers", type=int, default=16, help="併發抓取數(health 用;compliance 強制序列)")
    ap.add_argument("--out", default="", help="結果寫出 JSON 路徑")
    args = ap.parse_args()

    only_14 = (args.profile == "compliance")
    sites = load_sites(args.sheet, args.csv, only_14=only_14)
    if args.limit:
        sites = sites[:args.limit]
    print(f"載入 {len(sites)} 站 | 剖面={args.profile} | 來源={'Sheet' if args.sheet else 'CSV'} | 渲染={'開' if args.render else '標記'}\n", flush=True)

    from collections import Counter
    import concurrent.futures, json, time
    hv = Counter(); mc = Counter(); results = []
    t0 = time.time()

    def do_fetch(s):
        return s, fetch_layered(s["url"], allow_render=args.render,
                                force_method=("manual" if s["method"] == "manual" else None))

    # compliance 走序列(AI 呼叫不併發轟炸);health 併發抓取
    workers = 1 if args.profile == "compliance" else args.workers
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for s, fr in ex.map(do_fetch, sites):
            mc[fr["method"]] += 1
            rec = {"url": s["url"], "name": s["name"], "method": fr["method"]}
            if args.profile in ("health", "both"):
                h = health_profile(s, fr); hv[h["verdict"]] += 1; rec.update(h)
            if args.profile in ("compliance", "both"):
                rec["compliance"] = compliance_profile(s, fr)
            results.append(rec)
            done += 1
            if done % 50 == 0:
                print(f"  ...{done}/{len(sites)} ({time.time()-t0:.0f}s)", flush=True)

    print(f"\n完成 {len(results)} 站,耗時 {time.time()-t0:.0f}s")
    print(f"抓取方式分布: {dict(mc)}")
    if args.profile in ("health", "both"):
        print(f"健康剖面分布: {dict(hv)}")
        stale = sorted([r for r in results if r.get("verdict") == "停更"], key=lambda r: r.get("latest") or "9999")
        print(f"\n停更(逾一年){len(stale)} 站(前20):")
        for r in stale[:20]:
            print(f"  {r.get('latest') or '?':11} {r['name'][:28]}")
    if args.out:
        json.dump(results, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\n結果寫出: {args.out}")


if __name__ == "__main__":
    main()
