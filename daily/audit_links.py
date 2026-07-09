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
import os
import socket
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup

requests.packages.urllib3.disable_warnings()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scan_settings  # 詞庫/分頁參數單一來源(Sheet掃描設定→快取→內建預設)

TIMEOUT = 12
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TaipeiLinkAudit/1.0",
           # 缺 Accept 時部分政府 .aspx 站(如 wifi.taipei)會回 500,故明確帶上
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"}

# ── 渲染由「內容抓取方式」驅動:只有 playwright 站才開,靜態站全程不渲染 ──
# 呼叫端(batch_audit / full_overnight)依該站方法在掃描前設定 ALLOW_RENDER。
import threading
ALLOW_RENDER = False        # 預設關;playwright 站才由呼叫端設 True
_RENDER_CAP = 60            # 開啟時每站最多渲染幾頁空殼(避免整站 SPA 每頁都渲染)
_render_lock = threading.Lock()
_render_count = 0

def _reset_render_budget():
    global _render_count
    with _render_lock:
        _render_count = 0

def _take_render_budget():
    global _render_count
    with _render_lock:
        if _render_count < _RENDER_CAP:
            _render_count += 1
            return True
        return False

# 政府/教育網域視為低風險(仍檢查是否失效)
TRUSTED_SUFFIXES = (".gov.tw", ".gov.taipei", ".taipei", ".edu.tw", ".org.tw", ".mil.tw")

# 只有臺灣官方能註冊、民間永遠碰不到 → 不可能被搶註,略過內容掃描(仍驗存活)
# 依 TWNIC 規章:gov.tw/mil.tw(數發部/國防部審核)、gov.taipei(北市府 ICANN 地理頂域)
# 注意:.edu.tw(學校)、.org.tw(協會)、.tw/.taipei(泛用) 民間都能註冊,一律全檢,不列此
GOV_EXCLUSIVE_SUFFIXES = (".gov.tw", ".mil.tw", ".gov.taipei", ".政府.tw", ".軍事.tw", ".xn--kpry57d")  # 末項為 .台灣 punycode 備用

# 社群/分享/短網址:重導到不同網域是設計行為,非問題 → 只驗存活,不抓內容/不判重導
SOCIAL_SKIP_HOSTS = ("line.me", "lin.ee", "line.naver.jp", "facebook.com", "fb.com", "fb.watch",
    "twitter.com", "x.com", "instagram.com", "youtube.com", "youtu.be", "google.com", "goo.gl",
    "forms.gle", "docs.google.com", "maps.app.goo.gl", "reurl.cc", "lihi.cc", "lihi1.cc", "lihi2.cc",
    "lihi3.cc", "bit.ly", "pse.is", "tinyurl.com", "t.me", "threads.net", "linkedin.com")

# 可疑內容關鍵字(賭博、色情 + 停放頁):單一來源見 scan_settings(Sheet 可調)
SUSPICIOUS_KEYWORDS = (scan_settings.get("suspicious_keywords")
                       + scan_settings.get("parked_keywords"))

# 比對前先剔除的善意詞(避免「白色情人節」誤撞「色情」這類子字串誤判)
# 帶點的 TLD 商品名:域名註冊商首頁把 .casino/.poker/.bet 當商品賣,非賭博內容
BENIGN_PHRASES = scan_settings.get("benign_phrases")


def _kw_pattern(kw):
    """英文關鍵字要求整字比對(避免 specialise 撞到 cialis),中文做子字串比對。
    邊界字元類須含拉丁擴充(é ü 等),否則法文 spécialiste 的 é 非 ASCII、
    ASCII-only 邊界會誤判成立而撞到 cialis(www.un.org/fr 實例)。"""
    if all(ord(c) < 128 for c in kw):
        b = r"[a-z0-9À-ɏ]"
        return re.compile(r"(?<!" + b + r")" + re.escape(kw.lower()) + r"(?!" + b + r")")
    return re.compile(re.escape(kw.lower()))


KEYWORD_PATTERNS = [(kw, _kw_pattern(kw)) for kw in SUSPICIOUS_KEYWORDS]

# 根路徑關鍵字快取(host -> 命中清單)。搶註者常只在根路徑放賭博/色情內容,
# 深層路徑(如 /default.html)回 200 但內容乾淨,只掃被連到的那頁會漏
# (實例:taitraesource.com/default.html 乾淨,/ 是 Dewa77 賭場)。
_ROOT_KW_CACHE = {}


def _root_keyword_hits(host):
    """抓 host 根路徑掃可疑關鍵字,每 host 只抓一次(快取)。"""
    if host in _ROOT_KW_CACHE:
        return _ROOT_KW_CACHE[host]
    hits = []
    for scheme in ("https", "http"):
        try:
            rr = requests.get(f"{scheme}://{host}/", timeout=TIMEOUT, headers=HEADERS,
                              verify=False, allow_redirects=True)
            if "html" in rr.headers.get("Content-Type", ""):
                soup = BeautifulSoup(rr.text[:200000], "html.parser")
                t = soup.title.string.strip()[:80] if soup.title and soup.title.string else ""
                body = (t + " " + soup.get_text(" ", strip=True)[:5000] + " " + rr.url).lower()
                for ph in BENIGN_PHRASES:
                    body = body.replace(ph.lower(), " ")
                hits = [kw for kw, pat in KEYWORD_PATTERNS if pat.search(body)]
            break
        except Exception:
            continue
    _ROOT_KW_CACHE[host] = hits
    return hits

RISK_ORDER = {"SUSPICIOUS": 0, "DEAD": 1, "BROKEN": 2, "REDIRECTED": 3, "WARN": 4, "OK": 5}

# 分頁/清單參數:含這些的內部 URL 視為分頁,不再往下挖(避免月曆/分頁製造無限內部頁)
PAGINATION_PARAMS = {p.lower() for p in scan_settings.get("pagination_params")}
# crawl_internal 每次執行後,把「實際爬幾頁 / 是否因上限截斷」寫到這(呼叫端讀取)
LAST_CRAWL = {"pages": 0, "capped": False}


def _is_pagination_url(url):
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        return any(k.lower() in PAGINATION_PARAMS for k in q)
    except Exception:
        return False

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
    """抓一頁:靜態優先,判為 JS 空殼且未超渲染額度時升級 Playwright。
    回傳 (最終網址, html) 或 None。(整併:讓 JS 站也爬得到內部連結)"""
    try:
        r = sess.get(page, timeout=TIMEOUT, verify=False)
        if "html" not in r.headers.get("Content-Type", ""):
            return None
        html, final_url = r.text, r.url
    except Exception:
        return None
    # 只有 playwright 站(ALLOW_RENDER=True)才做空殼偵測+渲染;靜態站全程靜態
    if ALLOW_RENDER:
        try:
            from engine.fetch_layered import detect_shell, render_to_html
            text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
            need, _ = detect_shell(html, text)
            if need and _take_render_budget():
                rhtml, _note = render_to_html(page)
                if rhtml:
                    html = rhtml
        except Exception:
            pass  # engine 不可用(如獨立 exe)→ 退回純靜態
    return final_url, html


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
    _reset_render_budget()  # 每站重置渲染額度
    links_log = open(links_log_path, "w", encoding="utf-8")

    with ThreadPoolExecutor(max_workers=8) as ex:
        while frontier and pages_done < max_pages:
            batch = frontier[: min(40, max_pages - pages_done)]
            frontier = frontier[len(batch):]
            next_frontier = []
            for res in ex.map(lambda p: fetch_page(sess, p), batch):
                if res is None:
                    continue
                final_url, html = res
                pages_done += 1
                soup = BeautifulSoup(html, "html.parser")
                page_title = ""
                if soup.title and soup.title.string:
                    page_title = soup.title.string.strip()[:80]
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    absu = urllib.parse.urljoin(final_url, href)
                    absu = urllib.parse.urldefrag(absu)[0]
                    # 只檢查 http/https,排除 opay:// jkos:// mailto: 等 App 協議
                    if not absu.lower().startswith(("http://", "https://")):
                        continue
                    host = norm_host(absu)
                    if not host:
                        continue
                    if host == start_host:
                        if absu not in seen_pages and not _is_pagination_url(absu):
                            seen_pages.add(absu)
                            next_frontier.append(absu)
                    else:
                        occ = {"found_on": final_url, "page_title": page_title,
                               "anchor": a.get_text(strip=True)[:50]}
                        occs = external.setdefault(absu, [])
                        # 同一頁同一連結只記一次,每個連結最多記 50 個出現位置
                        if len(occs) < 50 and not any(o["found_on"] == final_url for o in occs):
                            occs.append(occ)
                            links_log.write(json.dumps(
                                {"url": absu, **occ}, ensure_ascii=False) + "\n")
                            links_log.flush()
            frontier.extend(next_frontier)
            print(f"  已爬 {pages_done} 頁,佇列 {len(frontier)},外部連結 {len(external)} 筆")

    links_log.close()
    LAST_CRAWL["pages"] = pages_done
    LAST_CRAWL["capped"] = bool(frontier)   # 還有佇列=撞上限截斷;空=全站爬完
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
    except (socket.gaierror, UnicodeError, OSError):
        # UnicodeError:畸形主機名(空/超長標籤)IDNA 編碼失敗,一條壞連結不能炸掉整站稽核
        result["dns"] = "FAIL"
        result["risk"] = "DEAD"
        # gov 專屬域民間註冊不到,DNS 失敗只可能是子網域下線/內網限定(split-horizon),不是被搶註
        if host.endswith(GOV_EXCLUSIVE_SUFFIXES):
            result["note"] = "DNS 解析失敗(政府專屬網域無搶註可能;多為服務下線或僅內網可解)"
        else:
            result["note"] = "DNS 解析失敗(網域可能已釋出,留意被搶註風險)"
        return result
    # 1.5 政府專屬域 / 社群短網址 / 白名單:只驗存活,跳過內容抓取與關鍵字掃描
    #     (政府專屬域民間註冊不到→不可能被搶註;社群短網址重導為設計行為;白名單已人工確認無虞)
    gov_excl = host.endswith(GOV_EXCLUSIVE_SUFFIXES)
    social = any(host == s or host.endswith("." + s) for s in SOCIAL_SKIP_HOSTS)
    wl = any(host == w or host.endswith("." + w) for w in content_whitelist)
    if gov_excl or social or wl:
        try:
            rr = requests.head(url, timeout=TIMEOUT, headers=HEADERS, verify=False, allow_redirects=True)
            code = rr.status_code
            if code >= 400:  # 很多政府/CMS 伺服器不支援 HEAD(www.gov.taipei HEAD=404 但 GET=200)
                rr = requests.get(url, timeout=TIMEOUT, headers=HEADERS, verify=False,
                                  allow_redirects=True, stream=True)  # GET 再確認,stream 不讀 body
                code = rr.status_code; rr.close()
            result["status"] = str(code)
            if code >= 400:
                result["risk"] = "BROKEN"; result["note"] = f"HTTP {code}"
            else:
                result["note"] = ("政府專屬網域(民間不可註冊、不可能被搶註),僅驗存活" if gov_excl
                                  else "白名單(已確認內容無虞),僅驗存活" if wl
                                  else "社群/短網址(重導為設計行為),僅驗存活")
        except requests.exceptions.SSLError:
            result["risk"] = "WARN"; result["note"] = "SSL 憑證錯誤"
        except Exception as e:
            result["risk"] = "DEAD"; result["note"] = f"連線失敗: {type(e).__name__}"
        return result
    # 2. HTTP 請求(其餘一律全檢:抓內容+掃搶註/賭博/色情關鍵字)
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
        if not hits and not whitelisted:
            # 深層路徑乾淨仍要驗根路徑:搶註者常只在首頁放賭博/色情
            pu = urllib.parse.urlparse(r.url or url)
            if pu.path not in ("", "/") or pu.query:
                rhits = _root_keyword_hits(final_host or host)
                if rhits:
                    result["risk"] = "SUSPICIOUS"
                    result["note"] = ("根路徑命中可疑關鍵字: " + ", ".join(rhits[:5])
                                      + "(被連頁面正常,疑網域已遭搶註)")
                    return result
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
        urls = list(external.keys())
        for n, fut in enumerate(futs, 1):
            try:
                results.append(fut.result())
            except Exception as e:
                # 單條連結檢測炸掉不可毀整站:記為 DEAD 續行
                u = urls[n - 1]
                results.append({"url": u, "host": norm_host(u), "occurrences": len(external[u]),
                                "found_on": external[u][0]["found_on"],
                                "found_on_title": external[u][0]["page_title"],
                                "anchor": external[u][0]["anchor"], "all_locations": "",
                                "trusted_gov": "N", "dns": "", "status": "", "final_url": "",
                                "final_host_changed": "", "title": "",
                                "risk": "DEAD", "note": f"檢測例外: {type(e).__name__}"})
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
