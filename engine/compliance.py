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
            hdrs = dict(r.headers)
            hdrs["_set_cookies"] = r.headers.get_all("Set-Cookie") or []
            hdrs["_hsts_all"] = r.headers.get_all("Strict-Transport-Security") or []
            hdrs["_final_url"] = r.geturl()
            return r.status, hdrs, body, None
    except urllib.error.HTTPError as e:
        hdrs = dict(e.headers or {})
        if e.headers:
            hdrs["_set_cookies"] = e.headers.get_all("Set-Cookie") or []
            hdrs["_hsts_all"] = e.headers.get_all("Strict-Transport-Security") or []
        hdrs["_final_url"] = e.geturl() if hasattr(e, "geturl") else url
        return e.code, hdrs, None, None
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


def check_tech_and_security(url, headers, html="", status=None):
    """被動式系統語言/框架指紋識別與 HTTP 標頭/元件資安檢視（零額外請求）。

    整合 10/22 全府 469 站實戰盤點規則：
      1. 精準識別 11 大類系統架構（PHP、ASP.NET CCMS/Core/MVC/WebForms、Java/HyWeb、
         Node.js Next/Nuxt/Express、校園 NSS、前端 SPA、ColdFusion、Classic ASP），
         並排除「ASP.NET 站因頁尾含外部 .php 友站連結被誤判為 PHP」之情形。
      2. 被動檢查 PHP EOL/版號外洩、CSP 殘留 FC2 或明文 http:// 來源、
         Session Cookie (HttpOnly/Secure) 缺失、Cookieless Session、
         CKEditor/CKFinder 元件引用與重複 HSTS 標頭。
    """
    hdr_low = {str(k).lower(): str(v) for k, v in (headers or {}).items()
               if not str(k).startswith("_")}
    set_cookies = (headers or {}).get("_set_cookies") or []
    if not set_cookies and "set-cookie" in hdr_low:
        set_cookies = [hdr_low["set-cookie"]]
    hsts_all = (headers or {}).get("_hsts_all") or []
    final_url = (headers or {}).get("_final_url") or url

    server_sw = hdr_low.get("server", "").strip()
    powered_by = hdr_low.get("x-powered-by", "").strip()
    aspnet_ver = hdr_low.get("x-aspnet-version", "").strip()
    aspnetmvc_ver = hdr_low.get("x-aspnetmvc-version", "").strip()
    csp = hdr_low.get("content-security-policy", "").strip()

    body = html or ""
    body_lower = body.lower()
    cookies_str = " ; ".join(set_cookies)
    cookies_lower = cookies_str.lower()
    url_combo = f"{url} {final_url}".lower()

    # Meta generator
    generator = ""
    gen_m = (re.search(r'<meta[^>]+name=["\']?generator["\']?[^>]+content=["\']([^"\']+)["\']', body, re.I)
             or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']?generator["\']?', body, re.I))
    if gen_m:
        generator = gen_m.group(1).strip()

    techs, frameworks, cms_platform, frontend = set(), set(), set(), set()

    # ── 1. PHP 識別（區分真實 PHP 特徵 vs 僅內文外部連結含 .php）──
    php_version = ""
    php_strong_clues = []
    php_link_only = False

    if "php" in powered_by.lower() or "php" in server_sw.lower():
        m = re.search(r"php/?([0-9.]+)", f"{powered_by} {server_sw}", re.I)
        if m:
            php_version = m.group(1)
        php_strong_clues.append(f"Header ({powered_by or server_sw})")

    if "phpsessid" in cookies_lower:
        php_strong_clues.append("Cookie: PHPSESSID")

    if re.search(r"\.php(\b|$|\?)", urllib.parse.urlsplit(url).path.lower()) or \
       re.search(r"\.php(\b|$|\?)", urllib.parse.urlsplit(final_url).path.lower()):
        php_strong_clues.append("URL .php 路由")

    # PHP 框架與 CMS
    if "laravel_session" in cookies_lower or ("xsrf-token" in cookies_lower and "eyj" in cookies_lower):
        frameworks.add("Laravel")
        php_strong_clues.append("Laravel Cookie")
    if "ci_session" in cookies_lower:
        frameworks.add("CodeIgniter")
        php_strong_clues.append("CodeIgniter Cookie")
    if "symfony" in cookies_lower or "sf_redirect" in cookies_lower:
        frameworks.add("Symfony")
        php_strong_clues.append("Symfony Cookie")
    if "_csrf-frontend" in cookies_lower or "_csrf-backend" in cookies_lower:
        frameworks.add("Yii")
        php_strong_clues.append("Yii Cookie")
    if "wp-content" in body_lower or "wp-includes" in body_lower or "wordpress" in generator.lower():
        cms_platform.add("WordPress")
        php_strong_clues.append("WordPress 特徵")
    if "drupal" in generator.lower() or "x-drupal" in str(hdr_low) or "sites/default/files" in body_lower:
        cms_platform.add("Drupal")
        php_strong_clues.append("Drupal 特徵")
    if "joomla" in generator.lower() or "/media/system/js/" in body_lower or "/templates/system/" in body_lower:
        cms_platform.add("Joomla!")
        php_strong_clues.append("Joomla! 特徵")
    if "xoops" in generator.lower():
        cms_platform.add("XOOPS")
        php_strong_clues.append("XOOPS 特徵")
    if "moodlesession" in cookies_lower or "moodle" in generator.lower():
        cms_platform.add("Moodle")
        php_strong_clues.append("Moodle 特徵")

    has_html_php_link = bool(re.search(r'href=["\'][^"\']*\.php(\?|["\'])', body_lower))

    # ── 2. ASP.NET / Classic ASP / ColdFusion 識別 ──
    is_coldfusion = (".cfm" in url_combo or "cfid" in cookies_lower or "cftoken" in cookies_lower)
    is_classic_asp = ((".asp" in url_combo and ".aspx" not in url_combo)
                      or "aspsessionid" in cookies_lower)
    is_ccms = any(k in body for k in ("NEWS_Content.aspx", "Default.aspx?n=", "cp.aspx?n=", "News.aspx?n=")) or \
              any(k in body_lower for k in ("ccms", "ccms_cs", "sys_root", "sms_"))
    is_aspnet_core = (".aspnetcore." in cookies_lower or "kestrel" in server_sw.lower())
    is_aspnet = (is_ccms or is_aspnet_core or "asp.net" in powered_by.lower()
                 or bool(aspnet_ver) or bool(aspnetmvc_ver)
                 or "asp.net_sessionid" in cookies_lower or ".aspx" in url_combo
                 or "__viewstate" in body_lower or "__requestverificationtoken" in cookies_lower
                 or "__requestverificationtoken" in body_lower)

    if is_aspnet:
        techs.add("ASP.NET")
        if is_ccms:
            cms_platform.add("北市府共通性網站平台 (CCMS)")
        if is_aspnet_core:
            frameworks.add("ASP.NET Core")
        elif aspnetmvc_ver or "__requestverificationtoken" in body_lower or "__requestverificationtoken" in cookies_lower:
            frameworks.add(f"ASP.NET MVC {aspnetmvc_ver}".strip())
        elif "__viewstate" in body_lower or ".aspx" in url_combo:
            frameworks.add("ASP.NET WebForms")

    # 若無強 PHP 證據且本身為 ASP.NET，僅內文含外部 .php 連結 → 排除 PHP 誤判
    is_php = bool(php_strong_clues) or (has_html_php_link and not is_aspnet)
    if has_html_php_link and is_aspnet and not php_strong_clues:
        php_link_only = True
    if is_php:
        techs.add(f"PHP {php_version}".strip())

    # ── 3. Java / Node.js / Python / 前端 SPA 識別 ──
    is_hyweb = ("/wsite/" in url_combo or "wsite/" in body_lower or "mp?mp=" in url_combo)
    is_java = (is_hyweb or "jsessionid" in cookies_lower or ".jsp" in url_combo
               or ".do" in url_combo or ".action" in url_combo
               or "servlet" in powered_by.lower() or "jsp" in powered_by.lower())
    if is_java:
        techs.add("Java / Jakarta EE")
        if is_hyweb:
            cms_platform.add("凌網 HyWeb wSite CMS")
        if ".action" in url_combo:
            frameworks.add("Apache Struts")
        if "x-application-context" in hdr_low:
            frameworks.add("Spring Framework")

    is_nextjs = ("next.js" in powered_by.lower() or "/_next/static/" in body_lower or "__next_data__" in body_lower)
    is_nuxtjs = ("/_nuxt/" in body_lower or "__nuxt__" in body_lower or "nuxt" in generator.lower())
    is_express = ("express" in powered_by.lower() or "connect.sid" in cookies_lower or ".sig=" in cookies_lower)
    if is_nextjs or is_nuxtjs or is_express:
        techs.add("Node.js")
        if is_nextjs:
            frameworks.add("Next.js (React SSR)")
        if is_nuxtjs:
            frameworks.add("Nuxt.js (Vue SSR)")
        if is_express:
            frameworks.add("Express/Node")

    is_nss = ("/nss/p/" in url_combo or "/nss/s/" in body_lower)
    if is_nss:
        cms_platform.add("校園模組化平台 (NSS)")

    if "ng-version" in body_lower or "<app-root" in body_lower or "ng-app" in body_lower:
        frontend.add("Angular")
    if "data-v-" in body_lower or "vue.min.js" in body_lower or "vue.js" in body_lower or is_nuxtjs or is_nss:
        frontend.add("Vue.js")
    if "data-reactroot" in body_lower or "react-dom" in body_lower or is_nextjs:
        frontend.add("React")
    if "jquery" in body_lower:
        frontend.add("jQuery")

    # ── 4. 綜合歸類主架構名稱 (arch_category / stack_summary) ──
    srv_label = server_sw or "(隱藏Server標頭)"
    if status == 403 and "cloudflare" in server_sw.lower():
        arch_category = "Cloudflare CDN / WAF 防護"
        stack_summary = f"Cloudflare WAF ({srv_label})"
    elif is_php:
        arch_category = "PHP 系統架構"
        sub = sorted(cms_platform | frameworks)
        php_lbl = f"PHP {php_version}".strip()
        if sub:
            php_lbl += f" ({', '.join(sub)})"
        stack_summary = f"{php_lbl} + {srv_label}"
    elif is_coldfusion:
        arch_category = "老舊高風險架構 (ColdFusion)"
        stack_summary = f"Adobe ColdFusion (.cfm) + {srv_label}"
    elif is_classic_asp:
        arch_category = "老舊高風險架構 (Classic ASP)"
        stack_summary = f"Microsoft Classic ASP (.asp) + {srv_label}"
    elif is_java:
        arch_category = "Java / Jakarta EE 架構"
        sub = sorted(cms_platform | frameworks) or ["Spring / Servlet / JSP"]
        stack_summary = f"Java ({', '.join(sub)}) + {srv_label}"
    elif is_nextjs or is_nuxtjs or is_express:
        arch_category = "Node.js 全端 SSR 架構"
        sub = sorted(frameworks) or ["Node.js"]
        stack_summary = f"Node.js ({', '.join(sub)}) + {srv_label}"
    elif is_nss:
        arch_category = "校園模組化平台 (NSS)"
        stack_summary = f"Vue.js SPA + {srv_label} (校園模組化平台 NSS)"
    elif is_ccms:
        arch_category = "ASP.NET 共通平台 (CCMS)"
        stack_summary = f"ASP.NET WebForms (北市府共通平台 CCMS) + {srv_label}"
    elif is_aspnet:
        arch_category = "ASP.NET 架構"
        sub = sorted(frameworks) or ["ASP.NET"]
        if "Angular" in frontend or "Vue.js" in frontend or "React" in frontend:
            fe_str = "/".join(sorted(frontend - {"jQuery"}))
            stack_summary = f"前端 {fe_str} + 後端 {', '.join(sub)} ({srv_label})"
        else:
            stack_summary = f"{', '.join(sub)} + {srv_label}"
    elif frontend - {"jQuery"}:
        fe_str = "/".join(sorted(frontend - {"jQuery"}))
        arch_category = "現代前端 SPA 架構"
        stack_summary = f"{fe_str} SPA + {srv_label}"
    else:
        arch_category = "靜態展示 / 其他系統"
        stack_summary = f"HTML5 / 靜態展示 + {srv_label}"

    # ── 5. 被動式資安標頭與元件問題檢視 ──
    issues = []

    # (a) PHP 版本外洩與 EOL 檢查
    if is_php and php_version:
        try:
            parts = [int(x) for x in php_version.split(".")[:2]]
            if parts[0] < 8 or (parts[0] == 8 and parts[1] <= 1):
                issues.append({
                    "level": "high",
                    "item": "PHP 核心版本過舊 (EOL)",
                    "detail": f"Header 暴露 PHP/{php_version}（已終止官方安全支援），建議升級至 PHP 8.2/8.3 以上並設 expose_php = Off",
                })
            else:
                issues.append({
                    "level": "warn",
                    "item": "PHP 版本號外洩 (expose_php)",
                    "detail": f"Header 暴露 PHP/{php_version}，建議於 php.ini 設定 expose_php = Off 隱藏版本資訊",
                })
        except Exception:
            issues.append({
                "level": "warn",
                "item": "PHP 版本號外洩",
                "detail": f"Header 暴露 PHP/{php_version}，建議設定 expose_php = Off",
            })
    elif is_php:
        issues.append({
            "level": "info",
            "item": "PHP 核心版本確認",
            "detail": "採用 PHP 架構（已隱藏對外版本號），遇重大 PHP CVE 通報時請由主機端確認是否已更新至最新修補版",
        })

    # (b) 老舊高風險非 PHP 技術
    if is_coldfusion:
        issues.append({
            "level": "high",
            "item": "老舊 ColdFusion 架構風險",
            "detail": "偵測為 Adobe ColdFusion (.cfm)，歷年存在多起重大 RCE 漏洞，建議列為優先重構汰換對象",
        })
    elif is_classic_asp:
        issues.append({
            "level": "high",
            "item": "老舊 Classic ASP 架構風險",
            "detail": "偵測為傳統 Classic ASP (.asp)，已終止主流發展且缺乏現代防護，建議升級改寫為 ASP.NET Core",
        })

    # (c) CSP (Content-Security-Policy) 標頭檢查（含 FC2 殘留與明文 http:// 放行）
    if csp:
        csp_low = csp.lower()
        if "fc2.com" in csp_low:
            issues.append({
                "level": "high",
                "item": "CSP 標頭殘留 FC2 外部白名單",
                "detail": "Content-Security-Policy 標頭殘留日本 fc2.com（如 counter1.fc2.com 計數器）白名單，易觸發資安聯防告警，請自伺服器標頭移除",
            })
        # 檢查 script-src 或 default-src 是否允許 http:// 明文來源
        for directive in re.findall(r"(script-src|default-src)\s+([^;]+)", csp_low):
            d_name, d_val = directive
            http_srcs = re.findall(r"http://[^\s;'\"]+", d_val)
            if http_srcs:
                issues.append({
                    "level": "high",
                    "item": f"CSP ({d_name}) 放行明文 HTTP 來源",
                    "detail": f"允許未加密之外部來源（{', '.join(http_srcs[:3])}），一旦流量遭劫持可植入惡意腳本，請移除或改為 https://",
                })

    # (d) Session Cookie 安全屬性與 Cookieless Session 檢查
    if re.search(r"/\(S\([a-z0-9]{16,}\)\)/", final_url, re.I):
        issues.append({
            "level": "warn",
            "item": "網址暴露 Cookieless Session ID",
            "detail": "網址路徑內嵌 (S(...)) Session 識別碼，分享網址易導致他人接管連線，建議改用安全 Cookie",
        })
    session_cookie_checked = False
    for sc in set_cookies:
        sc_l = sc.lower()
        if any(k in sc_l for k in ("phpsessid", "asp.net_sessionid", "jsessionid", "laravel_session", "ci_session")):
            session_cookie_checked = True
            c_name = sc.split("=", 1)[0].strip()
            missing = []
            if "httponly" not in sc_l:
                missing.append("HttpOnly")
            if "secure" not in sc_l and final_url.lower().startswith("https://"):
                missing.append("Secure")
            if missing:
                issues.append({
                    "level": "warn",
                    "item": f"Session Cookie ({c_name}) 缺安全屬性",
                    "detail": f"Cookie 未設定 {', '.join(missing)} 屬性，建議於伺服器或框架設定補齊以防範 XSS 竊取與明文傳輸",
                })

    # (e) 前端高風險元件 (CKEditor / CKFinder) 與未預期錯誤訊息外洩
    if "ckfinder" in body_lower or "ckeditor" in body_lower:
        issues.append({
            "level": "warn",
            "item": "引用 CKEditor / CKFinder 元件",
            "detail": "前端包含 CKEditor/CKFinder 引用，請確認後台檔案上傳路由具備嚴格 Session 驗證與副檔名白名單",
        })
    if any(err in body_lower for err in ("fatal error:", "notice: undefined", "uncaught exception", "syntax error, unexpected")):
        issues.append({
            "level": "high",
            "item": "頁面直接輸出程式錯誤訊息",
            "detail": "回應內容包含未捕捉之程式錯誤堆疊，請關閉正式環境 Debug / display_errors 顯示",
        })
    if "aspxerrorpath=" in final_url.lower():
        issues.append({
            "level": "warn",
            "item": "觸發 ASP.NET 未預期錯誤導轉 (aspxerrorpath)",
            "detail": f"首頁或目標路徑轉址至錯誤頁 ({final_url})，請檢查後端例外日誌",
        })

    # (f) 重複發送不同效期之 HSTS 標頭
    if len(set(h.strip() for h in hsts_all if h.strip())) > 1:
        issues.append({
            "level": "warn",
            "item": "重複發送多條 HSTS 標頭",
            "detail": f"伺服器同時回傳 {len(hsts_all)} 條 Strict-Transport-Security 標頭（{'; '.join(hsts_all[:2])}），建議整併保留一條",
        })

    return {
        "arch_category": arch_category,
        "stack_summary": stack_summary,
        "server": server_sw,
        "x_powered_by": powered_by,
        "is_php": is_php,
        "php_version": php_version,
        "php_link_only_false_positive": php_link_only,
        "techs": sorted(techs),
        "frameworks": sorted(frameworks),
        "cms_platform": sorted(cms_platform),
        "frontend": sorted(frontend),
        "session_cookie_checked": session_cookie_checked,
        "issues": issues,
    }


# ── 組合介面 ──

def run_basic(url):
    """基本合規檢查（466 站都做）：HTTPS/RWD/搜尋/無障礙/首頁連結/系統語言架構與資安標頭。
    回傳 dict，格式沿用 monthly_check 的站級結構。"""
    r = {}
    status, headers, body, err = _http_get(url)
    if err and "CERTIFICATE_VERIFY_FAILED" in str(err):
        r["cert_chain_warning"] = True
        status, headers, body, err = _http_get(url, verify=False)
    r["alive"] = (err is None and status is not None and status < 400)
    r["status"] = status if not err else err
    html = ""
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
    r["tech_security"] = check_tech_and_security(url, headers, html, status=status)
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
