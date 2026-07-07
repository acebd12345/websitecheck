# -*- coding: utf-8 -*-
"""整併日常執行殼(地端排程入口)。

把統一引擎的產出串成一次執行:
  1. 健康剖面(全 466 站,首頁層,靜態優先,快)     → 最新消息時效/死站/需渲染
  2. 合規剖面(14 站,送地端 AI 判讀)               → 首頁最新消息區塊判讀
  3. 深層 BFS(選定站,全站爬取 + 外連稽核)         → 全站連結健康(可選,較久)
產出全部落在 private/reports/<時間>/,並印綜合摘要。

清單單一來源:Google Sheet「TCGweb466站清單」(--sheet)或本機 CSV。

用法:
  python -m engine.run_all --sheet                      健康+合規(預設,不深爬)
  python -m engine.run_all --sheet --deep 14站          深爬 14 站
  python -m engine.run_all --sheet --deep-stale         深爬「健康剖面判停更」的站
  python -m engine.run_all --csv <path> --no-compliance 只跑健康(離線,不呼叫AI)
"""
import argparse
import concurrent.futures
import datetime
import json
import os
import sys
from collections import Counter

sys.path.insert(0, r"D:\websitecheck")
sys.path.insert(0, r"D:\websitecheck\monthly")

from engine.scan import load_sites, health_profile, compliance_profile, CSV_DEFAULT
from engine.fetch_layered import fetch_layered
from engine.crawl import crawl_site
import config

REPORTS = config.REPORTS_DIR


def _fetch(site, allow_render):
    return site, fetch_layered(site["url"], allow_render=allow_render,
                               force_method=("manual" if site["method"] == "manual" else None))


def run_health(sites, workers=20, allow_render=False):
    hv = Counter(); recs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for s, fr in ex.map(lambda x: _fetch(x, allow_render), sites):
            h = health_profile(s, fr); hv[h["verdict"]] += 1; recs.append(h)
    return recs, dict(hv)


def run_compliance(sites14):
    recs = []
    for s in sites14:
        _, fr = _fetch(s, allow_render=False)
        c = compliance_profile(s, fr)
        recs.append({"url": s["url"], "name": s["name"], **c})
    ok = sum(1 for r in recs if r.get("ai_ok"))
    return recs, {"judged": ok, "failed": len(recs) - ok}


def run_deep(sites_subset, depth=1, check_external=True):
    out = []
    for s in sites_subset:
        try:
            r = crawl_site(s["url"], name=s["name"], max_depth=depth, check_external=check_external)
            out.append({"url": s["url"], "name": s["name"], **r["stats"]})
        except Exception as e:
            out.append({"url": s["url"], "name": s["name"], "error": f"{type(e).__name__}"})
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", action="store_true")
    ap.add_argument("--csv", default=CSV_DEFAULT)
    ap.add_argument("--no-compliance", action="store_true", help="不跑合規AI(離線)")
    ap.add_argument("--deep", type=int, default=0, help="深爬前 N 站(0=不深爬)")
    ap.add_argument("--deep-stale", action="store_true", help="深爬健康剖面判停更的站")
    ap.add_argument("--deep-depth", type=int, default=1)
    ap.add_argument("--workers", type=int, default=20)
    args = ap.parse_args()

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    outdir = os.path.join(REPORTS, f"engine_run_{stamp}")
    os.makedirs(outdir, exist_ok=True)
    sites = load_sites(args.sheet, args.csv)
    sites14 = [s for s in sites if s["is14"]]
    print(f"=== 整併引擎執行 {stamp} ===")
    print(f"清單來源: {'Google Sheet' if args.sheet else 'CSV'} | 全站 {len(sites)} | 合規 14 站集 {len(sites14)} URL")
    print(f"產出目錄: {outdir}\n")

    summary = {"stamp": stamp, "total_sites": len(sites)}

    # 1. 健康剖面
    print("[1/3] 健康剖面(全站首頁層,靜態優先)...")
    import time; t = time.time()
    health, hv = run_health(sites, workers=args.workers)
    json.dump(health, open(os.path.join(outdir, "health.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"      完成 {len(health)} 站 / {time.time()-t:.0f}s | {hv}\n")
    summary["health"] = hv

    # 2. 合規剖面
    if not args.no_compliance:
        print("[2/3] 合規剖面(14 站 → 地端 AI)...")
        t = time.time()
        comp, cstat = run_compliance(sites14)
        json.dump(comp, open(os.path.join(outdir, "compliance.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"      完成 {len(comp)} 站 / {time.time()-t:.0f}s | 判讀成功 {cstat['judged']} 失敗 {cstat['failed']}\n")
        summary["compliance"] = cstat
    else:
        print("[2/3] 合規剖面: 略過(--no-compliance)\n")

    # 3. 深層 BFS(可選)
    subset = []
    if args.deep_stale:
        stale_urls = {h["url"] for h in health if h["verdict"] == "停更"}
        subset = [s for s in sites if s["url"] in stale_urls]
        print(f"[3/3] 深層 BFS: 停更站 {len(subset)} 站,depth={args.deep_depth}...")
    elif args.deep:
        subset = sites[:args.deep]
        print(f"[3/3] 深層 BFS: 前 {len(subset)} 站,depth={args.deep_depth}...")
    if subset:
        t = time.time()
        deep = run_deep(subset, depth=args.deep_depth)
        json.dump(deep, open(os.path.join(outdir, "deep_crawl.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        tot_dead = sum(d.get("dead_external", 0) for d in deep)
        print(f"      完成 {len(deep)} 站 / {time.time()-t:.0f}s | 累計死連 {tot_dead}\n")
        summary["deep"] = {"sites": len(deep), "dead_external_total": tot_dead}
    else:
        print("[3/3] 深層 BFS: 略過(未指定 --deep / --deep-stale)\n")

    json.dump(summary, open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("=== 綜合摘要 ===")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"\n全部產出: {outdir}")


if __name__ == "__main__":
    main()
