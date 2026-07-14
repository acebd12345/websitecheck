# -*- coding: utf-8 -*-
"""
站內深度檢測:
  1. 內部頁面失效 — BFS 爬站內頁, 記錄 4xx/5xx 與連不上的內部連結(含出現位置)
  2. 下載文件通用格式 — 收集附件連結, 檢查 Office 檔(doc/docx/xls/xlsx/ppt/pptx)
     是否在同一頁提供 PDF 或 ODF(odt/ods/odp) 替代版本(檢核表(一)要求)

被 engine/compliance.py 引用; 也可單獨執行:
  python deep_check.py https://網址 [最大頁數]
(外部連結的失效/被搶註/可疑內容由 link_audit 每日排程負責, 本模組只看站內)
"""
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup

requests.packages.urllib3.disable_warnings()

TIMEOUT = 15
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WebCheckBot/1.0",
           "Accept": "text/html,*/*;q=0.8", "Accept-Language": "zh-TW,zh;q=0.9"}

OFFICE_EXT = (".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")
UNIVERSAL_EXT = (".pdf", ".odt", ".ods", ".odp")
ATTACH_EXT = OFFICE_EXT + UNIVERSAL_EXT + (".csv", ".zip", ".rar", ".7z")


def _stem(url):
    """附件路徑去副檔名, 供同名不同格式配對 (xxx.docx ↔ xxx.pdf/xxx.odt)"""
    path = urllib.parse.urlsplit(url).path
    return path.rsplit(".", 1)[0].lower()


def deep_scan(start_url, max_pages=200):
    """回傳 {pages_crawled, broken_internal:[(url, status, found_on)],
             attachments_total, office_no_universal:[(office_url, found_on)]}"""
    start_host = urllib.parse.urlsplit(start_url).hostname
    sess = requests.Session()
    sess.headers.update(HEADERS)
    seen = {start_url}
    frontier = [start_url]
    pages_done = 0
    broken = []            # (url, status, found_on)
    page_attach = {}       # page_url -> [attachment urls]

    def fetch(page):
        try:
            r = sess.get(page, timeout=TIMEOUT, verify=True)  # 預設驗證憑證
            return page, r
        except requests.exceptions.SSLError:
            try:  # 憑證鏈不完整(gov.taipei 常缺中繼憑證) → 退回不驗證仍續抓內容
                return page, sess.get(page, timeout=TIMEOUT, verify=False)
            except Exception as e:
                return page, e
        except Exception as e:
            return page, e

    referrer = {}  # url -> 首次發現它的頁面
    with ThreadPoolExecutor(max_workers=8) as ex:
        while frontier and pages_done < max_pages:
            batch = frontier[: min(30, max_pages - pages_done)]
            frontier = frontier[len(batch):]
            nxt = []
            for page, r in ex.map(fetch, batch):
                pages_done += 1
                src = referrer.get(page, "(起始頁)")
                if isinstance(r, Exception):
                    broken.append((page, type(r).__name__, src))
                    continue
                if r.status_code >= 400:
                    broken.append((page, f"HTTP {r.status_code}", src))
                    continue
                if "html" not in r.headers.get("Content-Type", ""):
                    continue
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    absu = urllib.parse.urldefrag(
                        urllib.parse.urljoin(r.url, a["href"].strip()))[0]
                    if not absu.lower().startswith(("http://", "https://")):
                        continue
                    host = urllib.parse.urlsplit(absu).hostname
                    low = urllib.parse.urlsplit(absu).path.lower()
                    if low.endswith(ATTACH_EXT):
                        page_attach.setdefault(r.url, []).append(absu)
                        continue
                    if host == start_host and absu not in seen:
                        seen.add(absu)
                        referrer[absu] = r.url
                        nxt.append(absu)
            frontier.extend(nxt)

    # Office 檔配對檢查: 同一頁有同名(或任一) PDF/ODF 才算有提供通用格式
    office_no_universal = []
    for page, atts in page_attach.items():
        stems_universal = {_stem(u) for u in atts
                           if urllib.parse.urlsplit(u).path.lower().endswith(UNIVERSAL_EXT)}
        for u in atts:
            if urllib.parse.urlsplit(u).path.lower().endswith(OFFICE_EXT):
                if _stem(u) not in stems_universal:
                    office_no_universal.append((u, page))

    return {
        "pages_crawled": pages_done,
        "broken_internal": broken,
        "attachments_total": sum(len(v) for v in page_attach.values()),
        "office_no_universal": office_no_universal,
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        print(__doc__)
        return
    url = sys.argv[1]
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    res = deep_scan(url, max_pages)
    print(f"已爬 {res['pages_crawled']} 頁, 附件 {res['attachments_total']} 個")
    print(f"站內失效頁面 {len(res['broken_internal'])} 筆:")
    for u, st, src in res["broken_internal"]:
        print(f"  ❌ {u} → {st}（出現於 {src}）")
    print(f"Office 檔未提供 PDF/ODF 替代 {len(res['office_no_universal'])} 筆:")
    for u, page in res["office_no_universal"]:
        print(f"  📎 {u}（頁面 {page}）")


if __name__ == "__main__":
    main()
