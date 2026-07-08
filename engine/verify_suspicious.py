# -*- coding: utf-8 -*-
"""SUSPICIOUS 第二關:地端 AI 複查。

連結稽核(audit_links)機械關鍵字比對會有大量誤報(反毒/藝文/新聞站被「賭場/色情」
等字誤中)。這支對機械判為 SUSPICIOUS 的連結,實際連線抓內容 → 送地端 AI 判:
  A 真線上賭博/色情/博弈站
  B 網域停放/出售(搶註嫌疑)
  C 正當內容(政府/新聞/藝文/防治宣導等,誤報)
把 AI 判定寫回一份 verified CSV,讓搶註欄可信。

用法: python -m engine.verify_suspicious [audit_csv]
      不給參數則取最新一次 linkaudit_all 的 all_problems.csv
"""
import csv, glob, os, re, ssl, sys, socket, urllib.parse, urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); sys.path.insert(0, os.path.join(_ROOT, "monthly"))
import webcheck_ai

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
HDR = {"User-Agent": "Mozilla/5.0"}

AI_Q = ("以下是某政府網站連到的『外部連結』實際內容。請判斷該連結目標屬於哪一類,"
        "只回開頭代號再加一句理由:\n"
        "A=線上賭博/色情/博弈等不當網站(含被搶註後導向這類內容)\n"
        "B=網域停放頁/出售頁(buy this domain/域名出售/parked)\n"
        "C=正當內容(政府、新聞媒體、藝文、學術、防治宣導等;關鍵字只是內文剛好提到)\n"
        "判斷重點:防治/反毒/反性別暴力/嗜賭症衛教/藝評報導 都算 C(正當)。")


def fetch(url):
    host = urllib.parse.urlparse(url).hostname
    try:
        socket.getaddrinfo(host, None)
    except Exception:
        return None, "DNS失敗"
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=15, context=CTX)
        html = r.read(120000).decode("utf-8", "replace")
        return webcheck_ai.html_to_text(html)[:4000], None
    except Exception as e:
        return None, f"連不上({type(e).__name__})"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        d = sorted(glob.glob(os.path.join(_ROOT, "private", "reports", "linkaudit_all_*")), key=os.path.getmtime)[-1]
        path = os.path.join(d, "all_problems.csv")
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    susp = {}
    for r in rows:
        if r["risk"] == "SUSPICIOUS" and r["url"] not in susp:
            susp[r["url"]] = r
    print(f"第二關 AI 複查:{len(susp)} 筆 SUSPICIOUS(來源 {path})\n")

    out = []
    for i, (u, r) in enumerate(susp.items(), 1):
        text, err = fetch(u)
        if err:
            verdict, reason = ("B", f"實連{err},疑停放/失效")
        else:
            try:
                ans = webcheck_ai.ask_ai(AI_Q, text, u).strip()
                m = re.search(r"[ABC]", ans)
                verdict = m.group(0) if m else "?"
                reason = ans.replace("\n", " ")[:80]
            except Exception as e:
                verdict, reason = ("?", f"AI失敗({type(e).__name__})")
        out.append({"verdict": verdict, "url": u, "site": r["site_name"], "org": r.get("org", ""),
                    "kw": r["note"], "ai_reason": reason})
        tag = {"A": "🔴真賭博/色情", "B": "🟠停放/搶註", "C": "🟢誤報(正當)", "?": "❓待人工"}.get(verdict, verdict)
        print(f"[{i:2}] {tag}  {r['site_name'][:16]:18} {u[:52]}")
        print(f"      AI: {reason}")

    # 寫回
    outpath = path.replace("all_problems.csv", "suspicious_verified.csv")
    with open(outpath, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["verdict", "url", "site", "org", "kw", "ai_reason"])
        w.writeheader(); w.writerows(out)
    from collections import Counter
    print(f"\n判定分布: {dict(Counter(o['verdict'] for o in out))}")
    print(f"真要辦的(A/B): {sum(1 for o in out if o['verdict'] in 'AB')} 筆")
    print(f"寫出: {outpath}")


if __name__ == "__main__":
    main()
