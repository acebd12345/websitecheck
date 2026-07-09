# -*- coding: utf-8 -*-
"""靜態優先深層 BFS 爬蟲(整併 stage 3:引擎統一 做完整)。

移植自 TCGweb crawler/web_crawler.py 的有價值能力,但每頁抓取改走 engine.fetch_layered
(靜態優先、必要才渲染)。因為靜態抓取很輕,故**砍掉 TCGweb 原本的 multiprocessing +
psutil 記憶體監控 + worker 自動重啟**那整套(那是每頁渲染吃記憶體才需要的)。

保留:深度 BFS、sitemap 偵測與主內容連結抽取、內/外連結拆分、外部連結狀態檢查、
      同標題/分頁去重、日期抽取(engine.dates)。
產出:{pages:[{url,status,title,last_updated,depth,method}], external:{url:status}, stats:{}}

用法:
  python -m engine.crawl https://culture.gov.taipei/ --depth 1
  python -m engine.crawl https://xxx/ --depth 2 --no-external   (不查外連,更快)
"""
import argparse
import concurrent.futures
import ssl
import sys
import urllib.parse
import urllib.request
from collections import deque

import os
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "monthly"))
import scan_settings

from bs4 import BeautifulSoup
from engine.fetch_layered import fetch_layered
from engine import dates as dates_mod
import webcheck_ai

_CTX = ssl.create_default_context(); _CTX.check_hostname = False; _CTX.verify_mode = ssl.CERT_NONE
_UA = {"User-Agent": webcheck_ai.UA, "Accept": "text/html,*/*;q=0.8", "Accept-Language": "zh-TW"}
SKIP_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar", ".7z",
            ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".ico",
            ".mp4", ".avi", ".mov", ".wmv", ".mp3", ".wav", ".txt", ".csv", ".json", ".xml"}
# 單一來源(scan_settings):16 參數完整版,含月曆類(date/month/year),
# 之前這裡只有 8 個、缺月曆參數 → 健康剖面爬蟲會掉進月曆無限頁陷阱
PAGINATION_PARAMS = {p.lower() for p in scan_settings.get("pagination_params")}
SITEMAP_KW = ("sitemap", "網站導覽", "網頁導覽", "webmap")


def _is_skip_url(url):
    p = urllib.parse.urlparse(url).path.lower()
    return any(p.endswith(e) for e in SKIP_EXT)


def check_link_status(url):
    """HEAD 先試,失敗碼退 GET;HTTP 失敗試 HTTPS。回傳 (url, status)。status 0=連不上。"""
    def _try(u):
        req = urllib.request.Request(u, headers=_UA, method="HEAD")
        try:
            r = urllib.request.urlopen(req, timeout=12, context=_CTX)
            if r.status in (403, 404, 405):
                raise urllib.error.HTTPError(u, r.status, "retry-get", None, None)
            return r.status
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 405):
                try:
                    rq = urllib.request.Request(u, headers=_UA)
                    return urllib.request.urlopen(rq, timeout=12, context=_CTX).status
                except urllib.error.HTTPError as e2:
                    return e2.code
            return e.code
    try:
        return url, _try(url)
    except Exception:
        if url.startswith("http://"):
            try:
                return url, _try(url.replace("http://", "https://", 1))
            except Exception:
                return url, 0
        return url, 0


def extract_links(html, base_url):
    """回傳 (internal_links set, external_links set)。同網域為內部。"""
    soup = BeautifulSoup(html, "html.parser")
    base_dom = urllib.parse.urlparse(base_url).netloc
    internal, external = set(), set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        link = urllib.parse.urljoin(base_url, href).split("#")[0]
        dom = urllib.parse.urlparse(link).netloc
        if not dom:
            continue
        (internal if dom == base_dom else external).add(link)
    return internal, external


def find_sitemap(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        blob = (a.get("href", "") + " " + a.get("title", "") + " " + a.get_text(strip=True)).lower()
        if any(k in blob for k in SITEMAP_KW):
            return urllib.parse.urljoin(base_url, a["href"]).split("#")[0]
    return None


def sitemap_links(html, sitemap_url):
    """從 sitemap 頁主內容區抽連結(同網域)。抽不到回傳空 set。"""
    soup = BeautifulSoup(html, "html.parser")
    sels = ['main', '[role="main"]', '#main', '#content', '#main-content', '.main', '.content',
            '.main-content', '#CCMS_Content', '[id*="main"]', '[id*="content"]', '[class*="content"]']
    base_dom = urllib.parse.urlparse(sitemap_url).netloc
    for sel in sels:
        try:
            els = soup.select(sel)
        except Exception:
            continue
        if els and els[0].find_all("a", href=True):
            links = set()
            for a in els[0].find_all("a", href=True):
                href = a["href"]
                if href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    continue
                link = urllib.parse.urljoin(sitemap_url, href).split("#")[0]
                if urllib.parse.urlparse(link).netloc == base_dom:
                    links.add(link)
            if links:
                return links
    return set()


def _page_title(html, url):
    soup = BeautifulSoup(html, "html.parser")
    return (soup.title.string.strip() if soup.title and soup.title.string else url.split("/")[-1] or "index")


def _is_pagination(url):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return any(k.lower() in PAGINATION_PARAMS for k in q)


def crawl_site(start_url, name="", max_depth=1, check_external=True, allow_render=True, use_sitemap=True):
    if not name:
        name = urllib.parse.urlparse(start_url).netloc
    base_dom = urllib.parse.urlparse(start_url).netloc
    visited, pages, seen_titles = set(), [], set()
    ext_seen = {}

    def fetch_page(url, depth):
        """抓一頁:靜態優先,回傳 (page_record, internal_links, external_links, html)"""
        if _is_skip_url(url):
            return None, set(), set(), ""
        fr = fetch_layered(url, allow_render=allow_render)
        if fr["method"] == "error":
            return ({"url": url, "status": 0, "title": "", "last_updated": "[爬取失敗]",
                     "depth": depth, "method": "error", "reason": fr["reason"]}, set(), set(), "")
        html = fr.get("html") or ""
        title = _page_title(html, url) if html else fr["text"][:40]
        # 日期抽取(有 html 用 TCGweb 抽取器)
        last = "[無日期]"
        if html:
            try:
                last = dates_mod.extract_last_updated(BeautifulSoup(html, "html.parser"), log_func=lambda m: None) or "[無日期]"
            except Exception:
                pass
        internal, external = extract_links(html, url) if html else (set(), set())
        rec = {"url": url, "status": 200, "title": title, "last_updated": last,
               "depth": depth, "method": fr["method"]}
        return rec, internal, external, html

    # ── 首頁 ──
    home_rec, home_int, home_ext, home_html = fetch_page(start_url, 0)
    visited.add(start_url)
    if home_rec:
        pages.append(home_rec); seen_titles.add(home_rec["title"])
    queue = deque()

    # ── sitemap 優先 ──
    smap = find_sitemap(home_html, start_url) if (use_sitemap and home_html) else None
    if smap and smap not in visited:
        sm_rec, _, sm_ext, sm_html = fetch_page(smap, 0)
        visited.add(smap)
        if sm_rec:
            pages.append(sm_rec)
        links = sitemap_links(sm_html, smap) if sm_html else set()
        home_ext |= sm_ext
        if links:
            for lk in links:
                if lk not in visited:
                    queue.append((lk, 1))
        else:
            for lk in home_int:
                if lk not in visited:
                    queue.append((lk, 1))
    else:
        for lk in home_int:
            if lk not in visited:
                queue.append((lk, 1))

    all_external = set(home_ext)

    # ── BFS ──
    while queue:
        url, depth = queue.popleft()
        if url in visited or depth > max_depth:
            continue
        if urllib.parse.urlparse(url).netloc != base_dom:
            continue
        visited.add(url)
        rec, internal, external, _ = fetch_page(url, depth)
        if rec is None:
            continue
        # 同標題+分頁 → 去重(不重複收錄,但分頁仍挖連結)
        if rec["title"] in seen_titles and _is_pagination(url):
            pass
        elif rec["title"] in seen_titles and rec["method"] != "error":
            # 同標題非分頁:視為重複頁,收錄但不再往下挖
            pages.append(rec)
            continue
        pages.append(rec)
        seen_titles.add(rec["title"])
        all_external |= external
        if depth < max_depth:
            for lk in internal:
                if lk not in visited:
                    queue.append((lk, depth + 1))

    # ── 外部連結狀態檢查(併發) ──
    ext_status = {}
    if check_external and all_external:
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
            for u, st in ex.map(check_link_status, all_external):
                ext_status[u] = st

    failed_pages = [p for p in pages if p["status"] == 0 or p["method"] == "error"]
    dead_ext = {u: s for u, s in ext_status.items() if s == 0 or s >= 400}
    rendered = [p for p in pages if p["method"] == "playwright"]
    return {
        "site": name, "url": start_url,
        "pages": pages, "external": ext_status,
        "stats": {"total_pages": len(pages), "failed_pages": len(failed_pages),
                  "rendered_pages": len(rendered), "total_external": len(ext_status),
                  "dead_external": len(dead_ext)},
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--name", default="")
    ap.add_argument("--depth", type=int, default=1)
    ap.add_argument("--no-external", action="store_true")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--no-sitemap", action="store_true")
    args = ap.parse_args()
    import time
    t0 = time.time()
    r = crawl_site(args.url, name=args.name, max_depth=args.depth,
                   check_external=not args.no_external, allow_render=not args.no_render,
                   use_sitemap=not args.no_sitemap)
    dt = time.time() - t0
    s = r["stats"]
    print(f"\n=== {r['site']} (depth={args.depth}) ===")
    print(f"頁面 {s['total_pages']}(失敗 {s['failed_pages']}、渲染 {s['rendered_pages']})"
          f" | 外連 {s['total_external']}(死連 {s['dead_external']}) | 耗時 {dt:.0f}s")
    print("\n前 15 頁:")
    for p in r["pages"][:15]:
        print(f"  d{p['depth']} {p['method']:9} {p['last_updated']:12} {p['title'][:34]}")
    if s["dead_external"]:
        print(f"\n死連(前 10):")
        for u, st in list({u: v for u, v in r["external"].items() if v == 0 or v >= 400}.items())[:10]:
            print(f"  [{st}] {u[:70]}")


if __name__ == "__main__":
    main()
