# -*- coding: utf-8 -*-
"""
臺北市政府資訊局 網站每月檢核 自動化檢測

用法:
  python monthly_check.py              檢測 sites.json 全部網站 (含AI判讀)
  python monthly_check.py --no-ai      只跑硬檢測, 不呼叫地端AI
  python monthly_check.py --site 03    只檢測 sheet 名稱包含 "03" 的網站
  python monthly_check.py --month 11506  指定報告月份標籤 (預設自動算上個月)

檢測項目 (對應檢核表):
  (一) 超連結有效性 — 首頁所有連結逐一檢測
  (二) 檢索功能     — 偵測頁面是否有搜尋框
  (三) HTTPS        — 憑證有效性/效期/網域、HTTP轉HTTPS、HSTS
  (四) RWD          — viewport meta、響應式CSS
  無障礙           — 標章圖示/連結偵測(含被註解掉的偵測)
  AI內容判讀       — sites.json 中 ai_checks 設定的題目 (使用地端AI)

輸出:
  reports/report_<月份>.md    人工閱讀用月報
  reports/result_<月份>.json  機器可讀完整結果
"""
import concurrent.futures
import datetime
import html as html_mod
import json
import os
import re
import socket
import ssl
import sys
import urllib.parse
import urllib.request

import webcheck_ai  # 同目錄的 AI 判讀工具 (fetch_html / html_to_text / ask_ai)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WebCheckBot/1.0"
HEADERS = {"User-Agent": UA, "Accept": "text/html,*/*;q=0.8", "Accept-Language": "zh-TW,zh;q=0.9"}
TIMEOUT = 25
MAX_LINKS_PER_SITE = 50
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 有防機器人保護、無法自動檢測的網域 → 列為「需人工確認」而非失效
SKIP_DOMAINS = {"accessibility.moda.gov.tw"}
# link_audit 全站連結稽核工具的資料夾(另一套每日排程的深度稽核, 結果併入月檢核)
import config
LINK_AUDIT_DIR = config.LINK_AUDIT_DIR


def read_link_audit(url):
    """讀取 link_audit 對該站最近一次的全站稽核結果。
    回傳 {scanned, scan_date, problems:[{risk,url,note}], counts:{risk:n}} 或 None(從未掃描)"""
    import csv as _csv
    import glob as _glob
    host = urllib.parse.urlparse(url).hostname or ""
    tag = host.replace(".", "_")
    jsonl = os.path.join(LINK_AUDIT_DIR, f"links_{tag}.jsonl")
    csvs = sorted(_glob.glob(os.path.join(LINK_AUDIT_DIR, f"problems_{tag}_*.csv")))
    if not os.path.exists(jsonl) and not csvs:
        return None
    out = {"scanned": True, "scan_date": None, "problems": [], "counts": {}}
    if os.path.exists(jsonl):
        out["scan_date"] = datetime.date.fromtimestamp(os.path.getmtime(jsonl)).isoformat()
    if csvs:
        latest = csvs[-1]
        csv_date = latest.rsplit("_", 1)[-1].replace(".csv", "")
        # problems CSV 比 jsonl 舊 → 之後的掃描無異常, 不採用舊問題清單
        if out["scan_date"] is None or csv_date >= out["scan_date"]:
            out["scan_date"] = csv_date
            with open(latest, encoding="utf-8-sig") as f:
                for row in _csv.DictReader(f):
                    risk = row.get("risk", "")
                    out["counts"][risk] = out["counts"].get(risk, 0) + 1
                    out["problems"].append({"risk": risk, "url": row.get("url", ""),
                                            "note": row.get("note", "")})
    return out


def normalize_url(u):
    """解碼HTML實體、處理中文與空白字元, 讓 urllib 能正確送出請求"""
    u = html_mod.unescape(u).strip()
    parts = urllib.parse.urlsplit(u)
    netloc = parts.netloc
    if any(ord(c) > 127 for c in netloc):
        netloc = netloc.encode("idna").decode()
    safe = "/%:@+,;$!*'()~-._=&?"
    path = urllib.parse.quote(parts.path, safe=safe)
    query = urllib.parse.quote(parts.query, safe=safe).replace(" ", "%20")
    return urllib.parse.urlunsplit((parts.scheme, netloc, path, query, ""))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http_get(url, follow=True, head=False, verify=True):
    """回傳 (status_code, headers, body_bytes或None, error字串或None)"""
    try:
        url = normalize_url(url)
    except Exception as e:
        return None, {}, None, f"URL格式錯誤: {e}"
    req = urllib.request.Request(url, headers=HEADERS, method="HEAD" if head else "GET")
    handlers = []
    if not follow:
        handlers.append(NoRedirect())
    if not verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            body = None if head else r.read()
            return r.status, dict(r.headers), body, None
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), None, None
    except Exception as e:
        return None, {}, None, f"{type(e).__name__}: {e}"


def check_cert(host):
    """TLS 憑證檢查: 有效性(能否完成驗證握手)、效期、SAN"""
    out = {"valid": False, "expires": None, "days_left": None, "san": [], "error": None}
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as s:
                cert = s.getpeercert()
        out["valid"] = True  # 預設驗證含網域比對, 握手成功即有效
        exp = datetime.datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), datetime.timezone.utc)
        out["expires"] = exp.strftime("%Y-%m-%d")
        out["days_left"] = (exp - datetime.datetime.now(datetime.timezone.utc)).days
        out["san"] = [v for k, v in cert.get("subjectAltName", []) if k == "DNS"]
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def check_https(url):
    """(三) HTTPS: 憑證 + HTTP轉址 + HSTS"""
    host = urllib.parse.urlparse(url).hostname
    res = {"cert": check_cert(host), "redirect_to_https": None, "hsts": None}
    status, headers, _, err = http_get(f"http://{host}/", follow=False)  # 用GET, 部分WAF擋HEAD
    if status in (301, 302, 307, 308):
        loc = headers.get("Location", "")
        res["redirect_to_https"] = loc.startswith("https://")
    elif err:
        res["redirect_to_https"] = None  # 80埠未開, 無法測 (不算錯)
    else:
        res["redirect_to_https"] = False
    status, headers, _, _ = http_get(url, head=True)
    if headers:
        res["hsts"] = "Strict-Transport-Security" in headers
    return res


def strip_comments(html):
    return re.sub(r"<!--.*?-->", " ", html, flags=re.S)


def extract_links(html, base_url):
    links = []
    for m in re.finditer(r'<a[^>]*href=["\']([^"\']+)["\']', strip_comments(html), re.I):
        href = m.group(1).strip()
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        links.append(urllib.parse.urljoin(base_url, href))
    seen, out = set(), []
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def check_links(links):
    """逐一檢測連結, 回傳 (失效清單, 略過清單)。
    HEAD 失敗一律改 GET 重試 (gov.taipei 的 CMS/WAF 會擋 HEAD)。"""
    skipped = [u for u in links if urllib.parse.urlparse(u).hostname in SKIP_DOMAINS]
    targets = [u for u in links if u not in skipped]

    def one(u):
        status, _, _, err = http_get(u, head=True)
        if err or (status and status >= 400):
            status, _, _, err = http_get(u)  # GET 重試
        if err:
            return (u, err)
        if status >= 400:
            return (u, f"HTTP {status}")
        return None
    broken = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        for r in ex.map(one, targets):
            if r:
                broken.append(r)
    return broken, skipped


def check_rwd(html, base_url):
    """(四) RWD: viewport meta + 響應式 CSS"""
    res = {"viewport": False, "responsive_css": False}
    res["viewport"] = bool(re.search(r'<meta[^>]*name=["\']viewport["\']', html, re.I))
    # link 標籤本身帶寬度條件的 media 屬性
    if re.search(r'<link[^>]*media=["\'][^"\']*(min|max)-width', html, re.I):
        res["responsive_css"] = True
    elif re.search(r"@media[^{]*(min|max)-width", html):  # 行內 style
        res["responsive_css"] = True
    else:
        # 抓前幾支外部 CSS 找 @media
        hrefs = re.findall(r'<link[^>]*rel=["\']stylesheet["\'][^>]*href=["\']([^"\']+)["\']', html, re.I)
        hrefs += re.findall(r'<link[^>]*href=["\']([^"\']+)["\'][^>]*rel=["\']stylesheet["\']', html, re.I)
        for href in hrefs[:6]:
            _, _, body, _ = http_get(urllib.parse.urljoin(base_url, href))
            if body and re.search(rb"@media[^{]*(min|max)-width", body):
                res["responsive_css"] = True
                break
    return res


def check_search(html):
    """(二) 檢索功能偵測 (啟發式)"""
    h = strip_comments(html)
    patterns = [
        r'<input[^>]*type=["\']search["\']',
        r'<input[^>]*name=["\'](q|qs|query|keyword|keywords|search\w*)["\']',
        r'<(input|button|a)[^>]*(placeholder|title|aria-label)=["\'][^"\']*(搜尋|檢索|查詢|Search)',
    ]
    return any(re.search(p, h, re.I) for p in patterns)


def check_accessibility(html):
    """無障礙標章偵測: 有效的 / 被註解掉的"""
    res = {"badge_active": False, "badge_in_comment": False, "detail": ""}
    no_comment = strip_comments(html)
    pat = r'(accessibility\.(moda|ncc)\.gov\.tw|無障礙標章|accessibility[^"\']*Detail)'
    m = re.search(pat, no_comment, re.I)
    if m:
        res["badge_active"] = True
        alt = re.search(r'alt=["\']([^"\']*無障礙[^"\']*)["\']', no_comment)
        res["detail"] = alt.group(1) if alt else m.group(0)
    comments = " ".join(re.findall(r"<!--(.*?)-->", html, re.S))
    if re.search(pat, comments, re.I):
        res["badge_in_comment"] = True
    return res


DEEP_MAX_PAGES = 150  # 每站站內深度爬檢頁數上限(站內404+附件格式)


def check_site(site, use_ai=True, use_deep=True):
    """對單一網站執行全部檢測"""
    result = {"sheet": site["sheet"], "name": site["name"], "urls": {}, "ai": []}
    for url in site["urls"]:
        r = {}
        status, headers, body, err = http_get(url)
        if err and "CERTIFICATE_VERIFY_FAILED" in err:
            # 憑證鏈不完整等問題: 改用不驗證模式取得內容, 憑證問題仍會在(三)回報
            r["cert_chain_warning"] = True
            status, headers, body, err = http_get(url, verify=False)
        r["alive"] = (err is None and status and status < 400)
        r["status"] = status if not err else err
        if body:
            try:
                html = body.decode("utf-8", errors="replace")
            except Exception:
                html = body.decode("big5", errors="replace")
            title = re.search(r"<title[^>]*>([^<]*)", html, re.I)
            r["title"] = title.group(1).strip() if title else ""
            links = extract_links(html, url)
            r["links_total"] = len(links)
            checked = links[:MAX_LINKS_PER_SITE]
            r["links_checked"] = len(checked)
            r["links_broken"], r["links_skipped"] = check_links(checked)
            r["rwd"] = check_rwd(html, url)
            r["search"] = check_search(html)
            r["accessibility"] = check_accessibility(html)
        r["https"] = check_https(url)
        r["link_audit"] = read_link_audit(url)
        if use_deep and r.get("alive"):
            try:
                import deep_check
                d = deep_check.deep_scan(url, DEEP_MAX_PAGES)
                r["deep"] = {"pages": d["pages_crawled"],
                             "broken_internal": d["broken_internal"],
                             "attachments": d["attachments_total"],
                             "office_no_universal": d["office_no_universal"]}
            except Exception as e:
                r["deep"] = {"error": f"{type(e).__name__}: {e}"}
        result["urls"][url] = r

    if use_ai:
        for chk in site.get("ai_checks", []):
            try:
                html = webcheck_ai.fetch_html(chk["url"])
                text = webcheck_ai.html_to_text(html)
                ans = webcheck_ai.ask_ai(chk["question"], text, chk["url"])
            except Exception as e:
                ans = f"(AI判讀失敗: {e})"
            result["ai"].append({"url": chk["url"], "question": chk["question"], "answer": ans})
    return result


def roc_prev_month():
    """回傳上個月的民國年月標籤, 如 11505"""
    today = datetime.date.today()
    first = today.replace(day=1)
    prev = first - datetime.timedelta(days=1)
    return f"{prev.year - 1911}{prev.month:02d}"


def fmt_bool(v, ok="○", bad="✕", unknown="—"):
    if v is True:
        return ok
    if v is False:
        return bad
    return unknown


def write_report(results, month):
    os.makedirs(config.REPORTS_DIR, exist_ok=True)
    jpath = os.path.join(config.REPORTS_DIR, f"result_{month}.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    lines = [f"# {month} 網站檢核自動檢測報告", "",
             f"檢測時間：{datetime.datetime.now():%Y-%m-%d %H:%M}", "",
             "## 總覽", "",
             "| 網站 | 連線 | HTTPS憑證 | 轉HTTPS | HSTS | RWD | 檢索 | 失效連結 | 無障礙標章 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for res in results:
        for url, r in res["urls"].items():
            https = r.get("https", {})
            cert = https.get("cert", {})
            rwd = r.get("rwd", {})
            acc = r.get("accessibility", {})
            rwd_ok = rwd.get("viewport") and rwd.get("responsive_css") if rwd else None
            broken = len(r.get("links_broken", [])) if "links_broken" in r else "—"
            badge = "○" if acc.get("badge_active") else ("註解中" if acc.get("badge_in_comment") else "✕")
            lines.append(
                f"| {res['name'][:14]}<br>`{url}` | {fmt_bool(r.get('alive'))} "
                f"| {fmt_bool(cert.get('valid'))} ({cert.get('expires','?')}) "
                f"| {fmt_bool(https.get('redirect_to_https'))} | {fmt_bool(https.get('hsts'))} "
                f"| {fmt_bool(rwd_ok)} | {fmt_bool(r.get('search'))} | {broken} | {badge} |")
    lines.append("")

    for res in results:
        lines += [f"## {res['sheet']}：{res['name']}", ""]
        for url, r in res["urls"].items():
            https = r.get("https", {})
            cert = https.get("cert", {})
            lines += [f"### {url}", "",
                      f"- 連線狀態：{r.get('status')}　標題：{r.get('title','')}"]
            if cert.get("valid"):
                lines.append(f"- (三)HTTPS：○ 憑證有效至 {cert['expires']}（剩 {cert['days_left']} 天）"
                             f"｜HTTP轉址 {fmt_bool(https.get('redirect_to_https'))}"
                             f"｜HSTS {fmt_bool(https.get('hsts'))}")
            elif "unable to get local issuer" in str(cert.get("error", "")):
                lines.append("- (三)HTTPS：⚠ 伺服器未送出完整憑證鏈（缺中繼憑證）。"
                             "瀏覽器通常仍可開啟，但部分程式/APP會連線失敗，建議通知維運廠商修正")
            else:
                lines.append(f"- (三)HTTPS：✕ 憑證問題 → {cert.get('error')}")
            if "rwd" in r:
                rwd = r["rwd"]
                ok = rwd["viewport"] and rwd["responsive_css"]
                lines.append(f"- (四)RWD：{fmt_bool(ok)}"
                             f"（viewport {fmt_bool(rwd['viewport'])}／響應式CSS {fmt_bool(rwd['responsive_css'])}）")
                lines.append(f"- (二)檢索功能：{fmt_bool(r.get('search'))}（啟發式偵測，✕ 時請人工確認）")
                acc = r.get("accessibility", {})
                if acc.get("badge_active"):
                    lines.append(f"- 無障礙標章：偵測到（{acc.get('detail','')}）")
                elif acc.get("badge_in_comment"):
                    lines.append("- 無障礙標章：⚠ 標章程式碼存在但被註解隱藏")
                else:
                    lines.append("- 無障礙標章：未偵測到")
                broken = r.get("links_broken", [])
                if r.get("links_total", 0) == 0:
                    lines.append("- (一)超連結：頁面連結為JavaScript動態載入，無法靜態檢測，請人工抽查")
                else:
                    lines.append(f"- (一)超連結：檢測 {r.get('links_checked',0)}/{r.get('links_total',0)} 條"
                                 f"，失效 {len(broken)} 條")
                for u, why in broken:
                    lines.append(f"    - ❌ {u} → {why}")
                for u in r.get("links_skipped", []):
                    lines.append(f"    - ⏭ {u} （防機器人保護，需人工確認）")
                deep = r.get("deep")
                if deep:
                    if "error" in deep:
                        lines.append(f"- 站內深度檢測：失敗（{deep['error'][:80]}）")
                    else:
                        lines.append(f"- 站內深度檢測：爬 {deep['pages']} 頁"
                                     f"，內部失效 {len(deep['broken_internal'])} 筆"
                                     f"，附件 {deep['attachments']} 個"
                                     f"，Office檔缺PDF/ODF替代 {len(deep['office_no_universal'])} 筆")
                        for u, st, src in deep["broken_internal"][:10]:
                            low = u.lower()
                            hint = ""
                            if "mailto:" in low or "tel:" in low:
                                hint = "（疑似 email/電話連結誤寫成相對路徑，非缺頁，請網站方修正 href）"
                            lines.append(f"    - ❌ {u} → {st}{hint}（出現於 {src}）")
                        for u, page in deep["office_no_universal"][:10]:
                            lines.append(f"    - 📎 {u}（頁面 {page}）")
                la = r.get("link_audit")
                if la is None:
                    lines.append("- 全站連結稽核（link_audit）：尚未掃描過此站，請確認每日排程涵蓋")
                else:
                    cnt = "、".join(f"{k} {v} 筆" for k, v in la["counts"].items()) or "無異常"
                    lines.append(f"- 全站連結稽核（link_audit，{la['scan_date']} 掃描）：{cnt}")
                    for p in la["problems"][:10]:
                        lines.append(f"    - [{p['risk']}] {p['url']} → {p['note']}")
                    if len(la["problems"]) > 10:
                        lines.append(f"    - …其餘 {len(la['problems'])-10} 筆見 link_audit 的 CSV")
            lines.append("")
        for ai in res.get("ai", []):
            lines += [f"**AI判讀** `{ai['url']}`", f"- 問：{ai['question']}",
                      "- 答：" + ai["answer"].replace("\n", "\n  "), ""]

    mpath = os.path.join(config.REPORTS_DIR, f"report_{month}.md")
    with open(mpath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return mpath, jpath


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    use_ai = "--no-ai" not in args
    use_deep = "--no-deep" not in args
    month = roc_prev_month()
    if "--month" in args:
        month = args[args.index("--month") + 1]
    site_filter = None
    if "--site" in args:
        site_filter = args[args.index("--site") + 1]

    with open(config.SITES_JSON, encoding="utf-8") as f:
        sites = json.load(f)["sites"]
    if site_filter:
        keys = [k.strip() for k in site_filter.split(",") if k.strip()]
        sites = [s for s in sites if any(k in s["sheet"] for k in keys)]
    print(f"共 {len(sites)} 個網站，AI判讀：{'開' if use_ai else '關'}，月份標籤:{month}")

    results = []
    for i, site in enumerate(sites, 1):
        print(f"[{i}/{len(sites)}] {site['sheet']} ...", flush=True)
        try:
            results.append(check_site(site, use_ai=use_ai, use_deep=use_deep))
        except Exception as e:
            print(f"  !! 檢測失敗: {e}")
            results.append({"sheet": site["sheet"], "name": site["name"],
                            "urls": {}, "ai": [], "error": str(e)})

    mpath, jpath = write_report(results, month)
    print(f"\n完成。報告：{mpath}\nJSON：{jpath}")


if __name__ == "__main__":
    main()
