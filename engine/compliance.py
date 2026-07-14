# -*- coding: utf-8 -*-
"""合規檢核模組：HTTPS/RWD/搜尋/無障礙/首頁連結/站內深爬/AI 判讀。

原在 monthly/monthly_check.py 的檢查邏輯搬到這裡，整合進深掃 worker。
基本合規（HTTPS/RWD/搜尋/無障礙）466 站都做（成本近零）；
AI 判讀 + deep_check 只做「合規檢核=是」的站。

由 full_overnight worker 呼叫，不直接執行。
"""
import concurrent.futures
import datetime
import html as html_mod
import os
import re
import socket
import ssl
import sys
import urllib.parse
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WebCheckBot/1.0"
HEADERS = {"User-Agent": UA, "Accept": "text/html,*/*;q=0.8",
           "Accept-Language": "zh-TW,zh;q=0.9"}
TIMEOUT = 25
MAX_LINKS_PER_SITE = 50
SKIP_DOMAINS = {"accessibility.moda.gov.tw"}
DEEP_MAX_PAGES = 150


# ── HTTP 工具 ──

def _normalize_url(u):
    u = html_mod.unescape(u).strip()
    parts = urllib.parse.urlsplit(u)
    netloc = parts.netloc
    if any(ord(c) > 127 for c in netloc):
        netloc = netloc.encode("idna").decode()
    safe = "/%:@+,;$!*'()~-._=&?"
    path = urllib.parse.quote(parts.path, safe=safe)
    query = urllib.parse.quote(parts.query, safe=safe).replace(" ", "%20")
    return urllib.parse.urlunsplit((parts.scheme, netloc, path, query, ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _http_get(url, follow=True, head=False, verify=True):
    try:
        url = _normalize_url(url)
    except Exception as e:
        return None, {}, None, f"URL格式錯誤: {e}"
    req = urllib.request.Request(url, headers=HEADERS,
                                method="HEAD" if head else "GET")
    handlers = []
    if not follow:
        handlers.append(_NoRedirect())
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


# ── 各檢查項目（搬自 monthly_check.py，邏輯不變）──

def check_cert(host):
    """TLS 憑證檢查：有效性、效期、SAN。"""
    out = {"valid": False, "expires": None, "days_left": None,
           "san": [], "error": None}
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as s:
                cert = s.getpeercert()
        out["valid"] = True
        exp = datetime.datetime.fromtimestamp(
            ssl.cert_time_to_seconds(cert["notAfter"]), datetime.timezone.utc)
        out["expires"] = exp.strftime("%Y-%m-%d")
        out["days_left"] = (exp - datetime.datetime.now(
            datetime.timezone.utc)).days
        out["san"] = [v for k, v in cert.get("subjectAltName", [])
                      if k == "DNS"]
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def check_https(url):
    """HTTPS：憑證 + HTTP 轉址 + HSTS。"""
    host = urllib.parse.urlparse(url).hostname
    res = {"cert": check_cert(host), "redirect_to_https": None, "hsts": None}
    status, headers, _, err = _http_get(f"http://{host}/", follow=False)
    if status in (301, 302, 307, 308):
        loc = headers.get("Location", "")
        res["redirect_to_https"] = loc.startswith("https://")
    elif err:
        res["redirect_to_https"] = None
    else:
        res["redirect_to_https"] = False
    status, headers, _, _ = _http_get(url, head=True)
    if headers:
        res["hsts"] = "Strict-Transport-Security" in headers
    return res


def _strip_comments(html):
    return re.sub(r"<!--.*?-->", " ", html, flags=re.S)


def _extract_links(html, base_url):
    links = []
    for m in re.finditer(r'<a[^>]*href=["\']([^"\']+)["\']',
                         _strip_comments(html), re.I):
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
    """逐一檢測連結，HEAD 失敗改 GET 重試。"""
    skipped = [u for u in links
               if urllib.parse.urlparse(u).hostname in SKIP_DOMAINS]
    targets = [u for u in links if u not in skipped]

    def one(u):
        status, _, _, err = _http_get(u, head=True)
        if err or (status and status >= 400):
            status, _, _, err = _http_get(u)
        if err:
            return (u, err)
        if status and status >= 400:
            return (u, f"HTTP {status}")
        return None

    broken = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        for r in ex.map(one, targets):
            if r:
                broken.append(r)
    return broken, skipped


def check_rwd(html, base_url):
    """RWD：viewport meta + 響應式 CSS。"""
    res = {"viewport": False, "responsive_css": False}
    res["viewport"] = bool(
        re.search(r'<meta[^>]*name=["\']viewport["\']', html, re.I))
    if re.search(r'<link[^>]*media=["\'][^"\']*(min|max)-width', html, re.I):
        res["responsive_css"] = True
    elif re.search(r"@media[^{]*(min|max)-width", html):
        res["responsive_css"] = True
    else:
        hrefs = re.findall(
            r'<link[^>]*rel=["\']stylesheet["\'][^>]*href=["\']([^"\']+)["\']',
            html, re.I)
        hrefs += re.findall(
            r'<link[^>]*href=["\']([^"\']+)["\'][^>]*rel=["\']stylesheet["\']',
            html, re.I)
        for href in hrefs[:6]:
            _, _, body, _ = _http_get(urllib.parse.urljoin(base_url, href))
            if body and re.search(rb"@media[^{]*(min|max)-width", body):
                res["responsive_css"] = True
                break
    return res


def check_search(html):
    """檢索功能偵測（啟發式）。"""
    h = _strip_comments(html)
    patterns = [
        r'<input[^>]*type=["\']search["\']',
        r'<input[^>]*name=["\'](q|qs|query|keyword|keywords|search\w*)["\']',
        r'<(input|button|a)[^>]*(placeholder|title|aria-label)=["\'][^"\']*(搜尋|檢索|查詢|Search)',
    ]
    return any(re.search(p, h, re.I) for p in patterns)


def check_accessibility(html):
    """無障礙標章偵測：有效 / 被註解。"""
    res = {"badge_active": False, "badge_in_comment": False, "detail": ""}
    no_comment = _strip_comments(html)
    pat = (r'(accessibility\.(moda|ncc)\.gov\.tw|無障礙標章'
           r'|accessibility[^"\']*Detail)')
    m = re.search(pat, no_comment, re.I)
    if m:
        res["badge_active"] = True
        alt = re.search(r'alt=["\']([^"\']*無障礙[^"\']*)["\']', no_comment)
        res["detail"] = alt.group(1) if alt else m.group(0)
    comments = " ".join(re.findall(r"<!--(.*?)-->", html, re.S))
    if re.search(pat, comments, re.I):
        res["badge_in_comment"] = True
    return res


# ── 組合介面 ──

def run_basic(url):
    """基本合規檢查（466 站都做）：HTTPS/RWD/搜尋/無障礙/首頁連結。
    回傳 dict，格式沿用 monthly_check 的站級結構。"""
    r = {}
    status, headers, body, err = _http_get(url)
    if err and "CERTIFICATE_VERIFY_FAILED" in str(err):
        r["cert_chain_warning"] = True
        status, headers, body, err = _http_get(url, verify=False)
    r["alive"] = (err is None and status is not None and status < 400)
    r["status"] = status if not err else err
    if body:
        try:
            html = body.decode("utf-8", errors="replace")
        except Exception:
            html = body.decode("big5", errors="replace")
        title = re.search(r"<title[^>]*>([^<]*)", html, re.I)
        r["title"] = title.group(1).strip() if title else ""
        links = _extract_links(html, url)
        r["links_total"] = len(links)
        checked = links[:MAX_LINKS_PER_SITE]
        r["links_checked"] = len(checked)
        r["links_broken"], r["links_skipped"] = check_links(checked)
        r["rwd"] = check_rwd(html, url)
        r["search"] = check_search(html)
        r["accessibility"] = check_accessibility(html)
    r["https"] = check_https(url)
    return r


def run_deep(url):
    """站內深度檢測（合規檢核=是 的站才做）：內部失效連結 + Office 缺 PDF/ODF。"""
    sys.path.insert(0, os.path.join(_ROOT, "monthly"))
    import deep_check
    try:
        d = deep_check.deep_scan(url, DEEP_MAX_PAGES)
        return {"pages": d["pages_crawled"],
                "broken_internal": d["broken_internal"],
                "attachments": d["attachments_total"],
                "office_no_universal": d["office_no_universal"]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def run_ai_checks(ai_checks):
    """AI 內容判讀（合規檢核=是 且有 AI判讀題目 的站）。串行呼叫，不併發。"""
    sys.path.insert(0, os.path.join(_ROOT, "monthly"))
    import webcheck_ai
    results = []
    for chk in ai_checks:
        try:
            html = webcheck_ai.fetch_html(chk["url"])
            text = webcheck_ai.html_to_text(html)
            ans = webcheck_ai.ask_ai(chk["question"], text, chk["url"])
        except Exception as e:
            ans = f"(AI判讀失敗: {e})"
        results.append({"url": chk["url"], "question": chk["question"],
                         "answer": ans})
    return results
