# -*- coding: utf-8 -*-
"""全 466 站連結稽核(渲染版統一引擎,站層級多行程平行)。

讀 TCGweb466站清單 → 多行程平行,每站一個 worker(獨立行程 → ALLOW_RENDER/渲染額度
各自獨立,不互相干擾)→ audit_links.audit_site(靜態優先,只有 playwright 站才渲染)
→ 挑死連/搶註/404/重導,邊完成邊寫合併 CSV + 進度 JSON。

用法:
  python -m engine.link_audit_all --max-pages 30 --workers 6
  python -m engine.link_audit_all --max-pages 300 --workers 4 --limit 50
"""
import argparse, csv, datetime, json, os, sys, time
import concurrent.futures as cf

sys.path.insert(0, r"D:\websitecheck")
sys.path.insert(0, r"D:\websitecheck\daily")
import config
from audit_links import norm_host, CSV_COLS

CSV_LIST = r"D:\websitecheck\private\TCGweb_466站對照清單_v2.csv"
OUT_DIR = config.PRIVATE_DIR
SKIP_METHODS = {"manual", "疑似失效"}
SKIP_HOSTS = {"3d.taipei"}


def audit_one(task):
    """單站 worker(獨立行程執行)。
    task=(name,url,org,method,max_pages,outdir,whitelist,skip_hosts)"""
    name, url, org, method, max_pages, outdir, whitelist, skip_hosts = task
    sys.path.insert(0, r"D:\websitecheck"); sys.path.insert(0, r"D:\websitecheck\daily")
    import audit_links
    audit_links.ALLOW_RENDER = (method == "playwright")   # 每行程各自設定,不互相干擾
    host = norm_host(url)
    tag = host.replace(".", "_")
    try:
        results = audit_links.audit_site(
            url, max_pages, links_log_path=os.path.join(outdir, f"links_{tag}.jsonl"),
            content_whitelist=whitelist, skip_hosts=skip_hosts)
        probs = [r for r in results if r["risk"] != "OK"]
        return {"name": name, "url": url, "org": org, "status": "ok",
                "links": len(results), "problems": probs,
                "n_problems": len(probs),
                "suspicious": sum(1 for p in probs if p["risk"] == "SUSPICIOUS"),
                "dead": sum(1 for p in probs if p["risk"] == "DEAD")}
    except Exception as e:
        return {"name": name, "url": url, "org": org, "status": f"fail:{type(e).__name__}",
                "links": 0, "problems": [], "n_problems": 0, "suspicious": 0, "dead": 0}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=30)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    outdir = os.path.join(OUT_DIR, "reports", f"linkaudit_all_{stamp}")
    os.makedirs(outdir, exist_ok=True)
    combined = os.path.join(outdir, "all_problems.csv")
    progress = os.path.join(outdir, "progress.json")

    # 從 config 讀白名單(與 batch_audit 同一來源):
    #   content_whitelist 已確認內容無虞的網域(跳過關鍵字檢查,仍檢連線)
    #   skip_hosts 有防爬蟲、人工確認正常的網域(完全略過)
    scan = config._cfg.get("scan", {})
    whitelist = tuple(w.strip().lower() for w in str(scan.get("content_whitelist","")).split(",") if w.strip())
    skip_hosts = tuple(w.strip().lower() for w in str(scan.get("skip_hosts","")).split(",") if w.strip())
    print(f"白名單:content_whitelist {len(whitelist)} 個、skip_hosts {len(skip_hosts)} 個", flush=True)

    rows = list(csv.DictReader(open(CSV_LIST, encoding="utf-8-sig")))
    all_sites = [(r.get("網站名稱","").strip(), r.get("網址","").strip(),
                  r.get("局處","").strip(), r.get("內容抓取方式","").strip())
                 for r in rows if r.get("網址","").strip()]
    if args.limit:
        all_sites = all_sites[:args.limit]

    tasks, skipped = [], []
    for name, url, org, method in all_sites:
        if method in SKIP_METHODS or norm_host(url) in SKIP_HOSTS:
            skipped.append({"name": name, "url": url, "org": org, "status": method or "skip",
                            "n_problems": 0})
        else:
            tasks.append((name, url, org, method, args.max_pages, outdir, whitelist, skip_hosts))

    n_pw = sum(1 for t in tasks if t[3] == "playwright")
    print(f"全站連結稽核(平行):待掃 {len(tasks)} 站(其中 playwright {n_pw} 會渲染)、"
          f"跳過 {len(skipped)} 站 | {args.workers} 行程 | {args.max_pages} 頁/站\n"
          f"產出 {outdir}\n", flush=True)

    cfp = open(combined, "w", newline="", encoding="utf-8-sig")
    cw = csv.DictWriter(cfp, fieldnames=["site_name","org"]+CSV_COLS, extrasaction="ignore")
    cw.writeheader(); cfp.flush()

    prog = list(skipped)
    done = 0
    t0 = time.time()
    with cf.ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(audit_one, t): t for t in tasks}
        for fut in cf.as_completed(futs):
            r = fut.result()
            for p in r["problems"]:
                cw.writerow({"site_name": r["name"], "org": r["org"], **p})
            cfp.flush()
            prog.append({k: v for k, v in r.items() if k != "problems"})
            json.dump(prog, open(progress, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            done += 1
            print(f"[{done}/{len(tasks)}] {r['name'][:18]:20} 連結{r['links']} 異常{r['n_problems']}"
                  f"(搶註{r['suspicious']} 死連{r['dead']}) [{r['status']}] {time.time()-t0:.0f}s", flush=True)
    cfp.close()

    ok = [p for p in prog if p.get("status") == "ok"]
    print(f"\n=== 完成 {time.time()-t0:.0f}s ===")
    print(f"掃描 {len(ok)} 站,異常連結 {sum(p['n_problems'] for p in ok)}"
          f"(搶註 {sum(p.get('suspicious',0) for p in ok)}、"
          f"死連 {sum(p.get('dead',0) for p in ok)})")
    print(f"合併明細: {combined}")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
