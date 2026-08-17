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
    """把一條問題分流成 (bucket, 成因標籤)。bucket ∈ act(需處理)/update(建議更新)/ref(參考誤報)。
    只信地端 AI 對 SUSPICIOUS 的判定；連線逾時多為境外掃描地緣誤判，降為參考。"""
    risk = p.get("risk", ""); note = p.get("note", ""); verdict = p.get("_verdict", "")
    if risk == "SUSPICIOUS":
        # C(誤報)已在 group_by_org 濾掉;剩 A/B(真)或 ?(未複查成功)一律當需確認,不可當誤報
        if verdict in ("A", "B"):
            return ("act", "疑遭搶註／掛不當內容(AI 判定真,請確認移除)")
        return ("act", "命中可疑關鍵字,待確認(未能複查,請人工檢視)")
    if risk == "REDIRECTED":
        return ("update", "連結已跳轉,建議更新為新網址")
    if risk == "DEAD":
        if "釋出" in note or "留意被搶註" in note:
            return ("act", "外部網域已釋出(搶註風險,請確認移除)")
        if "政府專屬" in note or "無搶註" in note:
            return ("ref", "政府服務網域下線(非搶註風險)")
        return ("ref", "連線逾時(多為境外掃描誤判或暫時性)")
    if risk == "BROKEN":
        if "403" in note or "429" in note:
            return ("ref", "對方網站阻擋自動檢測(擋爬蟲,通常正常)")
        if "404" in note:
            return ("act", "目標頁 404,連結失效")
        return ("ref", "HTTP 狀態異常")
    if risk == "WARN":
        return ("ref", "SSL 憑證問題")
    return ("ref", "其他")


def _triage(problems):
    """回傳 (act, update, ref) 三桶,每條附 _cause。"""
    act, update, ref = [], [], []
    for p in problems:
        b, label = classify_cause(p)
        q = dict(p); q["_cause"] = label
        (act if b == "act" else update if b == "update" else ref).append(q)
    for lst in (act, update, ref):
        lst.sort(key=lambda r: (r.get("site_name", ""), r.get("url", "")))
    return act, update, ref


def build_mail_html(org, problems, stamp):
    """建局處層級彙整信 HTML(成因分流:需處理先行、誤報收摺)。"""
    today = stamp or datetime.date.today().strftime("%Y-%m-%d")
    act, update, ref = _triage(problems)

    def n_pages(p):
        loc = p.get("all_locations", "")
        return len([l for l in loc.splitlines() if l.strip()]) or 1

    def line(p):
        return (f"<li><b style='color:#c00000'>{html.escape(p['_cause'])}</b> — "
                f"{html.escape(p['url'])}"
                f"<br><span style='font-size:9pt;color:#666'>出現在 {n_pages(p)} 個頁面;"
                f"站:{html.escape(p.get('site_name',''))};狀況:{html.escape(p.get('note',''))}</span></li>")

    P = []
    P.append("<p>您好:</p>")
    P.append(f"<p>依數發部 115/6/8「委外案或活動結束後未移除網址」清查,{today} 對 "
             f"<b>{html.escape(org)}</b> 所管網站自動深度掃描(含 AI 複查),結果如下。</p>")
    # 摘要
    P.append("<p><b>■ 摘要</b></p><ul style='font-size:10pt'>")
    P.append(f"<li><b style='color:#c00000'>需貴處處理或確認:{len(act)} 筆</b></li>")
    if update:
        P.append(f"<li>建議更新連結(已跳轉):{len(update)} 筆</li>")
    P.append(f"<li>系統偵測、多屬誤報(僅供參考):{len(ref)} 筆</li></ul>")
    # 需處理
    P.append("<p><b>■ 需要貴處處理或確認</b></p>")
    if act:
        P.append("<ul style='font-size:10pt'>" + "".join(line(p) for p in act) + "</ul>")
    else:
        P.append("<p style='color:#107c10'>本次沒有需要貴處處理的連結 👍</p>")
    # 建議更新(跳轉)
    if update:
        P.append("<p><b>■ 已跳轉,建議把連結更新為新網址</b></p><ul style='font-size:10pt'>")
        for p in update:
            fin = p.get("final_url", "") or ""
            P.append(f"<li>{html.escape(p['url'])}<br>→ 建議改為:"
                     f"<b>{html.escape(fin)}</b></li>")
        P.append("</ul>")
    # 參考(誤報,收摺)
    if ref:
        P.append(f"<p><b>■ 系統偵測到、但多屬誤報({len(ref)} 筆,無需回報)</b></p>")
        P.append("<p style='font-size:9pt;color:#666'>多為對方網站阻擋自動檢測、或境外掃描連線逾時,"
                 "連結對一般使用者通常正常。完整清單見附件。</p>")
        P.append("<ul style='font-size:9pt;color:#666'>" + "".join(line(p) for p in ref[:25]) + "</ul>")
        if len(ref) > 25:
            P.append(f"<p style='font-size:9pt;color:#666'>…另有 {len(ref)-25} 筆,詳見附件 CSV。</p>")
    # 如何回覆
    P.append("<p><b>■ 如何回覆(選用)</b></p><ul style='font-size:10pt'>")
    P.append("<li>連結<b>已移除或下架者無需回報</b>——下一輪掃描(約一個月內)就不會再出現。</li>")
    P.append(f"<li>若認為屬<b>誤報／處理中／無法處理</b>,請至回饋頁填寫,並輸入貴處專屬 "
             f"<b>PIN 碼</b>(另以專信寄送):<br>🔗 回饋頁:{FEEDBACK_URL}</li></ul>")
    P.append("<p style='font-size:9pt;color:#808080'>本郵件由連結稽核工具自動產生"
             "(可疑內容類已經 AI 讀全文複查)。完整明細見附件 CSV/PDF。</p>")
    return f"<div style='font-family:微軟正黑體,Segoe UI;font-size:11pt'>{''.join(P)}</div>"


def make_subject(org, problems, stamp):
    """產主旨:以「需處理幾筆」為主軸,有 AI 判 A 的加【急】。"""
    today = stamp or datetime.date.today().strftime("%Y-%m-%d")
    act, _u, _r = _triage(problems)
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

        # 寫附件：異常明細 CSV + 信件彙整 HTML 轉 PDF
        base = os.path.join(outdir, f"mail_{org.replace(' ', '_')}")
        csv_path = write_org_csv(problems, base + ".csv")
        pdf_path = html_to_pdf(body, base + ".pdf")
        attachments = [csv_path] + ([pdf_path] if pdf_path else [])

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
