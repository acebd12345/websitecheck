# -*- coding: utf-8 -*-
"""深掃寄信模組：讀 full_overnight 報告目錄，按局處彙整異常，寄信通知。

設計要點：
  - 只寄「經 AI 複查後仍成立」的搶註(A/B) + 明確失效類(DEAD/BROKEN/REDIRECTED/WARN)
  - SUSPICIOUS 且 AI 判 C(誤報) → 不列入信
  - SUSPICIOUS 且 AI 判 ?(待人工) → 列在信末「待人工確認」區，不當警報
  - 一個局處若複查後 0 條真問題 → 不寄
  - 附件：異常明細 CSV ＋ 信件彙整 HTML 轉 PDF（playwright，未裝則只附 CSV）
  - 收件人：各局處在府內網站表「局處Email」欄的真值（per-局處）
    此欄可含多個承辦信箱（逗號/分號分隔）→ 全部都寄
  - --mail-to 為可選 override（給了才蓋全部收件人，測試用）
  - 查無 Email → fallback config mail_override_to → 兩者皆無跳過並警告

用法：
  # 搭配 full_overnight（階段4後自動呼叫，需 --mail）
  python -m engine.full_overnight --mail

  # 獨立對既有報告補寄
  python -m engine.mailer <報告目錄>
  python -m engine.mailer <報告目錄> --mail-to someone@example.com   # override 測試
  python -m engine.mailer <報告目錄> --dry-run   # 不寄，只印彙整結果
"""
import argparse, configparser, csv, datetime, html, json, os, re, sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import config

CSV_LIST = os.path.join(config.PRIVATE_DIR, "TCGweb_466站對照清單_v2.csv")

FALLBACK_MAIL_TO = config.get("mail_override_to", "")

RISK_LABEL = {
    "SUSPICIOUS": "可疑內容(賭博/色情/停放頁)",
    "DEAD": "DNS失敗或連不上",
    "BROKEN": "HTTP錯誤(404/403等)",
    "REDIRECTED": "重導向到其他網域",
    "WARN": "SSL憑證錯誤",
}
RISK_COLOR = {
    "SUSPICIOUS": "#c00000", "DEAD": "#e36c0a",
    "BROKEN": "#bf9000", "REDIRECTED": "#7030a0", "WARN": "#808080",
}
RISK_ORDER = {"SUSPICIOUS": 0, "DEAD": 1, "BROKEN": 2, "REDIRECTED": 3, "WARN": 4}


# ── 讀取報告 ──

def load_org_email_map():
    """從 466 站 CSV 建 局處 → 局處Email 對照。"""
    m = {}
    if not os.path.exists(CSV_LIST):
        return m
    for row in csv.DictReader(open(CSV_LIST, encoding="utf-8-sig")):
        org = row.get("局處", "").strip()
        email = row.get("局處Email", "").strip()
        if org and email and "@" in email:
            m[org] = email
    return m


def load_report(outdir):
    """讀報告目錄，回傳 (all_problems, verified_map, progress)。
    verified_map: url → {ai_verdict, ...}  只有 SUSPICIOUS 才有。
    """
    combined = os.path.join(outdir, "all_problems.csv")
    sv_csv = os.path.join(outdir, "suspicious_verified.csv")
    progress_f = os.path.join(outdir, "progress.json")

    if not os.path.exists(combined):
        sys.exit(f"找不到 {combined}")

    allp = list(csv.DictReader(open(combined, encoding="utf-8-sig")))
    verified = {}
    if os.path.exists(sv_csv):
        for r in csv.DictReader(open(sv_csv, encoding="utf-8-sig")):
            verified[r.get("url", "")] = r
    progress = []
    if os.path.exists(progress_f):
        progress = json.load(open(progress_f, encoding="utf-8"))

    return allp, verified, progress


def group_by_org(allp, verified, progress):
    """按局處分組，過濾掉 AI 判 C 的 SUSPICIOUS，回傳 {局處: [problems]}。
    每條 problem 增加 _verdict / _pending_human 欄位供信件使用。
    """
    # 從 progress 建 url→org 備查（all_problems 裡有 org 欄）
    groups = {}
    for p in allp:
        org = p.get("org", "").strip() or "未知局處"
        risk = p.get("risk", "")
        url = p.get("url", "")

        if risk == "SUSPICIOUS":
            v = verified.get(url, {})
            verdict = v.get("ai_verdict", "?")
            if verdict == "C":
                continue  # 誤報，不列
            p = dict(p)  # 不改原物件
            p["_verdict"] = verdict
            p["_pending_human"] = (verdict == "?")
            p["_ai_reason"] = v.get("ai_reason", "")
        else:
            p = dict(p)
            p["_verdict"] = ""
            p["_pending_human"] = False
            p["_ai_reason"] = ""

        groups.setdefault(org, []).append(p)

    return groups


# ── 信件建構 ──

FEEDBACK_URL = "(待建)"   # GAS 回饋頁 URL，建好後填


def classify_cause(p):
    """回傳 (bucket, 狀態, 建議做法)。bucket ∈ act(確定需處理)/update(建議更新)/check(請貴處確認)。
    原則:確定的才說確定;系統無法自動判定的一律「請貴處確認」,絕不臆測「誤報」。"""
    risk = p.get("risk", ""); note = p.get("note", ""); verdict = p.get("_verdict", "")
    if risk == "SUSPICIOUS":
        # C(誤報)已在 group_by_org 濾掉;剩 A/B(AI判真)或 ?(未複查)一律列出,不臆測
        if verdict in ("A", "B"):
            return ("act", "AI 判定此連結連向賭博／色情／搶註內容", "請立即移除該連結")
        return ("act", "偵測到可疑關鍵字,尚待人工確認", "請人工檢視該連結內容,確認後移除")
    if risk == "REDIRECTED":
        return ("update", "連結已跳轉至其他網址", "請將連結更新為跳轉後的正確網址")
    if risk == "DEAD":
        if "釋出" in note or "留意被搶註" in note:
            return ("act", "外部網域已失效(可能已被釋出／搶註)", "請移除該連結")
        if "政府專屬" in note or "無搶註" in note:
            return ("check", "目標政府網站服務下線", "請確認是否仍需保留此連結")
        return ("check", "系統連線逾時,未取得回應", "請確認連結是否正常;正常請忽略,失效請移除")
    if risk == "BROKEN":
        if "403" in note or "429" in note:
            return ("check", "對方網站回應 " + (note or "HTTP 403/429"), "請確認連結是否正常;正常請忽略,失效請移除")
        if "404" in note:
            return ("act", "目標頁面回應 404(不存在)", "請確認並移除或更正該連結")
        return ("check", "HTTP 狀態異常(" + (note or "") + ")", "請確認連結是否正常")
    if risk == "WARN":
        return ("check", "目標網站 SSL 憑證異常", "請確認連結是否正常")
    return ("check", note or "系統偵測項目", "請確認連結是否正常")


def _triage(problems):
    """回傳 (act, update, check) 三桶,每條附 _cause / _advice。"""
    act, update, check = [], [], []
    for p in problems:
        b, label, advice = classify_cause(p)
        q = dict(p); q["_cause"] = label; q["_advice"] = advice
        (act if b == "act" else update if b == "update" else check).append(q)
    for lst in (act, update, check):
        lst.sort(key=lambda r: (r.get("site_name", ""), r.get("url", "")))
    return act, update, check


def build_mail_html(org, problems, stamp):
    """建局處層級彙整信 HTML(需處理先行,不確定者一律「請確認」、不寫誤報,每條附建議做法)。"""
    today = stamp or datetime.date.today().strftime("%Y-%m-%d")
    act, update, check = _triage(problems)

    def n_pages(p):
        loc = p.get("all_locations", "")
        return len([l for l in loc.splitlines() if l.strip()]) or 1

    def line(p):
        return (f"<li style='margin-bottom:8px'>{html.escape(p['url'])}"
                f"<br><span style='font-size:10pt'>狀態:{html.escape(p['_cause'])}</span>"
                f"<br><span style='font-size:10pt;color:#0f6e56'>建議做法:{html.escape(p['_advice'])}</span>"
                f"<br><span style='font-size:9pt;color:#888'>站:{html.escape(p.get('site_name',''))}"
                f";出現在 {n_pages(p)} 個頁面</span></li>")

    P = []
    P.append("<p>您好:</p>")
    P.append(f"<p>依數發部 115/6/8「委外案或活動結束後未移除網址」清查,{today} 對 "
             f"<b>{html.escape(org)}</b> 所管網站自動深度掃描,結果如下。</p>")
    # 摘要
    P.append("<p><b>■ 摘要</b></p><ul style='font-size:10pt'>")
    P.append(f"<li><b style='color:#c00000'>需處理:{len(act)} 筆</b></li>")
    if update:
        P.append(f"<li>建議更新連結(已跳轉):{len(update)} 筆</li>")
    P.append(f"<li>請貴處確認:{len(check)} 筆</li></ul>")
    # 需處理
    P.append("<p><b>■ 需要貴處處理</b></p>")
    if act:
        P.append("<ul style='font-size:10pt'>" + "".join(line(p) for p in act) + "</ul>")
    else:
        P.append("<p style='color:#107c10'>本次沒有需要貴處處理的連結。</p>")
    # 建議更新(跳轉)
    if update:
        P.append("<p><b>■ 連結已跳轉,建議更新為新網址</b></p><ul style='font-size:10pt'>")
        for p in update:
            fin = p.get("final_url", "") or ""
            P.append(f"<li style='margin-bottom:6px'>{html.escape(p['url'])}<br>"
                     f"→ 建議更新為:<b>{html.escape(fin)}</b></li>")
        P.append("</ul>")
    # 請貴處確認(不臆測誤報,只陳述系統偵測到的狀態)
    if check:
        P.append("<p><b>■ 請貴處確認</b></p>")
        P.append("<p style='font-size:9.5pt;color:#555'>以下由系統自動偵測到異常狀態,但可能因對方網站設定"
                 "或暫時性連線問題所致,系統無法自動判定是否為實際失效。<b>請貴處確認</b>:確認正常者無需回覆,"
                 "確為失效請移除或更正。</p>")
        P.append("<ul style='font-size:9.5pt'>" + "".join(line(p) for p in check[:30]) + "</ul>")
        if len(check) > 30:
            P.append(f"<p style='font-size:9pt;color:#888'>…另有 {len(check)-30} 筆,詳見附件明細。</p>")
    # 如何回覆
    P.append("<p><b>■ 如何回覆(選用)</b></p><ul style='font-size:10pt'>")
    P.append("<li>連結<b>已移除或下架者無需回報</b>——下一輪掃描(約一個月內)就不會再出現。</li>")
    P.append(f"<li>若<b>已處理／處理中／認為不需處理</b>,請至回饋頁填寫並輸入貴處專屬 "
             f"<b>PIN 碼</b>(另以專信寄送):<br>🔗 回饋頁:{FEEDBACK_URL}</li></ul>")
    P.append("<p style='font-size:9pt;color:#808080'>本郵件由連結稽核工具自動產生。完整明細見附件。</p>")
    return f"<div style='font-family:微軟正黑體,Segoe UI;font-size:11pt'>{''.join(P)}</div>"


def make_subject(org, problems, stamp):
    """產主旨:以「需處理幾筆」為主軸,有 AI 判 A 的加【急】。"""
    today = stamp or datetime.date.today().strftime("%Y-%m-%d")
    act, _u, _c = _triage(problems)
    has_urgent = any(p.get("_verdict") == "A" for p in act)
    if act:
        subject = f"網站對外連結稽核結果 - {org} {today}(需處理 {len(act)} 筆)"
    else:
        subject = f"網站對外連結稽核結果 - {org} {today}(本次無需處理)"
    if has_urgent:
        subject = "【急】" + subject
    return subject


def write_org_csv(problems, out_path):
    """為單一局處寫異常 CSV 附件。"""
    cols = ["risk", "url", "host", "note", "site_name", "found_on", "all_locations"]
    try:
        f = open(out_path, "w", newline="", encoding="utf-8-sig")
    except PermissionError:
        out_path = out_path.replace(".csv", "_new.csv")
        f = open(out_path, "w", newline="", encoding="utf-8-sig")
    with f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for p in problems:
            if not p.get("_pending_human"):
                w.writerow(p)
    return out_path


# ── 寄信 ──

def _load_mail_config():
    """從共用 config.json 取 mail/gmail 設定。"""
    c = config._cfg
    cfg = configparser.ConfigParser()
    cfg["mail"] = {k: str(v) for k, v in c.get("mail", {}).items()}
    cfg["gmail"] = {k: str(v) for k, v in c.get("gmail", {}).items()}
    return cfg


def parse_recipients(to):
    """把可能含逗號/分號的收件字串拆成乾淨、去重的 email list（保序）。"""
    parts = to if isinstance(to, (list, tuple)) else re.split(r"[,;]", to or "")
    seen, out = set(), []
    for p in parts:
        e = (p or "").strip()
        if e and "@" in e and e.lower() not in seen:
            seen.add(e.lower())
            out.append(e)
    return out


def _attach_list(attachments):
    """把附件參數（單一路徑或路徑 list）正規化成「存在的檔案」list。"""
    if not attachments:
        return []
    if isinstance(attachments, (str, bytes)):
        attachments = [attachments]
    return [a for a in attachments if a and os.path.exists(a)]


def html_to_pdf(html, pdf_path):
    """用 playwright(chromium) 把 HTML 內文轉 PDF。
    未裝 playwright 或轉檔失敗 → 回 None（只警告、不阻斷寄信）。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  ⚠ 未裝 playwright,略過 PDF 附件（CSV 仍會附）")
        return None
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page()
            pg.set_content(html, wait_until="networkidle")
            pg.pdf(path=pdf_path, format="A4", print_background=True,
                   margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"})
            b.close()
        return pdf_path
    except Exception as e:
        print(f"  ⚠ PDF 轉檔失敗({type(e).__name__}),略過 PDF 附件（CSV 仍會附）")
        return None


def send_outlook(to, subject, html_body, attachments=None):
    import win32com.client
    addrs = parse_recipients(to)
    if not addrs:
        raise ValueError(f"無有效收件人: {to!r}")
    to = "; ".join(addrs)  # Outlook 收件人以分號分隔
    outlook = win32com.client.Dispatch("Outlook.Application")
    mail = outlook.CreateItem(0)
    mail.To = to
    mail.Subject = subject
    mail.HTMLBody = html_body
    for att in _attach_list(attachments):
        mail.Attachments.Add(os.path.abspath(att))
    try:
        mail.Send()
        print(f"  已寄出(outlook): {to}")
    except Exception:
        # Outlook 安全性阻擋 Send(),改開草稿讓使用者手動寄
        mail.Display()
        print(f"  已開草稿(outlook 安全性阻擋自動寄出): {to}")


def send_gmail(cfg, to, subject, html_body, attachments=None):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.application import MIMEApplication

    user = cfg.get("gmail", "user")
    pwd = cfg.get("gmail", "app_password")
    addrs = parse_recipients(to)
    if not addrs:
        raise ValueError(f"無有效收件人: {to!r}")
    msg = MIMEMultipart()
    msg["From"] = user
    msg["To"] = ", ".join(addrs)  # 郵件標頭以逗號分隔
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    for att in _attach_list(attachments):
        name = os.path.basename(att)
        subtype = "pdf" if name.lower().endswith(".pdf") else "octet-stream"
        with open(att, "rb") as f:
            part = MIMEApplication(f.read(), _subtype=subtype, Name=name)
        part["Content-Disposition"] = f'attachment; filename="{name}"'
        msg.attach(part)
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg, to_addrs=addrs)  # 逐一送給每個承辦
    print(f"  已寄出(gmail): {', '.join(addrs)}")


def send_mail(to, subject, html_body, attachments=None):
    cfg = _load_mail_config()
    method = cfg.get("mail", "method", fallback="outlook").strip().lower()
    if method == "gmail":
        send_gmail(cfg, to, subject, html_body, attachments)
    else:
        send_outlook(to, subject, html_body, attachments)


def _build_report_pdfs(org, problems, base):
    """用既有 report_html 產該局處(這次有問題的站)的正式報告 → 轉 PDF。
    回 PDF 路徑 list;任何失敗回 [](呼叫端會退回信件彙整 PDF)。"""
    try:
        from engine.report_html import generate_for_sites
        # 站名→網址(讀站清單 CSV);只挑本局處這次有問題的站
        names = {(p.get("site_name") or "").strip() for p in problems if (p.get("site_name") or "").strip()}
        url_by_name = {}
        if os.path.exists(CSV_LIST):
            for row in csv.DictReader(open(CSV_LIST, encoding="utf-8-sig")):
                nm = (row.get("網站名稱") or "").strip()
                u = (row.get("網址") or "").strip()
                if nm and u:
                    url_by_name[nm] = u
        urls = [url_by_name[n] for n in names if n in url_by_name]
        if not urls:
            return []
        pdfs = []
        for hp in generate_for_sites(urls):
            if not hp or not os.path.exists(hp):
                continue
            html_str = open(hp, encoding="utf-8").read()
            pdfp = os.path.splitext(hp)[0] + ".pdf"
            if html_to_pdf(html_str, pdfp):
                pdfs.append(pdfp)
        return pdfs
    except Exception as e:
        print(f"  ⚠ [{org}] 產正式報告附件失敗({type(e).__name__}: {e}),改附信件 PDF")
        return []


# ── 主流程 ──

def run(outdir, mail_to=None, dry_run=False):
    """對報告目錄執行寄信。回傳 (sent_count, skipped_orgs, details)。

    mail_to: 全部收件人 override（測試用）。None=走各局處Email真值。
    """
    sys.stdout.reconfigure(encoding="utf-8")
    override = mail_to   # 明確給了才蓋全部收件人
    fallback = FALLBACK_MAIL_TO

    allp, verified, progress = load_report(outdir)
    groups = group_by_org(allp, verified, progress)
    org_email = load_org_email_map()

    # 從目錄名取日期戳
    dirname = os.path.basename(outdir)
    stamp_match = dirname.replace("full_overnight_", "")[:8]
    try:
        stamp = f"{stamp_match[:4]}-{stamp_match[4:6]}-{stamp_match[6:8]}"
    except Exception:
        stamp = datetime.date.today().strftime("%Y-%m-%d")

    sent = 0
    skipped = []
    details = []
    for org, problems in sorted(groups.items()):
        # 過濾：只計確認問題(非待人工)
        confirmed = [p for p in problems if not p.get("_pending_human")]
        if not confirmed:
            skipped.append(org)
            print(f"  [{org}] 複查後 0 條真問題,不寄")
            continue

        # 收件人決定：override > 局處Email > fallback > 跳過
        if override:
            to = override
        else:
            to = org_email.get(org, "")
            if not to:
                if fallback:
                    to = fallback
                    print(f"  ⚠ [{org}] 查無局處Email, fallback → {to}")
                else:
                    print(f"  ⚠ [{org}] 查無局處Email 且無 fallback, 跳過(不得寄錯人)")
                    skipped.append(org)
                    continue

        subject = make_subject(org, problems, stamp)
        body = build_mail_html(org, problems, stamp)

        n_confirmed = len(confirmed)
        n_pending = sum(1 for p in problems if p.get("_pending_human"))
        n_susp = sum(1 for p in confirmed if p["risk"] == "SUSPICIOUS")

        info = {"org": org, "to": to,
                "confirmed": n_confirmed, "pending_human": n_pending,
                "suspicious": n_susp, "subject": subject}
        details.append(info)

        if dry_run:
            print(f"  [DRY-RUN] {org}: 確認 {n_confirmed} 筆 + 待人工 {n_pending} 筆 → 寄 {to}")
            print(f"            主旨: {subject}")
            for p in confirmed:
                v = p.get("_verdict", "")
                vtag = f" [AI:{v}]" if v else ""
                print(f"            - [{p['risk']}]{vtag} {p['url'][:60]}")
            continue

        # 附件:異常明細 CSV + 該局處「正式報告」(report_html 產,轉 PDF)
        base = os.path.join(outdir, f"mail_{org.replace(' ', '_')}")
        csv_path = write_org_csv(problems, base + ".csv")
        attachments = [csv_path]
        report_pdfs = _build_report_pdfs(org, problems, base)
        if report_pdfs:
            attachments += report_pdfs
        else:                                   # 報告產不出來→退回信件彙整轉 PDF,不中斷
            fallback = html_to_pdf(body, base + ".pdf")
            if fallback:
                attachments.append(fallback)

        try:
            send_mail(to, subject, body, attachments)
            sent += 1
        except Exception as e:
            print(f"  !! [{org}] 寄信失敗: {e}")

    return sent, skipped, details


def main():
    ap = argparse.ArgumentParser(description="對深掃報告目錄按局處寄信")
    ap.add_argument("outdir", help="報告目錄路徑(full_overnight_* 目錄)")
    ap.add_argument("--mail-to", default=None,
                    help="收件人 override(測試用;不給則走各局處Email真值)")
    ap.add_argument("--dry-run", action="store_true", help="不寄信,只印彙整結果")
    args = ap.parse_args()

    outdir = args.outdir
    if not os.path.isabs(outdir):
        outdir = os.path.join(config.PRIVATE_DIR, "reports", outdir)

    print(f"===== 深掃寄信 {os.path.basename(outdir)} =====")
    rcpt_desc = args.mail_to or "各局處Email真值"
    print(f"收件人: {rcpt_desc}" + (" [DRY-RUN]" if args.dry_run else ""))
    sent, skipped, details = run(outdir, mail_to=args.mail_to or None, dry_run=args.dry_run)
    print(f"\n完成: 寄出 {sent} 封, 跳過 {len(skipped)} 局處(零真問題)")


if __name__ == "__main__":
    main()
