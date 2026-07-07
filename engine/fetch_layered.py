# -*- coding: utf-8 -*-
"""靜態優先分層抓取(整併 stage 3 的核心)。

流程:靜態抓取 → 判斷是否 JS 空殼/frameset/內容過少 → 只有必要才升級 Playwright。
這把 TCGweb「每頁必渲」翻成「靜態優先、例外才渲染」,實測 466 站僅約 10% 需渲染。

重用:
- webcheck_ai.fetch_html / html_to_text / get_content  (靜態抓取、轉純文字、升級渲染)

已驗證的陷阱(見 memory content-fetch-method-decisions):
- wifi.taipei:靜態正常但 playwright 會撞 500 → NEVER_RENDER,不升級
- travel.taipei:Cloudflare 人機驗證,連 headless 都過不了 → FORCE_MANUAL
"""
import re
import ssl
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, r"D:\websitecheck")          # for config
sys.path.insert(0, r"D:\websitecheck\monthly")  # for webcheck_ai
import webcheck_ai

_CTX_NOVERIFY = ssl.create_default_context()
_CTX_NOVERIFY.check_hostname = False
_CTX_NOVERIFY.verify_mode = ssl.CERT_NONE


def render_to_html(url, timeout=30):
    """用 Playwright 渲染並回傳 raw HTML(BFS 抽連結需要 html,非純文字)。
    未裝 playwright 或失敗 → 回傳 (None, 說明)。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "Playwright未安裝"
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page()
            pg.goto(url, wait_until="networkidle", timeout=timeout * 1000)
            html = pg.content()
            b.close()
        return html, None
    except Exception as e:
        return None, f"渲染失敗({type(e).__name__})"


def _fetch_static(url):
    """靜態抓 HTML;憑證鏈不全(CERTIFICATE_VERIFY_FAILED)時退回不驗證重試。
    (webcheck_ai.fetch_html 用預設驗證,會誤判憑證不全的活站為連線失敗)"""
    try:
        return webcheck_ai.fetch_html(url)
    except Exception as e:
        if "CERTIFICATE_VERIFY_FAILED" not in str(e):
            raise
        req = urllib.request.Request(webcheck_ai._encode_url(url), headers={
            "User-Agent": webcheck_ai.UA, "Accept": "text/html,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9"})
        with urllib.request.urlopen(req, timeout=30, context=_CTX_NOVERIFY) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")

MIN_TEXT_CHARS = 300
SPA_MARKERS = [
    r'data-reactroot', r'id=["\']__next["\']', r'data-v-app', r'id=["\']__nuxt["\']',
    r'\bng-version\b', r'<app-root', r'\[ng-app\]',
]
SPA_RE = re.compile("|".join(SPA_MARKERS), re.I)
TEMPLATE_RE = re.compile(r"(\{\{[^}]{1,40}\}\}|\[\[[^\]]{1,40}\]\])")  # 未渲染 {{ }} / [[ ]]

NEVER_RENDER = {"wifi.taipei"}
FORCE_MANUAL = {"www.travel.taipei", "travel.taipei"}


def _host(url):
    return (urllib.parse.urlparse(url).hostname or "").lower()


def _result(url, method, escalated, need_render, reason, text, html=""):
    return {"url": url, "method": method, "escalated": escalated, "need_render": need_render,
            "reason": reason, "text": text or "", "chars": len(text or ""), "html": html}


def detect_shell(html, text):
    """靜態 HTML 是否為需渲染空殼。回傳 (need_render, reason)。"""
    if re.search(r"<frame\b", html, re.I):
        return True, "frameset(需跟進 frame src 或渲染)"
    tmpl = TEMPLATE_RE.findall(text or "")   # 未渲染模板是強訊號,不被字數蓋過
    if len(tmpl) >= 2:
        return True, f"未渲染模板語法×{len(tmpl)}(JS動態,如{tmpl[0][:16]})"
    m = SPA_RE.search(html)
    if m and len(text or "") < 1500:
        return True, f"SPA框架指紋({m.group(0)[:20]})且靜態內容少"
    if len((text or "").strip()) < MIN_TEXT_CHARS:
        return True, f"靜態可見文字僅{len((text or '').strip())}字"
    return False, ""


def fetch_layered(url, allow_render=True, force_method=None):
    """靜態優先抓取,必要時升級渲染。method ∈ static|playwright|manual|error"""
    host = _host(url)
    if force_method == "manual" or host in FORCE_MANUAL:
        return _result(url, "manual", False, False, "列為人工檢視(Cloudflare/登入牆/3D等)", "")

    try:
        html = _fetch_static(url)
    except Exception as e:
        return _result(url, "error", False, None, f"靜態抓取失敗({type(e).__name__})", "")
    text = webcheck_ai.html_to_text(html)
    need_render, reason = detect_shell(html, text)

    if not need_render:
        return _result(url, "static", False, False, "", text, html)

    if host in NEVER_RENDER:
        return _result(url, "static", False, True, f"{reason};但{host}升級渲染會撞錯誤頁,強制留靜態", text, html)
    if not allow_render:
        return _result(url, "static", False, True, f"{reason};(allow_render=False,標記待渲染)", text, html)

    # 升級 Playwright(本地渲染取 raw html;未裝會優雅退回靜態)
    rhtml, note = render_to_html(url)
    if rhtml:
        rtext = webcheck_ai.html_to_text(rhtml)
        if len(rtext) > len(text):
            return _result(url, "playwright", True, True, reason, rtext, rhtml)
    return _result(url, "static", False, True, f"{reason};升級渲染未增益({note or '無增益'})", text, html)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    tests = sys.argv[1:] or [
        "https://www.gov.taipei/", "https://ono.tp.edu.tw/",
        "https://canet.civil.taipei/tp104-1/", "https://wifi.taipei/",
    ]
    for u in tests:
        r = fetch_layered(u, allow_render=False)
        print(f"{r['method']:10} need_render={str(r['need_render']):5} chars={r['chars']:6} {r['reason'][:44]:46} {u}")
