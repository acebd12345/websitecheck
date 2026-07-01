# -*- coding: utf-8 -*-
"""
政府網站對外連結稽核工具
爬取目標網站站內頁面,收集所有對外連結,檢查:
  1. 連結失效 (DNS 解析失敗 / 連線錯誤 / 4xx 5xx)
  2. 網域疑似被註冊走 (重導向到無關網域、停放頁、賭博/色情關鍵字)
用法: python audit_links.py [起始網址] [最大爬取頁數]
     無參數執行時進入互動模式(供 exe 雙擊使用)
     批次掃描多網站請用 batch_audit.py
"""
import re
import sys
import csv
import json
import socket
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup

requests.packages.urllib3.disable_warnings()

TIMEOUT = 12
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TaipeiLinkAudit/1.0"}

# 政府/教育網域視為低風險(仍檢查是否失效)
TRUSTED_SUFFIXES = (".gov.tw", ".gov.taipei", ".taipei", ".edu.tw", ".org.tw", ".mil.tw")

# 可疑內容關鍵字(賭博、色情、停放頁)
SUSPICIOUS_KEYWORDS = [
    "娛樂城", "百家樂", "博弈", "博彩", "賭場", "老虎機", "捕魚機", "六合彩",
    "casino", "baccarat", "slot", "betting", "poker", "jackpot",
    "色情", "成人影片", "成人視訊", "情色", "約砲", "av女優", "無碼",
    "porn", "hentai", "xvideo", "live sex", "escort",
    "виагра", "viagra", "cialis",
    "domain is for sale", "buy this domain", "此網域可供出售", "域名出售",
    "parked domain", "sedoparking", "godaddy.com/domainsearch",
]

# 比對前先剔除的善意詞(避免「白色情人節」誤撞「色情」這類子字串誤判)
BENIGN_PHRASES = ["白色情人節"]


def _kw_pattern(kw):
    """英文關鍵字要求整字比對(避免 specialise 撞到 cialis),中文做子字串比對"""
    if all(ord(c) < 128 for c in kw):
        return re.compile(r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])")
    return re.compile(re.escape(kw.lower()))


KEYWORD_PATTERNS = [(kw, _kw_pattern(kw)) for kw in SUSPICIOUS_KEYWORDS]

RISK_ORDER = {"SUSPICIOUS": 0, "DEAD": 1, "BROKEN": 2, "REDIRECTED": 3, "WARN": 4, "OK": 5}

CSV_COLS = ["risk", "url", "host", "trusted_gov", "dns", "status",
            "final_host_changed", "final_url", "title", "note",
            "occurrences", "found_on_title", "found_on", "anchor", "all_locations"]


def norm_host(url):
    try:
        return urllib.parse.urlsplit(url).hostname or ""
    except Exception:
        return ""


def is_trusted(host):
    host = (host or "").lower()
    return host.endswith(TRUSTED_SUFFIXES)


def fetch_page(sess, page):
    try:
        r = sess.get(page, timeout=TIMEOUT, verify=False)
        if "html" not in r.headers.get("Content-Type", ""):
            return None
        return r
    except Exception:
        return None


def crawl_internal(start_url, max_pages, links_log_path="external_links.jsonl"):
    """BFS 爬站內頁面(多執行緒),回傳 {外部連結: [出現位置, ...]}"""
    start_host = norm_host(start_url)
    seen_pages = {start_url}
    external = {}
    frontier = [start_url]
    sess = requests.Session()
    sess.headers.update(HEADERS)
    adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    pages_done = 0
    links_log = open(links_log_path, "w", encoding="utf-8")

    with ThreadPoolExecutor(max_workers=8) as ex:
        while frontier and pages_done < max_pages:
            batch = frontier[: min(40, max_pages - pages_done)]
            frontier = frontier[len(batch):]
            next_frontier = []
            for r in ex.map(lambda p: fetch_page(sess, p), batch):
                if r is None:
                    continue
                pages_done += 1
                soup = BeautifulSoup(r.text, "html.parser")
                page_title = ""
                if soup.title and soup.title.string:
                    page_title = soup.title.string.strip()[:80]
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    absu = urllib.parse.urljoin(r.url, href)
                    absu = urllib.parse.urldefrag(absu)[0]
                    # 只檢查 http/https,排除 opay:// jkos:// mailto: 等 App 協議
                    if not absu.lower().startswith(("http://", "https://")):
                        continue
                    host = norm_host(absu)
                    if not host:
                        continue
                    if host == start_host:
                        if absu not in seen_pages:
                            seen_pages.add(absu)
                            next_frontier.append(absu)
                    else:
                        occ = {"found_on": r.url, "page_title": page_title,
                               "anchor": a.get_text(strip=True)[:50]}
                        occs = external.setdefault(absu, [])
                        # 同一頁同一連結只記一次,每個連結最多記 50 個出現位置
                        if len(occs) < 50 and not any(o["found_on"] == r.url for o in occs):
                            occs.append(occ)
                            links_log.write(json.dumps(
                                {"url": absu, **occ}, ensure_ascii=False) + "\n")
                            links_log.flush()
            frontier.extend(next_frontier)
            print(f"  已爬 {pages_done} 頁,佇列 {len(frontier)},外部連結 {len(external)} 筆")

    links_log.close()
    print(f"爬取完成:共 {pages_done} 頁,收集到 {len(external)} 筆外部連結"
          + (f"(佇列仍剩 {len(frontier)} 頁未爬)" if frontier else "(全站爬完)"))
    return external


def check_external(url, occs, content_whitelist=(), skip_hosts=()):
    """檢查單一外部連結,回傳結果 dict(occs 為所有出現位置清單)
    content_whitelist: 確認過內容無虞的網域,跳過關鍵字檢查(仍檢查連線/狀態)
    skip_hosts: 人工確認正常但有防爬蟲的網域,完全略過檢測(視為 OK)"""
    host = norm_host(url)
    first = occs[0]
    all_loc = "\n".join(
        f"{o['page_title']} | {o['found_on']} | 連結文字: {o['anchor']}" for o in occs)
    result = {
        "url": url, "host": host,
        "occurrences": len(occs),
        "found_on": first["found_on"], "found_on_title": first["page_title"],
        "anchor": first["anchor"], "all_locations": all_loc,
        "trusted_gov": "Y" if is_trusted(host) else "N",
        "dns": "", "status": "", "final_url": "", "final_host_changed": "",
        "title": "", "risk": "OK", "note": "",
    }
    if any(host == w or host.endswith("." + w) for w in skip_hosts):
        result["note"] = "免檢名單:已人工確認正常(網站有防爬蟲機制),略過檢測"
        return result
    # 1. DNS 解析
    try:
        socket.getaddrinfo(host, None)
        result["dns"] = "OK"
    except socket.gaierror:
        result["dns"] = "FAIL"
        result["risk"] = "DEAD"
        result["note"] = "DNS 解析失敗(網域可能已釋出,留意被搶註風險)"
        return result
    # 2. HTTP 請求
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS, verify=False, allow_redirects=True)
        result["status"] = str(r.status_code)
        result["final_url"] = r.url
        final_host = norm_host(r.url)
        if final_host and final_host != host:
            result["final_host_changed"] = f"{host} -> {final_host}"
        # 3. 內容檢查
        body_lower = ""
        title = ""
        if "html" in r.headers.get("Content-Type", ""):
            soup = BeautifulSoup(r.text[:200000], "html.parser")
            if soup.title and soup.title.string:
                title = soup.title.string.strip()[:80]
            body_lower = (title + " " + soup.get_text(" ", strip=True)[:5000] + " " + r.url).lower()
        result["title"] = title
        whitelisted = any(host == w or host.endswith("." + w) for w in content_whitelist)
        for ph in BENIGN_PHRASES:
            body_lower = body_lower.replace(ph.lower(), " ")
        hits = [] if whitelisted else [
            kw for kw, pat in KEYWORD_PATTERNS if pat.search(body_lower)]
        if hits:
            result["risk"] = "SUSPICIOUS"
            result["note"] = "命中可疑關鍵字: " + ", ".join(hits[:5])
        elif r.status_code >= 400:
            result["risk"] = "BROKEN"
            result["note"] = f"HTTP {r.status_code}"
        elif result["final_host_changed"] and not is_trusted(final_host) and not is_trusted(host):
            result["risk"] = "REDIRECTED"
            result["note"] = "重導向到不同網域,建議人工確認"
    except requests.exceptions.SSLError:
        result["risk"] = "WARN"
        result["note"] = "SSL 憑證錯誤"
    except Exception as e:
        result["risk"] = "DEAD"
        result["note"] = f"連線失敗: {type(e).__name__}"
    return result


def audit_site(start_url, max_pages=5000, links_log_path="external_links.jsonl",
               content_whitelist=(), skip_hosts=()):
    """稽核單一網站,回傳排序後的結果 list(供批次腳本呼叫)"""
    print(f"開始稽核: {start_url} (最多爬 {max_pages} 頁)")
    external = crawl_internal(start_url, max_pages, links_log_path)
    results = []
    print(f"開始檢測 {len(external)} 筆外部連結...")
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(check_external, u, o, content_whitelist, skip_hosts)
                for u, o in external.items()]
        for n, fut in enumerate(futs, 1):
            results.append(fut.result())
            if n % 100 == 0:
                print(f"  已檢測 {n}/{len(external)}")
    results.sort(key=lambda r: (RISK_ORDER.get(r["risk"], 9), r["host"]))
    return results


def write_csv(results, out_path):
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS)
        w.writeheader()
        w.writerows(results)


def print_summary(results):
    print("\n===== 稽核結果統計 =====")
    for k in ["SUSPICIOUS", "DEAD", "BROKEN", "REDIRECTED", "WARN", "OK"]:
        c = sum(1 for r in results if r["risk"] == k)
        if c:
            print(f"  {k}: {c}")
    problems = [r for r in results if r["risk"] != "OK"]
    if problems:
        print("\n----- 需注意項目 -----")
        for r in problems:
            print(f"[{r['risk']}] {r['url']}")
            print(f"    {r['note']}")
            print(f"    出現位置({r['occurrences']} 處):")
            for line in r["all_locations"].splitlines():
                print(f"      - {line}")


def main():
    interactive = len(sys.argv) < 2
    if interactive:
        print("=" * 50)
        print(" 網站對外連結稽核工具")
        print(" 檢查失效連結、網域被搶註、導向賭博色情等情形")
        print("=" * 50)
        _u = input("請輸入要稽核的網站網址 (直接按 Enter = https://service.taipei/): ").strip()
        start_url = _u if _u else "https://service.taipei/"
        if not start_url.startswith("http"):
            start_url = "https://" + start_url
        max_pages = 5000
    else:
        start_url = sys.argv[1]
        max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 5000

    try:
        results = audit_site(start_url, max_pages)
        out = f"link_audit_report_{norm_host(start_url).replace('.', '_')}.csv"
        write_csv(results, out)
        print_summary(results)
        print(f"\n報告已輸出: {out}")
    except Exception:
        import traceback
        traceback.print_exc()
    if interactive:
        input("\n執行完畢,按 Enter 關閉視窗...")


if __name__ == "__main__":
    main()
