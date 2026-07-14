# -*- coding: utf-8 -*-
"""HTML 報告產生器：把掃描產出自動轉成人可讀的 HTML 報告。

兩種產物：
  - 單站報告：一站一份 HTML，按局處歸資料夾
  - 全市總報告：彙整所有深掃＋每日稽核的一份總覽

用法：
  python -m engine.report_html                      # 全部
  python -m engine.report_html --site 兵役          # 只產名稱/網址含關鍵字的站
  python -m engine.report_html --org 教育局         # 只產某局處的站
  python -m engine.report_html --city               # 只產全市總報告
  python -m engine.report_html --zip                # 產完後壓 zip
  python -m engine.report_html --days 30            # 每日近況回看天數(預設14)
"""
import argparse, csv, datetime, glob, json, os, re, sys, zipfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import config

# ── 常數 ──
REPORTS_HTML_DIR = os.path.join(config.PRIVATE_DIR, "reports_html")
FULL_OVERNIGHT_GLOB = os.path.join(config.PRIVATE_DIR, "reports", "full_overnight_*")
DAILY_GLOB = os.path.join(config.PRIVATE_DIR, "problems_*_*.csv")
CSV_LIST = os.path.join(config.PRIVATE_DIR, "TCGweb_466站對照清單_v2.csv")

# AI 判定顯示
VERDICT_DISPLAY = {
    "A": ("🔴", "確認賭博/色情/掛馬"),
    "B": ("🟠", "停放/搶註嫌疑"),
    "C": ("🟢", "已排除誤報"),
    "?": ("❓", "待人工確認"),
}
VERDICT_DEFAULT = ("⚪", "機械判定，未複查")

# 風險排序（含 AI 判定前綴）
RISK_ORDER = {
    "🔴A": 0, "🟠B": 1, "❓?": 2, "⚪": 3,
    "DEAD": 4, "BROKEN": 5, "REDIRECTED": 6, "WARN": 7,
}

# pill 顏色
PILL_COLORS = {
    "🔴A": "#b91c1c", "🟠B": "#b45309", "❓?": "#7c3aed", "⚪": "#6b7280",
    "SUSPICIOUS": "#b91c1c",
    "DEAD": "#c2410c", "BROKEN": "#b45309", "REDIRECTED": "#7c3aed", "WARN": "#6b7280",
}

# ── CSS（從兩份樣板原檔抽出）──
CSS_SITE = """\
:root{--ink:#1b2430;--muted:#5a6b70;--line:#e3ddd1;--card:#fffdf9;--paper:#f7f5f0;--accent:#0f766e}
*{box-sizing:border-box;margin:0;padding:0}body{background:var(--paper);color:var(--ink);font-family:"Microsoft JhengHei",system-ui,sans-serif;line-height:1.6;padding:0 18px}
.wrap{max-width:1000px;margin:0 auto;padding:40px 0 80px}h1{font-size:26px;font-weight:800;border-bottom:3px solid var(--ink);padding-bottom:12px}
.sub{color:var(--muted);font-size:13px;margin:6px 0 26px}h2{font-size:18px;margin:30px 0 10px;padding-left:12px;border-left:5px solid var(--accent)}
.cards{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0}.c{flex:1;min-width:100px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;text-align:center}
.c .n{font-size:26px;font-weight:800}.c .l{font-size:12px;color:var(--muted);margin-top:4px}
table{width:100%;border-collapse:collapse;margin:10px 0;font-size:12.5px;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}
th,td{padding:7px 9px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}th{background:#eef0ea;font-weight:700;white-space:nowrap}
.u{font-family:Consolas,monospace;font-size:11px;color:#0f4c81;word-break:break-all}.n{font-size:11.5px;color:var(--muted)}.num{text-align:right;font-weight:600}
td a{font-family:Consolas,monospace;font-size:11px;color:#0f4c81;word-break:break-all}
.pill{color:#fff;padding:1px 7px;border-radius:4px;font-size:11px;font-weight:700}
.occ{font-size:11px}.occ ul{margin:3px 0 0 16px;padding:0}.occ li{margin:2px 0;word-break:break-all}
.box{background:#f0fbf9;border:1px solid #cfe9e4;border-left:5px solid var(--accent);border-radius:8px;padding:12px 16px;margin:10px 0;font-size:13px}
.hj{background:#fff5f5;border:1px solid #f0c0c0;border-left:5px solid #b91c1c;border-radius:8px;padding:6px 16px 12px;margin:10px 0}
.hjt{font-size:15px;font-weight:800;color:#b91c1c;margin:10px 0 4px}.hjtab th{background:#fbe9e9;width:110px}
.foot{margin-top:30px;padding-top:14px;border-top:1px solid var(--line);font-size:11px;color:var(--muted)}"""

CSS_CITY = """\
:root{--ink:#1b2430;--muted:#5a6b70;--line:#e3ddd1;--card:#fffdf9;--paper:#f7f5f0;--accent:#0f766e}
*{box-sizing:border-box;margin:0;padding:0}body{background:var(--paper);color:var(--ink);font-family:"Microsoft JhengHei",system-ui,sans-serif;line-height:1.6;padding:0 18px}
.wrap{max-width:1060px;margin:0 auto;padding:40px 0 90px}
h1{font-size:27px;font-weight:800;border-bottom:3px solid var(--ink);padding-bottom:12px}
.sub{color:var(--muted);font-size:13px;margin:6px 0 24px}
h2{font-size:19px;margin:32px 0 10px;padding-left:12px;border-left:5px solid var(--accent)}
h3{font-size:16px;margin:24px 0 6px;padding:6px 10px;background:#eef0ea;border-radius:6px}
.osub{font-size:12px;font-weight:400;color:var(--muted)} .rpt{font-size:12px;float:right}
.cards{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0}.c{flex:1;min-width:90px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;text-align:center}
.c .n{font-size:26px;font-weight:800}.c .l{font-size:12px;color:var(--muted);margin-top:4px}
table{width:100%;border-collapse:collapse;margin:8px 0;font-size:12.5px;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}
th,td{padding:6px 9px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}th{background:#eef0ea;font-weight:700;white-space:nowrap}
a{color:#0f4c81;word-break:break-all}td a{font-family:Consolas,monospace;font-size:11px}
.n{font-size:11.5px;color:var(--muted)}.num{text-align:right;font-weight:600}
.pill{color:#fff;padding:1px 7px;border-radius:4px;font-size:11px;font-weight:700;white-space:nowrap}
.box{background:#f0fbf9;border:1px solid #cfe9e4;border-left:5px solid var(--accent);border-radius:8px;padding:12px 16px;margin:10px 0;font-size:13px}
.ok{color:#0f766e;font-size:12.5px;padding:4px 0 10px}
.hj{background:#fff5f5;border:1px solid #f0c0c0;border-left:5px solid #b91c1c;border-radius:8px;padding:4px 16px 12px;margin:10px 0}
.hjt{font-size:15px;font-weight:800;margin:10px 0 4px}.hjtab th{background:#fbe9e9;width:110px}
.foot{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);font-size:11px;color:var(--muted)}"""

METHOD_BOX = (
    "本報告由自動化引擎產出。掃描方法：站內全頁 BFS 爬取，收集所有對外連結；"
    "政府專屬域(gov.tw/.taipei)、社群平臺、白名單只驗存活(HTTP 狀態碼)；"
    "其餘驗存活＋掃內容關鍵字，命中者經<b>地端 AI 二次研判</b>降級誤報。"
)


# ── 工具函式 ──

def _h(s):
    """HTML escape."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _sanitize_filename(name, max_len=80):
    """清洗站名做檔名。"""
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return name[:max_len] if name else "_"


def _parse_dir_stamp(dirname):
    """從 full_overnight_YYYYMMDD_HHMM 解析時間戳。"""
    m = re.search(r"(\d{8})_(\d{4})$", dirname)
    if m:
        try:
            return datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M")
        except ValueError:
            pass
    return None


def _card(number, label):
    return f'<div class="c"><div class="n">{number}</div><div class="l">{_h(label)}</div></div>'


# ── 資料載入 ──

def _load_site_list():
    """載入 TCGweb 466 站對照清單 → {url: {name, org}}."""
    mapping = {}
    if not os.path.exists(CSV_LIST):
        return mapping
    with open(CSV_LIST, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            url = (row.get("網址") or "").strip()
            if url:
                mapping[url] = {
                    "name": (row.get("網站名稱") or "").strip(),
                    "org": (row.get("局處") or "").strip(),
                }
    return mapping


def _load_all_verified():
    """掃全部 full_overnight 目錄的 suspicious_verified，以 URL 取最新判定。"""
    verified = {}   # url → {ai_verdict, type, ai_reason, evidence, stamp}
    for d in sorted(glob.glob(FULL_OVERNIGHT_GLOB)):
        stamp = _parse_dir_stamp(os.path.basename(d))
        ts = stamp or datetime.datetime.fromtimestamp(os.path.getmtime(d))
        # try json first, then csv
        vpath = os.path.join(d, "suspicious_verified.json")
        entries = []
        if os.path.exists(vpath):
            try:
                entries = json.load(open(vpath, encoding="utf-8"))
            except Exception:
                entries = []
        else:
            vpath = os.path.join(d, "suspicious_verified.csv")
            if os.path.exists(vpath):
                try:
                    with open(vpath, encoding="utf-8-sig") as f:
                        entries = list(csv.DictReader(f))
                except Exception:
                    entries = []
        for e in entries:
            url = e.get("url", "")
            if not url:
                continue
            prev = verified.get(url)
            if prev is None or ts >= prev["_stamp"]:
                verified[url] = {
                    "ai_verdict": e.get("ai_verdict", "?"),
                    "type": e.get("type", ""),
                    "ai_reason": e.get("ai_reason", ""),
                    "evidence": e.get("evidence", ""),
                    "_stamp": ts,
                }
    return verified


def _load_deep_scan_data():
    """掃全部 full_overnight 目錄，回傳每站最新資料。

    Returns:
        sites: {url: {name, org, stamp, dir, pages, links, status, problems: [rows]}}
        dir_info: [{dir, stamp, n_sites}]  (用於全市報告的涵蓋說明)
    """
    # 第一遍：收集每站在每個目錄的 progress
    site_dir = {}  # (url) → [(stamp, dirname, progress_entry)]
    dir_info = []
    for d in sorted(glob.glob(FULL_OVERNIGHT_GLOB)):
        bn = os.path.basename(d)
        stamp = _parse_dir_stamp(bn)
        if stamp is None:
            try:
                stamp = datetime.datetime.fromtimestamp(os.path.getmtime(d))
            except Exception:
                continue
        ppath = os.path.join(d, "progress.json")
        if not os.path.exists(ppath):
            continue
        try:
            prog = json.load(open(ppath, encoding="utf-8"))
        except Exception:
            continue
        ok_entries = [p for p in prog if p.get("status") == "ok"]
        dir_info.append({"dir": bn, "stamp": stamp, "n_sites": len(ok_entries)})
        for p in ok_entries:
            url = p.get("url", "")
            if not url:
                continue
            site_dir.setdefault(url, []).append((stamp, d, p))

    # 每站取最新（時間戳最新且 status=ok）
    sites = {}
    for url, entries in site_dir.items():
        entries.sort(key=lambda x: x[0], reverse=True)
        stamp, d, p = entries[0]
        sites[url] = {
            "name": p.get("name", ""),
            "url": url,
            "org": p.get("org", ""),
            "stamp": stamp,
            "dir": d,
            "pages": p.get("pages", 0),
            "links": p.get("links", 0),
            "status": p.get("status", ""),
        }

    # 載入每站的異常明細（只從該站最新目錄的 all_problems.csv）
    # 先按目錄分組
    dir_sites = {}  # dir → [url]
    for url, info in sites.items():
        dir_sites.setdefault(info["dir"], []).append(url)

    for d, urls in dir_sites.items():
        url_set = set(urls)
        ap_path = os.path.join(d, "all_problems.csv")
        if not os.path.exists(ap_path):
            for u in urls:
                sites[u]["problems"] = []
            continue
        try:
            with open(ap_path, encoding="utf-8-sig") as f:
                # 按站名分組（all_problems 裡沒有 URL 鍵，但有 site_name）
                # 更精確：用 found_on 或 site_name 對應；但 site_name 更可靠
                dir_problems = {}  # site_url → [row]
                # 需要反查 site_name → url
                name_to_url = {}
                for u in urls:
                    name_to_url[sites[u]["name"]] = u
                for row in csv.DictReader(f):
                    sn = row.get("site_name", "")
                    su = name_to_url.get(sn)
                    if su and su in url_set:
                        dir_problems.setdefault(su, []).append(row)
        except Exception:
            dir_problems = {}
        for u in urls:
            sites[u]["problems"] = dir_problems.get(u, [])

    return sites, dir_info


def _load_daily_problems(days=14):
    """載入 private/problems_{host}_{date}.csv 近 N 天的。"""
    cutoff = datetime.date.today() - datetime.timedelta(days=days)
    daily = []
    for path in glob.glob(DAILY_GLOB):
        bn = os.path.basename(path)
        # problems_{host}_{YYYY-MM-DD}.csv
        m = re.search(r"(\d{4}-\d{2}-\d{2})\.csv$", bn)
        if not m:
            continue
        try:
            d = datetime.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if d < cutoff:
            continue
        try:
            with open(path, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    row["_date"] = d.isoformat()
                    row["_file"] = bn
                    daily.append(row)
        except Exception:
            pass
    daily.sort(key=lambda r: r.get("_date", ""), reverse=True)
    return daily


def _apply_verdicts(problems, verified):
    """套用 AI 複查判定，回傳 (main_rows, excluded_rows)。

    main_rows: 主表（剔除 C 判定）
    excluded_rows: C 判定的列（附錄用）
    每列增加 _verdict_icon, _verdict_label, _ai_reason 欄位。
    """
    main, excluded = [], []
    for row in problems:
        risk = row.get("risk", "")
        if risk == "SUSPICIOUS":
            url = row.get("url", "")
            v = verified.get(url)
            if v:
                verdict = v["ai_verdict"]
                icon, label = VERDICT_DISPLAY.get(verdict, VERDICT_DEFAULT)
                row["_verdict_icon"] = icon
                row["_verdict_label"] = label
                row["_verdict_key"] = f"{icon}{verdict}"
                row["_ai_reason"] = v.get("ai_reason", "")
                row["_ai_type"] = v.get("type", "")
                row["_ai_evidence"] = v.get("evidence", "")
                if verdict == "C":
                    excluded.append(row)
                    continue
            else:
                row["_verdict_icon"] = "⚪"
                row["_verdict_label"] = "機械判定，未複查"
                row["_verdict_key"] = "⚪"
                row["_ai_reason"] = ""
        else:
            row["_verdict_key"] = risk
        main.append(row)
    # 排序
    main.sort(key=lambda r: (RISK_ORDER.get(r.get("_verdict_key", ""), 99),
                              r.get("url", "")))
    return main, excluded


# ── 單站報告 ──

def _risk_pill(row):
    """產生風險 pill HTML。"""
    risk = row.get("risk", "")
    if risk == "SUSPICIOUS":
        key = row.get("_verdict_key", "⚪")
        icon = row.get("_verdict_icon", "⚪")
        label = row.get("_verdict_label", risk)
        color = PILL_COLORS.get(key, "#6b7280")
        return f'<span class="pill" style="background:{color}">{icon} {_h(label)}</span>'
    color = PILL_COLORS.get(risk, "#6b7280")
    return f'<span class="pill" style="background:{color}">{_h(risk)}</span>'


def _locations_html(all_locations_str):
    """把 all_locations 欄（\\n 分行）轉成 <ul>。"""
    if not all_locations_str:
        return ""
    lines = [l.strip() for l in str(all_locations_str).split("\n") if l.strip()]
    if not lines:
        return ""
    items = "".join(f"<li>{_h(l)}</li>" for l in lines[:20])
    extra = f"<li>…（共 {len(lines)} 處）</li>" if len(lines) > 20 else ""
    return f'<div class="occ"><ul>{items}{extra}</ul></div>'


def _compliance_section(comp_data):
    """產生合規檢核 HTML 區塊（紅綠燈表格）。"""
    if not comp_data:
        return ""
    items = []
    for url, r in comp_data.get("urls", {}).items():
        https = r.get("https", {})
        cert = https.get("cert", {})
        rwd = r.get("rwd", {})
        acc = r.get("accessibility", {})

        items.append(("HTTPS 憑證有效",
                      cert.get("valid"),
                      f"有效至 {cert.get('expires', '?')}（剩 {cert.get('days_left', '?')} 天）"
                      if cert.get("valid") else (cert.get("error") or "")))
        items.append(("HTTP→HTTPS 轉址", https.get("redirect_to_https"), ""))
        items.append(("HSTS", https.get("hsts"), ""))
        rwd_ok = rwd.get("viewport") and rwd.get("responsive_css") if rwd else None
        items.append(("RWD 響應式設計", rwd_ok,
                      f"viewport {'○' if rwd.get('viewport') else '✕'}"
                      f"／響應式CSS {'○' if rwd.get('responsive_css') else '✕'}"
                      if rwd else ""))
        items.append(("站內搜尋功能", r.get("search"), "啟發式偵測"))
        if acc.get("badge_active"):
            items.append(("無障礙標章", True, acc.get("detail", "")))
        elif acc.get("badge_in_comment"):
            items.append(("無障礙標章", None, "標章程式碼存在但被註解"))
        else:
            items.append(("無障礙標章", False, ""))
        broken = r.get("links_broken", [])
        lt = r.get("links_total", 0)
        items.append(("首頁連結有效",
                      len(broken) == 0 if lt > 0 else None,
                      f"檢測 {r.get('links_checked', 0)}/{lt} 條，失效 {len(broken)} 條"))
        deep = r.get("deep")
        if deep and "error" not in deep:
            bi = deep.get("broken_internal", [])
            ou = deep.get("office_no_universal", [])
            items.append(("站內失效連結", len(bi) == 0,
                          f"爬 {deep.get('pages', 0)} 頁，失效 {len(bi)} 筆"))
            items.append(("下載文件通用格式", len(ou) == 0,
                          f"Office 缺 PDF/ODF {len(ou)} 筆"))

    ai_results = comp_data.get("ai", [])

    rows_html = []
    for label, ok, detail in items:
        if ok is True:
            icon, color = "🟢", "#0f766e"
        elif ok is False:
            icon, color = "🔴", "#b91c1c"
        else:
            icon, color = "🟡", "#b45309"
        rows_html.append(
            f"<tr><td style='color:{color};font-weight:bold;text-align:center'>"
            f"{icon}</td><td>{_h(label)}</td><td>{_h(detail)}</td></tr>")

    ai_html = ""
    if ai_results:
        ai_parts = []
        for ar in ai_results:
            ai_parts.append(
                f"<tr><td><a href='{_h(ar['url'])}' target='_blank'>"
                f"{_h(ar['url'][:50])}</a></td>"
                f"<td>{_h(ar['question'])}</td>"
                f"<td>{_h(ar['answer'][:200])}</td></tr>")
        ai_html = (
            "<h3 style='font-size:14px;margin:16px 0 6px'>AI 內容判讀</h3>"
            "<table><tr><th>頁面</th><th>題目</th><th>AI 回覆</th></tr>"
            + "\n".join(ai_parts) + "</table>")

    return (
        '<h2>合規檢核</h2>'
        '<table><tr><th style="width:30px"></th><th>項目</th><th>說明</th></tr>'
        + "\n".join(rows_html) + '</table>'
        + ai_html)


def generate_site_report(site_url, site_info, verified, out_dir,
                         used_names=None, compliance=None):
    """產生單站報告 HTML，回傳輸出路徑。

    Args:
        used_names: 本輪已用名稱集合 {(org_dir_name, fname)}，用於同輪重名偵測。
                    傳 None 時不做重名偵測（同站重產直接覆蓋）。
        compliance: 該站的合規檢核資料（來自 compliance.json）。
    """
    name = site_info.get("name", "")
    org = site_info.get("org", "") or "其他"
    stamp = site_info.get("stamp")
    stamp_str = stamp.strftime("%Y-%m-%d %H:%M") if stamp else "未知"
    dir_name = os.path.basename(site_info.get("dir", ""))
    pages = site_info.get("pages", 0)
    links = site_info.get("links", 0)
    problems = site_info.get("problems", [])

    main_rows, excluded_rows = _apply_verdicts(problems, verified)
    n_anomaly = len(main_rows)

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # 組 HTML
    rows_html = []
    for row in main_rows:
        url = row.get("url", "")
        status = row.get("status", "")
        note = row.get("note", "")
        locs = _locations_html(row.get("all_locations", ""))
        rows_html.append(
            f"<tr><td>{_risk_pill(row)}</td>"
            f'<td><a href="{_h(url)}" target="_blank">{_h(url)}</a></td>'
            f"<td>{_h(status)}</td>"
            f"<td>{_h(note)}</td>"
            f"<td>{locs}</td></tr>"
        )

    detail_table = ""
    if rows_html:
        detail_table = (
            '<table><tr><th>風險</th><th>問題連結</th><th>狀態</th>'
            '<th>說明</th><th>出現頁</th></tr>'
            + "\n".join(rows_html)
            + "</table>"
        )
    else:
        detail_table = '<p class="ok" style="color:#0f766e">本站無異常連結。</p>'

    # 合規檢核區塊
    compliance_html = _compliance_section(compliance) if compliance else ""

    # 附錄：已排除誤報
    appendix = ""
    if excluded_rows:
        ex_rows = []
        for row in excluded_rows:
            url = row.get("url", "")
            reason = row.get("_ai_reason", "")
            ex_rows.append(
                f'<tr><td><a href="{_h(url)}" target="_blank">{_h(url)}</a></td>'
                f"<td>{_h(reason)}</td></tr>"
            )
        appendix = (
            '<h2>三、已排除誤報（附錄）</h2>'
            '<p class="n">以下連結經地端 AI 複查判定為正當內容（C），已從主表剔除。</p>'
            '<table><tr><th>連結</th><th>AI 判定理由</th></tr>'
            + "\n".join(ex_rows)
            + "</table>"
        )

    html = f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>{_h(org)} · {_h(name)} 連結稽核報告</title>
<style>{CSS_SITE}</style></head><body><div class="wrap">
<h1>{_h(org)} · {_h(name)} 連結稽核報告</h1>
<p class="sub">掃描日期：{stamp_str}　｜　資料來源：{_h(dir_name)}　｜　產表時間：{now_str}</p>

<h2>一、摘要</h2>
<div class="cards">
{_card(pages, '掃描頁數')}
{_card(links, '對外連結')}
{_card(n_anomaly, '異常筆數')}
</div>
<div class="box">{METHOD_BOX}</div>

<h2>二、異常連結明細</h2>
{detail_table}

{compliance_html}

{appendix}

<div class="foot">報告由 engine/report_html.py 自動產生　｜　{now_str}</div>
</div></body></html>"""

    # 寫檔
    org_dir_name = _sanitize_filename(org)
    org_dir = os.path.join(out_dir, org_dir_name)
    os.makedirs(org_dir, exist_ok=True)
    fname = _sanitize_filename(name) + ".html"
    # 重名偵測：只看本輪記憶體集合，同站重跑直接覆蓋舊檔
    if used_names is not None:
        key = (org_dir_name, fname)
        if key in used_names:
            base = _sanitize_filename(name)
            for suffix in range(2, 100):
                fname = f"{base}_{suffix}.html"
                key = (org_dir_name, fname)
                if key not in used_names:
                    break
        used_names.add(key)
    fpath = os.path.join(org_dir, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(html)
    return fpath


# ── 全市總報告 ──

def generate_city_report(sites, verified, dir_info, daily_problems, out_dir, days=14):
    """產生全市總報告 HTML，回傳輸出路徑。"""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # 彙整所有站的異常
    all_main = []
    all_excluded = []
    org_data = {}  # org → {sites: set, problems: [rows], excluded: [rows]}
    site_list = load_site_list_for_context()

    for url, info in sites.items():
        org = info.get("org", "") or "其他"
        problems = info.get("problems", [])
        main_rows, excluded_rows = _apply_verdicts(problems, verified)
        all_main.extend(main_rows)
        all_excluded.extend(excluded_rows)
        od = org_data.setdefault(org, {"sites": set(), "main": [], "excluded": [],
                                       "suspicious": 0, "dead": 0, "broken": 0,
                                       "redirected": 0, "warn": 0, "hijack": 0})
        od["sites"].add(url)
        od["main"].extend(main_rows)
        od["excluded"].extend(excluded_rows)
        for r in main_rows:
            risk = r.get("risk", "")
            vk = r.get("_verdict_key", "")
            if vk == "🔴A":
                od["hijack"] += 1
            elif risk == "SUSPICIOUS":
                od["suspicious"] += 1
            elif risk == "DEAD":
                od["dead"] += 1
            elif risk == "BROKEN":
                od["broken"] += 1
            elif risk == "REDIRECTED":
                od["redirected"] += 1
            elif risk == "WARN":
                od["warn"] += 1

    # 涵蓋說明
    if dir_info:
        stamps = [di["stamp"] for di in dir_info if di["stamp"]]
        date_range = ""
        if stamps:
            earliest = min(stamps).strftime("%Y-%m-%d")
            latest = max(stamps).strftime("%Y-%m-%d")
            date_range = f"{earliest} ~ {latest}"
        total_dirs = len(dir_info)
        total_sites_with_data = len(sites)
        # 466 站中幾站尚未輪掃到
        total_in_list = len(site_list) if site_list else 466
        not_scanned = total_in_list - total_sites_with_data
        coverage_note = (
            f"彙整 {total_dirs} 個深掃目錄（{date_range}），"
            f"共 {total_sites_with_data} 站有資料"
        )
        if not_scanned > 0:
            coverage_note += f"，{not_scanned} 站尚未輪掃到（輪掃進行中，資料不全為常態）"
    else:
        coverage_note = "無深掃資料"

    # §1 確認遭入侵/搶註
    hijack_a = [r for r in all_main if r.get("_verdict_key") == "🔴A"]
    hijack_b = [r for r in all_main if r.get("_verdict_key") == "🟠B"]

    def _hijack_card(row, border_color):
        url = row.get("url", "")
        site = row.get("site_name", "")
        org = row.get("org", "")
        vtype = row.get("_ai_type", "")
        evidence = row.get("_ai_evidence", "")
        reason = row.get("_ai_reason", "")
        found_on = row.get("found_on", "")
        bc = border_color
        return (
            f'<div class="hj" style="border-left-color:{bc}">'
            f'<div class="hjt" style="color:{bc}">{_h(site)}（{_h(org)}）</div>'
            f'<table class="hjtab"><tr><th>問題外連</th>'
            f'<td><a href="{_h(url)}" target="_blank">{_h(url)}</a></td></tr>'
            f'<tr><th>本府問題頁</th><td><a href="{_h(found_on)}" target="_blank">{_h(found_on)}</a></td></tr>'
            f'<tr><th>AI研判</th><td>{_h(reason)}</td></tr>'
            f'<tr><th>型態</th><td>{_h(vtype)}</td></tr>'
            f'<tr><th>事證</th><td>{_h(evidence)}</td></tr></table></div>'
        )

    hijack_html = ""
    if hijack_a or hijack_b:
        parts = []
        for r in hijack_a:
            parts.append(_hijack_card(r, "#b91c1c"))
        for r in hijack_b:
            parts.append(_hijack_card(r, "#b45309"))
        hijack_html = (
            '<h2>一、確認遭入侵 / 搶註</h2>'
            + "\n".join(parts)
        )
    else:
        hijack_html = (
            '<h2>一、確認遭入侵 / 搶註</h2>'
            '<p class="ok">本期無確認遭入侵或搶註的案件。</p>'
        )

    # §2 全市異常統計
    n_suspicious = sum(1 for r in all_main if r.get("risk") == "SUSPICIOUS")
    n_dead = sum(1 for r in all_main if r.get("risk") == "DEAD")
    n_broken = sum(1 for r in all_main if r.get("risk") == "BROKEN")
    n_redirected = sum(1 for r in all_main if r.get("risk") == "REDIRECTED")
    n_hijack = len(hijack_a)

    stats_html = (
        '<h2>二、全市異常統計</h2>'
        '<div class="cards">'
        + _card(len(sites), "掃描站數")
        + _card(len(all_main), "異常合計")
        + _card(n_suspicious, "可疑連結")
        + _card(n_dead, "死連")
        + _card(n_hijack, "確認掛馬")
        + _card(len(all_excluded), "已排除誤報")
        + '</div>'
        + f'<div class="box">{coverage_note}</div>'
    )

    # §3 各局處摘要表
    org_sorted = sorted(org_data.items(),
                        key=lambda x: (x[1]["hijack"], x[1]["suspicious"],
                                       len(x[1]["main"])),
                        reverse=True)
    summary_rows = []
    for org_name, od in org_sorted:
        n_sites = len(od["sites"])
        total = len(od["main"])
        summary_rows.append(
            f'<tr><td><a href="#{_h(org_name)}">{_h(org_name)}</a></td>'
            f'<td class="num">{n_sites}</td>'
            f'<td class="num">{od["suspicious"]}</td>'
            f'<td class="num">{od["dead"]}</td>'
            f'<td class="num">{od["broken"]}</td>'
            f'<td class="num">{od["redirected"]}</td>'
            f'<td class="num">{od["warn"]}</td>'
            f'<td class="num">{total}</td>'
            f'<td class="num">{od["hijack"]}</td></tr>'
        )
    org_table_html = (
        '<h2>三、各局處摘要表</h2>'
        '<table><tr><th>局處</th><th>站數</th><th>可疑</th><th>死連</th>'
        '<th>HTTP</th><th>重導</th><th>SSL</th><th>合計</th><th>掛馬</th></tr>'
        + "\n".join(summary_rows)
        + '</table>'
    )

    # §4 各局處異常明細
    detail_parts = []
    for org_name, od in org_sorted:
        cap = 400
        rows = od["main"][:cap]
        org_dir_name = _sanitize_filename(org_name)
        # 單站報告相對路徑連結
        rpt_link = f'<span class="rpt"><a href="{org_dir_name}/">單站報告 →</a></span>'
        n_total = len(od["main"])
        cap_note = f"（僅列前 {cap} 筆，共 {n_total} 筆）" if n_total > cap else ""

        detail_parts.append(
            f'<h3 id="{_h(org_name)}">{_h(org_name)}'
            f' <span class="osub">{len(od["sites"])} 站、{n_total} 筆異常{cap_note}</span>'
            f'{rpt_link}</h3>'
        )
        if not rows:
            detail_parts.append('<p class="ok">無異常。</p>')
            continue
        trs = []
        for r in rows:
            url = r.get("url", "")
            site = r.get("site_name", "")
            status = r.get("status", "")
            note = r.get("note", "")
            trs.append(
                f"<tr><td>{_risk_pill(r)}</td>"
                f"<td>{_h(site)}</td>"
                f'<td><a href="{_h(url)}" target="_blank">{_h(url)}</a></td>'
                f"<td>{_h(status)}</td>"
                f"<td>{_h(note)}</td></tr>"
            )
        detail_parts.append(
            '<table><tr><th>風險</th><th>所屬站</th><th>問題連結</th>'
            '<th>狀態</th><th>說明</th></tr>'
            + "\n".join(trs)
            + '</table>'
        )

    detail_html = '<h2>四、各局處異常明細</h2>' + "\n".join(detail_parts)

    # §5 每日稽核近況
    daily_html = ""
    if daily_problems:
        daily_html = f'<h2>五、每日稽核近況（近 {days} 天）</h2>'
        # 依日期分組摘要
        by_date = {}
        for r in daily_problems:
            d = r.get("_date", "")
            by_date.setdefault(d, []).append(r)
        daily_rows = []
        for d in sorted(by_date.keys(), reverse=True):
            rows = by_date[d]
            n = len(rows)
            risks = {}
            for r in rows:
                rk = r.get("risk", "?")
                # 套 AI 判定
                url = r.get("url", "")
                v = verified.get(url)
                if rk == "SUSPICIOUS" and v and v["ai_verdict"] == "C":
                    continue  # C 也從每日摘要剔除
                risks[rk] = risks.get(rk, 0) + 1
            risk_str = "、".join(f"{k} {v}" for k, v in sorted(risks.items()))
            daily_rows.append(
                f"<tr><td>{_h(d)}</td><td class='num'>{n}</td>"
                f"<td>{_h(risk_str)}</td>"
                f"<td>機械判定，已逐站寄信</td></tr>"
            )
        daily_html += (
            '<table><tr><th>日期</th><th>筆數</th><th>風險分布</th><th>備註</th></tr>'
            + "\n".join(daily_rows)
            + '</table>'
        )
    else:
        daily_html = (
            f'<h2>五、每日稽核近況（近 {days} 天）</h2>'
            '<p class="n">無每日稽核資料。</p>'
        )

    # §6 已排除誤報（附錄）
    appendix_html = ""
    if all_excluded:
        ex_rows = []
        for r in all_excluded:
            url = r.get("url", "")
            site = r.get("site_name", "")
            org = r.get("org", "")
            reason = r.get("_ai_reason", "")
            ex_rows.append(
                f"<tr><td>{_h(site)}</td><td>{_h(org)}</td>"
                f'<td><a href="{_h(url)}" target="_blank">{_h(url)}</a></td>'
                f"<td>{_h(reason)}</td></tr>"
            )
        appendix_html = (
            '<h2>六、已排除誤報（附錄）</h2>'
            '<p class="n">以下連結經地端 AI 複查判定為正當內容（C），已從主表剔除。</p>'
            '<table><tr><th>站名</th><th>局處</th><th>連結</th><th>AI 判定理由</th></tr>'
            + "\n".join(ex_rows)
            + '</table>'
        )

    html = f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>全市連結稽核總報告</title>
<style>{CSS_CITY}</style></head><body><div class="wrap">
<h1>全市連結稽核總報告</h1>
<p class="sub">產表時間：{now_str}</p>

{hijack_html}

{stats_html}

{org_table_html}

{detail_html}

{daily_html}

{appendix_html}

<div class="foot">報告由 engine/report_html.py 自動產生　｜　{now_str}</div>
</div></body></html>"""

    os.makedirs(out_dir, exist_ok=True)
    fpath = os.path.join(out_dir, "全市連結稽核總報告.html")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(html)
    return fpath


def load_site_list_for_context():
    """載入站點清單，用於涵蓋度計算。"""
    return _load_site_list()


# ── 公開 API（供 full_overnight 掛接）──

def _load_compliance():
    """載入所有 full_overnight 目錄的 compliance.json，新目錄覆蓋舊目錄。"""
    compliance = {}
    for d in sorted(glob.glob(FULL_OVERNIGHT_GLOB)):
        cpath = os.path.join(d, "compliance.json")
        if not os.path.exists(cpath):
            continue
        try:
            data = json.load(open(cpath, encoding="utf-8"))
            for name, info in data.items():
                compliance[name] = info
        except Exception:
            pass
    return compliance


def generate_for_sites(site_urls, sites_data=None, verified=None):
    """對指定站產單站報告（供 full_overnight 收尾呼叫）。

    Args:
        site_urls: 要產報告的站 URL 清單
        sites_data: 預載的站資料 dict（可選，不傳就重新掃目錄）
        verified: 預載的 AI 判定 dict（可選）
    Returns:
        產出的檔案路徑清單
    """
    if sites_data is None or verified is None:
        _sites, _ = _load_deep_scan_data()
        if sites_data is None:
            sites_data = _sites
        if verified is None:
            verified = _load_all_verified()
    compliance = _load_compliance()
    out_dir = REPORTS_HTML_DIR
    used_names = set()
    paths = []
    for url in site_urls:
        info = sites_data.get(url)
        if not info:
            continue
        name = info.get("name", "")
        comp = compliance.get(name)
        p = generate_site_report(url, info, verified, out_dir, used_names,
                                 compliance=comp)
        paths.append(p)
    return paths


# ── 主程式 ──

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="HTML 報告產生器")
    ap.add_argument("--site", default="", help="只產名稱/網址含關鍵字的站(逗號分隔)")
    ap.add_argument("--org", default="", help="只產某局處的站")
    ap.add_argument("--city", action="store_true", help="只產全市總報告")
    ap.add_argument("--zip", action="store_true", help="產完後壓 zip")
    ap.add_argument("--days", type=int, default=14, help="每日稽核近況回看天數(預設14)")
    ap.add_argument("--no-report", action="store_true", help="跳過報告產生(供 full_overnight 用)")
    args = ap.parse_args()

    if args.no_report:
        print("[report_html] --no-report, 跳過", flush=True)
        return

    print("[report_html] 載入資料...", flush=True)
    sites, dir_info = _load_deep_scan_data()
    verified = _load_all_verified()
    daily_problems = _load_daily_problems(args.days)
    print(f"  深掃: {len(sites)} 站有資料, {len(dir_info)} 個目錄", flush=True)
    print(f"  AI判定: {len(verified)} 筆", flush=True)
    print(f"  每日稽核: {len(daily_problems)} 筆(近 {args.days} 天)", flush=True)

    out_dir = REPORTS_HTML_DIR
    os.makedirs(out_dir, exist_ok=True)
    generated = []

    # 過濾站
    target_sites = dict(sites)
    if args.site:
        toks = [t.strip().lower() for t in args.site.split(",") if t.strip()]
        target_sites = {u: s for u, s in target_sites.items()
                        if any(t in s["name"].lower() or t in u.lower() for t in toks)}
    if args.org:
        target_sites = {u: s for u, s in target_sites.items()
                        if s.get("org", "") == args.org}

    # 合規資料
    compliance = _load_compliance()
    print(f"  合規: {len(compliance)} 站有合規資料", flush=True)

    # 單站報告
    used_names = set()
    if not args.city:
        print(f"\n[report_html] 產生單站報告: {len(target_sites)} 站...", flush=True)
        for url, info in target_sites.items():
            name = info.get("name", "")
            comp = compliance.get(name)
            p = generate_site_report(url, info, verified, out_dir, used_names,
                                     compliance=comp)
            generated.append(p)
            name = info.get("name", "")[:20]
            print(f"  ✓ {name}", flush=True)
        print(f"  共 {len(generated)} 份單站報告", flush=True)

    # 全市總報告（無 --site/--org 過濾時，或有 --city 時）
    if args.city or (not args.site and not args.org):
        print("\n[report_html] 產生全市總報告...", flush=True)
        p = generate_city_report(sites, verified, dir_info, daily_problems,
                                 out_dir, args.days)
        generated.append(p)
        print(f"  ✓ {p}", flush=True)

    # zip
    if args.zip:
        print("\n[report_html] 壓縮 zip...", flush=True)
        for entry in os.listdir(out_dir):
            entry_path = os.path.join(out_dir, entry)
            if os.path.isdir(entry_path):
                zip_path = os.path.join(out_dir, f"{entry}.zip")
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for root, dirs, files in os.walk(entry_path):
                        for fn in files:
                            fp = os.path.join(root, fn)
                            arcname = os.path.relpath(fp, out_dir)
                            zf.write(fp, arcname)
                generated.append(zip_path)
                print(f"  ✓ {zip_path}", flush=True)

    print(f"\n[report_html] 完成, 共 {len(generated)} 個產出", flush=True)


if __name__ == "__main__":
    main()
