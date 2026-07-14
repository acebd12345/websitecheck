# -*- coding: utf-8 -*-
"""全網站深度稽核(整夜無人值守)。一次跑完 4 階段:

  階段1 全 466 站連結稽核:每站最多 8000 頁,站層級多行程平行,
        方法驅動渲染(只 playwright 站渲染),套 config 白名單/skip_hosts。
  階段2 AI 複查:對機械判 SUSPICIOUS 的連結,實連抓內容送地端 AI 判 A賭博色情/B停放/C誤報。
  階段3 型態確認:對 AI 判 A/B 的,抓原始 HTML 判斷入侵型態
        (隱藏掛馬 hidden / 可見內容注入 visible / 網域易主 takeover / 停放失效 dead)。
  階段4 產最終報告:confirmed_hijacks.csv + summary.json + 可讀 summary。

全程 try/except、逐站/逐項 flush 寫檔,可中途查 progress。
用法: python -m engine.full_overnight --max-pages 8000 --workers 6
      python -m engine.full_overnight --max-pages 20 --workers 3 --limit 4   (煙霧測試)
"""
import argparse, csv, datetime, json, os, re, socket, ssl, sys, time, urllib.parse, urllib.request
import concurrent.futures as cf

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); sys.path.insert(0, os.path.join(_ROOT, "daily")); sys.path.insert(0, os.path.join(_ROOT, "monthly"))
import config
import scan_settings
from audit_links import norm_host, CSV_COLS
from engine import page_budget

CSV_LIST = os.path.join(config.PRIVATE_DIR, "TCGweb_466站對照清單_v2.csv")
OUT_DIR = config.PRIVATE_DIR
SKIP_METHODS = {"manual", "疑似失效"}
SKIP_HOSTS = {"3d.taipei"}
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
# 複查抓取用完整瀏覽器標頭:cloaking 站對陽春 UA 回不同內容、部分站缺 Accept 回 500
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
       "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
       "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"}

# 定性用詞庫(裸搜計數,只跑在 AI 已判 A/B 的頁上):單一來源見 scan_settings。
# 藥物詞(viagra/cialis)只適合整字比對,裸搜會撞 specialist 類字 → 排除;
# 品牌片段(dewa/judi/gacor)反之只適合裸搜 → 由 characterize_extra 補入。
_AMBIG_SUBSTR = {"viagra", "cialis", "виагра"}

def _build_bad_kw():
    return ([k for k in scan_settings.get("suspicious_keywords") if k not in _AMBIG_SUBSTR]
            + scan_settings.get("characterize_extra_keywords"))

BAD_KW = _build_bad_kw()


# ── 階段1 worker(獨立行程)──
CEIL = 9000    # 加碼硬上限:爬到 9000 頁還沒爬完的超大站就停,不再往上加

def audit_one(task):
    """逐步加碼爬完:先 1000,撞牆→+2000(=3000),再撞牆→每次 +3000,直到爬完或達上限。
    同時跑基本合規檢查(HTTPS/RWD/搜尋/無障礙);合規站加 deep_check。"""
    name, url, org, method, first_cap, outdir, whitelist, skip_hosts, is_compliance, no_escalate = task
    sys.path.insert(0, _ROOT); sys.path.insert(0, os.path.join(_ROOT, "daily")); sys.path.insert(0, os.path.join(_ROOT, "monthly"))
    import audit_links
    audit_links.ALLOW_RENDER = (method == "playwright")
    tag = norm_host(url).replace(".", "_")
    try:
        cap, step, rounds = first_cap, 2000, 0
        while True:
            results = audit_links.audit_site(url, cap,
                links_log_path=os.path.join(outdir, f"links_{tag}.jsonl"),
                content_whitelist=whitelist, skip_hosts=skip_hosts)
            lc = getattr(audit_links, "LAST_CRAWL", {"pages": 0, "capped": False})
            if not lc.get("capped") or cap >= CEIL or no_escalate:
                break
            cap = min(cap + step, CEIL); step = 3000; rounds += 1   # 1000→3000→6000→9000 封頂
        # 0 頁保護:可能是掃描當下暫時抓空,或靜態空殼(SPA)。開渲染重試最多 2 次。
        retried = 0
        while lc.get("pages", 0) == 0 and retried < 2:
            retried += 1
            time.sleep(3)
            audit_links.ALLOW_RENDER = True
            results = audit_links.audit_site(url, first_cap,
                links_log_path=os.path.join(outdir, f"links_{tag}.jsonl"),
                content_whitelist=whitelist, skip_hosts=skip_hosts)
            lc = getattr(audit_links, "LAST_CRAWL", {"pages": 0, "capped": False})
        probs = [r for r in results if r["risk"] != "OK"]
        # ── 合規檢查(每站基本 + 合規站 deep_check) ──
        comp = {}
        try:
            from engine.compliance import run_basic, run_deep
            comp = run_basic(url)
            if is_compliance and comp.get("alive"):
                comp["deep"] = run_deep(url)
        except Exception as e:
            comp["compliance_error"] = f"{type(e).__name__}: {e}"
        return {"name": name, "url": url, "org": org, "status": "ok",
                "links": len(results), "problems": probs, "n_problems": len(probs),
                "pages": lc.get("pages", 0), "capped": lc.get("capped", False),
                "rounds": rounds, "final_cap": cap, "retried": retried,
                "suspicious": sum(1 for p in probs if p["risk"]=="SUSPICIOUS"),
                "dead": sum(1 for p in probs if p["risk"]=="DEAD"),
                "compliance": comp, "is_compliance": is_compliance}
    except Exception as e:
        return {"name": name, "url": url, "org": org, "status": f"fail:{type(e).__name__}",
                "links": 0, "problems": [], "n_problems": 0, "pages": 0, "capped": False,
                "rounds": 0, "suspicious": 0, "dead": 0, "compliance": {}, "is_compliance": is_compliance}


# ── 階段2/3 輔助 ──
def fetch_raw(url, limit=200000):
    host = urllib.parse.urlparse(url).hostname
    try:
        socket.getaddrinfo(host, None)
    except Exception:
        return None, "DNS失敗"
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=20, context=CTX)
        return r.read(limit).decode("utf-8", "replace"), None
    except Exception as e:
        return None, f"連不上({type(e).__name__})"


def visible_text(html):
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S|re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)).strip()


def characterize(url, html):
    """判斷入侵型態 + 取證據。"""
    title = ""
    m = re.search(r"<title[^>]*>([^<]*)", html, re.I)
    if m: title = m.group(1).strip()
    vis = visible_text(html)
    # 隱藏區塊
    hidden = re.findall(r'<div[^>]*style="[^"]*display\s*:\s*none[^"]*"[^>]*>(.*?)</div>', html, re.S|re.I)
    hidden_bad = []
    for h in hidden:
        for kw in BAD_KW:
            if re.search(re.escape(kw), h, re.I):
                doms = sorted(set(re.findall(r'href="https?://([^/"]+)', h)))
                hidden_bad += doms
                break
    vis_hits = [kw for kw in BAD_KW if len(re.findall(re.escape(kw), vis, re.I)) >= 2]
    title_bad = any(re.search(re.escape(kw), title, re.I) for kw in BAD_KW + ["gaming"])
    if hidden_bad:
        return "隱藏掛馬", f"原始碼 display:none 藏 {len(set(hidden_bad))} 個外部網域(如 {', '.join(sorted(set(hidden_bad))[:5])}),畫面看不到"
    if title_bad and vis_hits:
        return "網域易主(整站賭博/色情)", f"標題已變「{title[:40]}」,可見內容含 {', '.join(vis_hits[:6])}"
    if vis_hits:
        return "可見內容注入", f"標題仍為「{title[:30]}」但可見內容出現 {', '.join(vis_hits[:6])}(CMS 疑遭灌入)"
    return "待人工", f"標題「{title[:40]}」,關鍵字未達可見門檻"


def ai_verify(url):
    import webcheck_ai
    # 抓取失敗重試 3 次:一次網路抖動不能把真掛馬誤降成 B(dac.tw/taitraesource 實例);
    # 三次仍失敗回「?」待人工,不猜停放——正當媒體擋爬(rti/貿協)也走這裡,別誤標
    html = err = None
    for attempt in range(3):
        html, err = fetch_raw(url, 120000)
        if not err:
            break
        time.sleep(4)
    if err:
        return "?", f"實連失敗×3({err}),待人工確認", (html or "")
    text = webcheck_ai.html_to_text(html)[:4000]
    q = ("以下是政府網站連到的外部連結內容,判斷屬於:A=線上賭博/色情/博弈站(含被搶註導向)"
         "B=網域停放/出售頁 C=正當內容(政府/新聞/藝文/防治宣導,關鍵字只是內文提到)。"
         "防治/反毒/衛教/藝評都算C。只回代號+一句理由。")
    try:
        ans = webcheck_ai.ask_ai(q, text, url).strip()
        v = re.search(r"[ABC]", ans)
        return (v.group(0) if v else "?"), ans.replace("\n"," ")[:90], html
    except Exception as e:
        return "?", f"AI失敗({type(e).__name__})", html


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=1000, help="首掃頁數;撞牆自動加碼")
    ap.add_argument("--force-cap", type=int, default=0, help="強制所有站首掃上限=N頁(停用加碼+頁數回寫);環境驗證/淺掃用")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--org", default="", help="只掃指定局處(如 資訊局),測試/分局處跑用")
    ap.add_argument("--only", default="", help="只掃名稱/網址含指定字串的站(逗號分隔),定點重測用")
    ap.add_argument("--resume", default="", help="續跑:指定既有 full_overnight_* 目錄,跳過已完成站")
    ap.add_argument("--verify", default="", help="複查:不重爬,對既有報告目錄的 all_problems.csv 重跑階段2-4(取代舊 verify_suspicious)")
    ap.add_argument("--no-report", action="store_true", help="跳過收尾的 HTML 報告產生")
    ap.add_argument("--mail", action="store_true", help="階段4後按局處寄信(預設關)")
    ap.add_argument("--mail-to", default=None, help="收件人 override(測試用;不給則走各局處Email真值)")
    ap.add_argument("--schedule-today", action="store_true", help="只掃月度掃描排程中今天的那批站")
    ap.add_argument("--dry-run", action="store_true", help="寄信時不實寄,只印彙整結果")
    args = ap.parse_args()
    if args.verify and args.resume:
        sys.exit("--verify 與 --resume 不可同時使用")
    if args.schedule_today and (args.org or args.only):
        sys.exit("--schedule-today 與 --org/--only 互斥")

    # 詞庫/分頁參數:Sheet「掃描設定」→本機快取;失敗沿用快取/內建預設,不中斷
    global BAD_KW
    _, ss_msg = scan_settings.refresh()
    BAD_KW = _build_bad_kw()   # refresh 後重建(模組載入時建的可能是舊快取)
    print(ss_msg, flush=True)

    # 站清單快照:Sheet「府內網站表」→ 本機CSV。唯一事實來源在 Sheet,
    # 改站清單/網址/抓取方式只改 Sheet,這裡每次執行前自動下載;失敗沿用舊快照。
    if not args.verify:
        try:
            n = page_budget.refresh_csv(CSV_LIST)
            print(f"站清單快照:已從 Sheet 更新({n} 站)", flush=True)
        except Exception as e:
            print(f"[警告] 站清單快照更新失敗({type(e).__name__}),沿用既有快照", flush=True)

    if args.verify:
        outdir = args.verify if os.path.isabs(args.verify) else os.path.join(OUT_DIR, "reports", args.verify)
        stamp = os.path.basename(outdir).replace("full_overnight_", "")
    elif args.resume:
        outdir = args.resume if os.path.isabs(args.resume) else os.path.join(OUT_DIR, "reports", args.resume)
        stamp = os.path.basename(outdir).replace("full_overnight_", "")
    else:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        outdir = os.path.join(OUT_DIR, "reports", f"full_overnight_{stamp}")
    os.makedirs(outdir, exist_ok=True)
    combined = os.path.join(outdir, "all_problems.csv")
    progress = os.path.join(outdir, "progress.json")

    if args.verify:
        if not os.path.exists(combined):
            sys.exit(f"--verify 找不到 {combined}")
        t0 = time.time()
        prog = json.load(open(progress, encoding="utf-8")) if os.path.exists(progress) else []
        skipped = [p for p in prog if p.get("status") not in ("ok",)
                   and not str(p.get("status", "")).startswith("fail")]
        print(f"===== 複查模式 {stamp}:跳過階段1,對既有 all_problems.csv 重跑階段2-4 =====\n", flush=True)

    if not args.verify:
        # 續跑:載入已完成站的 URL
        done_urls = set()      # 已處理過的所有 URL(ok/skip)→ 續跑時跳過
        prior_prog = []
        if args.resume and os.path.exists(progress):
            prior_prog = json.load(open(progress, encoding="utf-8"))
            done_urls = {p.get("url") for p in prior_prog if not str(p.get("status","")).startswith("fail")}
            n_ok = sum(1 for p in prior_prog if p.get("status") == "ok")
            print(f"[續跑] 先前已完成 {n_ok} 站(含跳過共 {len(done_urls)}),將跳過續跑\n", flush=True)

        scan = config._cfg.get("scan", {})
        whitelist = tuple(w.strip().lower() for w in str(scan.get("content_whitelist","")).split(",") if w.strip())
        skip_hosts = tuple(w.strip().lower() for w in str(scan.get("skip_hosts","")).split(",") if w.strip())

        rows = list(csv.DictReader(open(CSV_LIST, encoding="utf-8-sig")))
        # 每列多抓合規旗標 + AI判讀題目(合規AI在階段1後串行跑)
        allsites = []
        ai_checks_map = {}   # url → [{"url":..,"question":..}]
        for r in rows:
            url = r.get("網址","").strip()
            if not url:
                continue
            name = r.get("網站名稱","").strip()
            org = r.get("局處","").strip()
            method = r.get("內容抓取方式","").strip()
            is_comp = r.get("合規檢核","").strip() == "是"
            allsites.append((name, url, org, method, is_comp))
            # 解析 AI 判讀題目
            ai_q = r.get("AI判讀題目","").strip()
            if is_comp and ai_q:
                if "|" in ai_q:
                    u, q = ai_q.split("|", 1)
                    ai_checks_map[url] = [{"url": u.strip(), "question": q.strip()}]
                else:
                    ai_checks_map[url] = [{"url": url, "question": ai_q}]
        if args.schedule_today:
            from engine.schedule import today_batch
            batch = today_batch()
            batch_urls = {url for _, url, _, _ in batch}
            allsites = [s for s in allsites if s[1] in batch_urls]
            day_n = ((datetime.date.today().day - 1) % 30) + 1
            print(f"[排程] Day {day_n}：篩出 {len(allsites)} 站\n", flush=True)
        if args.org: allsites = [s for s in allsites if s[2] == args.org]
        if args.only:
            toks = [t.strip().lower() for t in args.only.split(",") if t.strip()]
            allsites = [s for s in allsites if any(t in s[0].lower() or t in s[1].lower() for t in toks)]
        if args.limit: allsites = allsites[:args.limit]
        # 讀 Sheet「頁數」欄:已知站首掃上限=記錄值+100;新站用 --max-pages(1000)。撞牆再加碼。
        # --force-cap 時全用指定值、停用加碼。
        no_escalate = bool(args.force_cap)
        if args.force_cap:
            reg = {}
        else:
            try:
                reg = page_budget.read_sheet()
            except Exception as e:
                print(f"[警告] 讀 Sheet 頁數欄失敗({type(e).__name__}),全用首掃上限 {args.max_pages}", flush=True)
                reg = {}
        new_pages = {}
        n_known = 0
        tasks, skipped = [], []
        for name,url,org,method,is_comp in allsites:
            if url in done_urls:
                continue  # 續跑:已完成,跳過
            if method in SKIP_METHODS or norm_host(url) in SKIP_HOSTS:
                skipped.append({"name":name,"url":url,"org":org,"status":method or "skip","n_problems":0})
            else:
                first_cap = args.force_cap if args.force_cap else page_budget.get_cap(reg, url, first_default=args.max_pages)
                if url in reg: n_known += 1
                tasks.append((name,url,org,method,first_cap,outdir,whitelist,skip_hosts,is_comp,no_escalate))
        n_pw = sum(1 for t in tasks if t[3]=="playwright")
        t0 = time.time()
        print(f"===== 全網站深度稽核 {stamp} =====", flush=True)
        print(f"待掃 {len(tasks)}(playwright {n_pw} 會渲染)、跳過 {len(skipped)} | {args.workers} 行程", flush=True)
        print(f"已知 {n_known} 站(首掃=Sheet記錄+{page_budget.BUFFER})、新站首掃 {args.max_pages};撞牆→+2000→每次+3000 直到爬完(上限 {CEIL})", flush=True)
        print(f"白名單 {len(whitelist)} / skip {len(skip_hosts)} | 產出 {outdir}\n", flush=True)

        # ── 階段1 ──
        resuming = bool(args.resume) and os.path.exists(combined)
        cfp = open(combined, "a" if resuming else "w", newline="", encoding="utf-8-sig")
        cw = csv.DictWriter(cfp, fieldnames=["site_name","org"]+CSV_COLS, extrasaction="ignore")
        if not resuming:
            cw.writeheader(); cfp.flush()
        prog = list(prior_prog) + list(skipped); done = 0   # 保留先前進度
        try:
            ex = cf.ProcessPoolExecutor(max_workers=args.workers, max_tasks_per_child=1)  # 每站全新行程,釋放記憶體
        except TypeError:
            ex = cf.ProcessPoolExecutor(max_workers=args.workers)  # 舊版無 max_tasks_per_child
        with ex:
            futs = {ex.submit(audit_one, t): t for t in tasks}
            for fut in cf.as_completed(futs):
                try: r = fut.result()
                except Exception as e:
                    r = {"name":"?","url":"?","org":"","status":f"fail:{type(e).__name__}","problems":[],"n_problems":0,"links":0,"suspicious":0,"dead":0}
                for p in r["problems"]:
                    cw.writerow({"site_name":r["name"],"org":r["org"],**p})
                cfp.flush()
                prog.append({k:v for k,v in r.items() if k not in ("problems",)})
                json.dump(prog, open(progress,"w",encoding="utf-8"), ensure_ascii=False)
                if r.get("status") == "ok":
                    new_pages[r["url"]] = r.get("pages", 0)   # 本次真實頁數,結束寫回 Sheet
                done += 1
                rounds = r.get("rounds", 0)
                mark = f"(加碼{rounds}次→{r.get('final_cap')})" if rounds else ""
                if r.get("capped"): mark += "⚠仍撞上限"
                print(f"[階段1 {done}/{len(tasks)}] {r['name'][:16]:18} {r.get('pages',0)}頁{mark} 連結{r['links']} 異常{r['n_problems']}(搶註{r['suspicious']} 死{r['dead']}) [{r['status']}] {time.time()-t0:.0f}s", flush=True)
        cfp.close()
        # 把本次真實頁數寫回 Sheet「頁數」欄(URL 對鍵,只在變大時更新 → 記錄各站最大值)
        # --force-cap 時跳過(淺掃頁數無參考價值)
        if args.force_cap:
            print(f"\n[force-cap] 跳過頁數寫回 Sheet(淺掃頁數無參考價值)", flush=True)
        else:
            try:
                chg = page_budget.write_sheet(new_pages)
                print(f"\n頁數已寫回 Sheet:本次 {len(new_pages)} 站,更新記錄 {chg} 站", flush=True)
            except Exception as e:
                print(f"\n[警告] 頁數寫回 Sheet 失敗({type(e).__name__});頁數仍存於 progress.json", flush=True)
        # ── 合規結果蒐集 → compliance.json ──
        compliance_all = {}
        for p in prog:
            name = p.get("name", "")
            url = p.get("url", "")
            if not name or not url:
                continue
            comp = p.get("compliance", {})
            if not comp:
                continue
            compliance_all[name] = {
                "name": name, "url": url, "org": p.get("org", ""),
                "compliance_flag": bool(p.get("is_compliance")),
                "urls": {url: comp}, "ai": [],
            }
        # ── 合規 AI 判讀(只對合規站,串行) ──
        comp_ai_sites = [(name, url, ai_checks_map.get(url, []))
                         for name, url, org, method, is_comp in allsites
                         if is_comp and url in ai_checks_map
                         and name in compliance_all]
        if comp_ai_sites:
            from engine.compliance import run_ai_checks
            print(f"\n===== 合規 AI 判讀({len(comp_ai_sites)} 站) =====", flush=True)
            for i, (name, url, checks) in enumerate(comp_ai_sites, 1):
                print(f"[合規AI {i}/{len(comp_ai_sites)}] {name[:20]}...", flush=True)
                try:
                    ai_results = run_ai_checks(checks)
                    compliance_all[name]["ai"] = ai_results
                    for ar in ai_results:
                        print(f"  Q: {ar['question'][:40]}  A: {ar['answer'][:60]}", flush=True)
                except Exception as e:
                    print(f"  !! AI失敗: {e}", flush=True)
        # 寫 compliance.json
        comp_path = os.path.join(outdir, "compliance.json")
        json.dump(compliance_all, open(comp_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        n_comp = sum(1 for v in compliance_all.values() if v.get("compliance_flag"))
        print(f"\n合規結果:{len(compliance_all)} 站(含 {n_comp} 合規站) → {comp_path}", flush=True)
        print(f"\n階段1 完成 {time.time()-t0:.0f}s\n", flush=True)

    # ── 階段2 + 3 ──
    allp = list(csv.DictReader(open(combined, encoding="utf-8-sig")))
    susp = {}
    for r in allp:
        if r["risk"]=="SUSPICIOUS" and r["url"] not in susp: susp[r["url"]] = r
    print(f"===== 階段2/3:AI 複查 + 型態確認 {len(susp)} 筆 SUSPICIOUS =====", flush=True)
    verified = []
    for i,(u,r) in enumerate(susp.items(),1):
        # 根路徑加驗圈到的:關鍵字在根路徑,深層頁可能 404/乾淨,AI 要驗根路徑才看得到證據
        vu = u
        if "根路徑" in (r.get("note") or ""):
            pu = urllib.parse.urlparse(u)
            vu = f"{pu.scheme or 'https'}://{pu.netloc}/"
        verdict, reason, html = ai_verify(vu)
        ctype, cevidence = ("", "")
        if verdict in ("A","B") and html:
            ctype, cevidence = characterize(u, html)
        elif verdict in ("A","B"):
            ctype, cevidence = "停放/失效", reason
        verified.append({"ai_verdict":verdict,"type":ctype,"url":u,"site":r["site_name"],"org":r.get("org",""),
                         "found_on":r.get("found_on",""),"kw":r["note"],"ai_reason":reason,"evidence":cevidence})
        tag={"A":"🔴","B":"🟠","C":"🟢","?":"❓"}.get(verdict,verdict)
        print(f"[階段2/3 {i}/{len(susp)}] {tag}{verdict} {ctype:12} {r['site_name'][:16]:18} {u[:44]}", flush=True)
        json.dump(verified, open(os.path.join(outdir,"suspicious_verified.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)

    # ── 階段4 ──
    with open(os.path.join(outdir,"suspicious_verified.csv"),"w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f, fieldnames=["ai_verdict","type","url","site","org","found_on","kw","ai_reason","evidence"]); w.writeheader(); w.writerows(verified)
    confirmed = [v for v in verified if v["ai_verdict"]=="A" or (v["ai_verdict"]=="B" and v["type"] not in ("停放/失效","待人工"))]
    from collections import Counter
    ok=[p for p in prog if p.get("status")=="ok"]
    summary={"stamp":stamp,"mode":"verify" if args.verify else "full","sites_scanned":len(ok),"skipped":len(skipped),
             "total_anomalies":len(allp),
             "by_risk":dict(Counter(r["risk"] for r in allp)),
             "suspicious_checked":len(susp),
             "ai_verdicts":dict(Counter(v["ai_verdict"] for v in verified)),
             "confirmed_hijacks":len([v for v in verified if v["ai_verdict"]=="A"]),
             "duration_sec":int(time.time()-t0)}
    json.dump(summary, open(os.path.join(outdir,"summary.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
    # 確認清單
    with open(os.path.join(outdir,"CONFIRMED_hijacks.csv"),"w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f, fieldnames=["type","url","site","org","found_on","evidence","ai_reason"])
        w.writeheader()
        for v in verified:
            if v["ai_verdict"]=="A": w.writerow({k:v.get(k,"") for k in ["type","url","site","org","found_on","evidence","ai_reason"]})
    print(f"\n===== 全部完成 {int(time.time()-t0)}s =====", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=1), flush=True)
    print(f"\n真搶註/掛馬(AI判A):{summary['confirmed_hijacks']} 筆 → CONFIRMED_hijacks.csv", flush=True)
    print(f"全部產出:{outdir}", flush=True)

    # ── HTML 報告產生（本次 status=ok 的站）──
    if not args.no_report:
        try:
            from engine.report_html import generate_for_sites
            ok_urls = [p["url"] for p in prog if p.get("status") == "ok" and p.get("url")]
            if ok_urls:
                print(f"\n[報告] 自動產生 {len(ok_urls)} 站的單站 HTML 報告...", flush=True)
                paths = generate_for_sites(ok_urls)
                print(f"[報告] 完成, 產出 {len(paths)} 份 → private/reports_html/", flush=True)
        except Exception as e:
            print(f"\n[警告] HTML 報告產生失敗({type(e).__name__}: {e}), 不影響掃描結果", flush=True)

    # ── 按局處寄信（需 --mail）──
    if args.mail:
        try:
            from engine.mailer import run as mailer_run
            rcpt_desc = args.mail_to or "各局處Email真值"
            dry_tag = " [DRY-RUN]" if args.dry_run else ""
            print(f"\n[寄信] 按局處彙整寄信(收件人: {rcpt_desc}){dry_tag}...", flush=True)
            sent, skipped, details = mailer_run(outdir, mail_to=args.mail_to or None, dry_run=args.dry_run)
            print(f"[寄信] 完成: 寄出 {sent} 封, 跳過 {len(skipped)} 局處(零真問題)", flush=True)
        except Exception as e:
            print(f"\n[警告] 寄信失敗({type(e).__name__}: {e})", flush=True)


if __name__ == "__main__":
    import multiprocessing; multiprocessing.freeze_support(); main()
