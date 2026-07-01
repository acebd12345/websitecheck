# -*- coding: utf-8 -*-
"""
網站檢核 AI 輔助判讀工具

用法:
  python webcheck_ai.py <網址> "<要問AI的問題>"        抓網頁並請地端AI判讀
  python webcheck_ai.py <網址> --dump                  只顯示抓到的純文字(不呼叫AI)

範例:
  python webcheck_ai.py https://ivoting.taipei/news.html "這頁有哪些消息?最新一筆日期是?"
  python webcheck_ai.py https://ivoting.taipei/contact-us.html "頁面上有哪些聯絡資訊?"

地端 AI 設定(擇一):
  1. 直接改下方 DEFAULT_* 常數
  2. 設環境變數 AI_BASE_URL / AI_MODEL / AI_API_KEY
     例: set AI_BASE_URL=http://localhost:11434/v1   (Ollama)
         set AI_MODEL=qwen2.5:14b
  支援所有 OpenAI 相容 API (Ollama / LM Studio / vLLM / Open WebUI ...)
"""
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request

# ===== AI 設定（來自 config.json，見 config.example.json）=====
import config

PROVIDER = (config.get("ai_provider", "openai") or "openai").lower()  # openai | anthropic | gemini
BASE_URL = (config.AI_BASE_URL or "").rstrip("/")
MODEL = config.AI_MODEL
API_KEY = config.AI_API_KEY

# Google Gemini 提供 OpenAI 相容端點, 沿用同一套 HTTP 路徑
GEMINI_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"


def _effective_base():
    if PROVIDER == "gemini":
        return BASE_URL or GEMINI_OPENAI_BASE  # 未設定就用 Gemini 預設端點
    return BASE_URL

MAX_CONTENT_CHARS = 30000  # 網頁文字超過此長度會截斷 (main_model context 128k, 此值很保守)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WebCheckBot/1.0"


def _encode_url(url: str) -> str:
    """處理含中文/空白的網址，避免 ascii 編碼錯誤"""
    parts = urllib.parse.urlsplit(url)
    safe = "/%:@+,;$!*'()~-._=&?"
    path = urllib.parse.quote(parts.path, safe=safe)
    query = urllib.parse.quote(parts.query, safe=safe)
    netloc = parts.netloc.encode("idna").decode() if any(ord(c) > 127 for c in parts.netloc) else parts.netloc
    return urllib.parse.urlunsplit((parts.scheme, netloc, path, query, ""))


def fetch_html(url: str) -> str:
    """抓取網頁原始 HTML。"""
    url = _encode_url(url)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,*/*;q=0.8",       # 部分網站(如wifi.taipei)缺Accept會回500
        "Accept-Language": "zh-TW,zh;q=0.9",
    })
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


EMPTY_SIGNALS = ["查無", "共 0 項", "共0項", "無任何", "無資料", "沒有資料", "no data", "0 項"]


def get_content(url: str, method: str = "ai"):
    """依分層方式取得網頁內容文字。
    回傳 (純文字, 實際使用的方式, 提示訊息或None)。
    code/ai → 靜態抓取; playwright → 嘗試無頭瀏覽器渲染(未安裝則退回靜態並提示);
    manual → 不抓，回傳 None 文字。"""
    if method == "manual":
        return None, "manual", "此站列為人工檢視（自動化無法判讀，如3D地圖/登入牆）"
    if method == "playwright":
        try:
            from playwright.sync_api import sync_playwright  # 安裝後才可用
            with sync_playwright() as p:
                b = p.chromium.launch()
                pg = b.new_page()
                pg.goto(url, wait_until="networkidle", timeout=30000)
                html = pg.content()
                b.close()
            return html_to_text(html), "playwright", None
        except ImportError:
            return html_to_text(fetch_html(url)), "playwright", "Playwright未安裝，暫以靜態判讀（內容可能不全）"
        except Exception as e:
            return html_to_text(fetch_html(url)), "playwright", f"Playwright渲染失敗({type(e).__name__})，暫以靜態判讀"
    return html_to_text(fetch_html(url)), method, None


def looks_empty(ai_answer: str) -> bool:
    """AI 判讀結果是否顯示「抓不到內容」→ 建議升級 playwright"""
    a = (ai_answer or "").lower()
    return any(s.lower() in a for s in EMPTY_SIGNALS)


def html_to_text(html: str) -> str:
    """去除 script/style/註解與標籤, 轉成純文字。"""
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    # 保留圖片替代文字(無障礙檢核會用到)
    html = re.sub(r'<img[^>]*alt="([^"]*)"[^>]*>', r" [圖片:\1] ", html, flags=re.I)
    # 保留連結網址, 方便檢查超連結
    html = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r" \2 (連結:\1) ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


SYSTEM_PROMPT = (
    "你是臺北市政府網站檢核的輔助判讀員。"
    "使用者會給你一個網頁的純文字內容與一個問題。"
    "請只根據提供的網頁內容回答, 不要編造網頁上沒有的資訊; "
    "若網頁內容不足以回答, 請明確說「網頁內容中查無此資訊」。"
    "回答使用繁體中文, 簡潔列點。"
)


def ask_ai(question: str, page_text: str, url: str) -> str:
    """把網頁文字與問題送給 AI 判讀, 回傳結果。
    依 config 的 ai_provider 切換三大家:
      openai    OpenAI 相容(地端 vLLM/Ollama, 或 OpenAI/OpenRouter/Groq 雲端)
      anthropic 雲端 Claude(官方 SDK)
      gemini    雲端 Google Gemini(OpenAI 相容端點)"""
    if len(page_text) > MAX_CONTENT_CHARS:
        page_text = page_text[:MAX_CONTENT_CHARS] + "\n...(內容過長已截斷)"
    user = f"網頁網址: {url}\n\n網頁內容如下:\n{page_text}\n\n問題: {question}"
    if PROVIDER == "anthropic":
        return _ask_anthropic(user)
    return _ask_openai(user)  # openai 與 gemini 共用 OpenAI 相容路徑


def _ask_openai(user: str) -> str:
    """OpenAI 相容 API(OpenAI / 地端 / Gemini OpenAI 端點 / OpenRouter / Groq)。"""
    payload = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "temperature": 0.1,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{_effective_base()}/chat/completions", data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {API_KEY}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def _ask_anthropic(user: str) -> str:
    """雲端 Anthropic Claude(messages API, 用官方 SDK)。需 pip install anthropic。
    模型由 config 的 ai_model 指定(預設 claude-opus-4-8)。"""
    import anthropic  # 選配依賴, 用到才載入
    client = anthropic.Anthropic(api_key=API_KEY)
    msg = client.messages.create(
        model=MODEL or "claude-opus-4-8",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if b.type == "text")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 3:
        print(__doc__)
        return 1

    url, question = sys.argv[1], sys.argv[2]

    print(f"[抓取] {url}")
    try:
        html = fetch_html(url)
    except Exception as e:
        print(f"[錯誤] 網頁抓取失敗: {e}")
        return 2
    text = html_to_text(html)
    print(f"[完成] 取得純文字 {len(text)} 字")

    if question == "--dump":
        print("=" * 60)
        print(text)
        return 0

    print(f"[判讀] 使用地端模型 {MODEL} @ {BASE_URL}")
    try:
        answer = ask_ai(question, text, url)
    except Exception as e:
        print(f"[錯誤] AI 呼叫失敗: {e}")
        print("請確認地端 AI 服務已啟動, 且 AI_BASE_URL / AI_MODEL 設定正確")
        return 3

    print("=" * 60)
    print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
