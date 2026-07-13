# -*- coding: utf-8 -*-
"""深掃寄信模組：讀 full_overnight 報告目錄，按局處彙整異常，寄信通知。

設計要點：
  - 只寄「經 AI 複查後仍成立」的搶註(A/B) + 明確失效類(DEAD/BROKEN/REDIRECTED/WARN)
  - SUSPICIOUS 且 AI 判 C(誤報) → 不列入信
  - SUSPICIOUS 且 AI 判 ?(待人工) → 列在信末「待人工確認」區，不當警報
  - 一個局處若複查後 0 條真問題 → 不寄
  - 收件人讀府內網站表「局處Email」；鐵律 override 預設蓋成 config 的 mail_override_to（使用者信箱）

用法：
  # 搭配 full_overnight（階段4後自動呼叫，需 --mail）
  python -m engine.full_overnight --mail --mail-to <收件人>

  # 獨立對既有報告補寄
  python -m engine.mailer <報告目錄>
  python -m engine.mailer <報告目錄> --mail-to someone@example.com
  python -m engine.mailer <報告目錄> --dry-run   # 不寄，只印彙整結果
"""
import argparse, configparser, csv, datetime, html, json, os, sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import config

CSV_LIST = os.path.join(config.PRIVATE_DIR, "TCGweb_466站對照清單_v2.csv")

DEFAULT_MAIL_TO = config.get("mail_override_to", "")

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

def build_mail_html(org, problems, stamp):
    """建局處層級彙整信 HTML。"""
    today = stamp or datetime.date.today().strftime("%Y-%m-%d")

    # 分兩類：確認問題 vs 待人工
    confirmed = [p for p in problems if not p.get("_pending_human")]
    pending = [p for p in problems if p.get("_pending_human")]
    confirmed.sort(key=lambda r: (RISK_ORDER.get(r.get("risk", ""), 9), r.get("url", "")))
    pending.sort(key=lambda r: r.get("url", ""))

    n_total = len(confirmed)
    n_susp = sum(1 for p in confirmed if p["risk"] == "SUSPICIOUS")
    has_urgent = any(p.get("_verdict") == "A" for p in confirmed)

    if has_urgent:
        alert = (f"<p style='color:#c00000;font-weight:bold'>⚠ 發現 {n_susp} 筆疑似遭"
                 f"搶註/導向不當內容之連結,請優先處理!</p>")
    elif n_total == 0 and not pending:
        alert = "<p style='color:#107c10;font-weight:bold'>本次掃描未發現異常連結。</p>"
    else:
        alert = ""

    # 按站分組
    site_problems = {}
    for p in confirmed:
        site = p.get("site_name", "") or "未知站"
        site_problems.setdefault(site, []).append(p)

    detail_section = ""
    if confirmed:
        blocks = []
        for site, probs in site_problems.items():
            blocks.append(f"<h4 style='font-size:11pt;margin:14px 0 4px 0'>{html.escape(site)}</h4>")
            for p in probs:
                color = RISK_COLOR.get(p["risk"], "#000")
                verdict_tag = ""
                if p.get("_verdict") in ("A", "B"):
                    verdict_tag = f" <b style='color:#c00000'>[AI判定:{p['_verdict']}]</b>"
                locs = ""
                if p.get("all_locations"):
                    locs_items = "".join(
                        f"<li>{html.escape(line)}</li>"
                        for line in p["all_locations"].splitlines() if line.strip())
                    locs = f"<ul style='font-size:9pt;margin:0 0 6px 18px'>{locs_items}</ul>"
                blocks.append(
                    f"<p style='margin:10px 0 2px 0'>"
                    f"<b style='color:{color}'>[{p['risk']}]</b> "
                    f"{html.escape(p['url'])}{verdict_tag}<br>"
                    f"<span style='font-size:9pt'>狀況:{html.escape(p.get('note', ''))}</span></p>"
                    f"{locs}")
        detail_section = (f"<h3 style='font-size:11pt'>異常明細(共 {n_total} 筆,"
                          f"完整資料見附件 CSV)</h3>" + "".join(blocks))

    pending_section = ""
    if pending:
        pblocks = []
        for p in pending:
            pblocks.append(
                f"<li>{html.escape(p['url'])} — {html.escape(p.get('note', ''))}"
                f"<br><span style='font-size:9pt;color:#808080'>AI:{html.escape(p.get('_ai_reason', ''))}</span></li>")
        pending_section = (f"<h3 style='font-size:11pt;color:#808080'>待人工確認"
                           f"({len(pending)} 筆,非警報)</h3>"
                           f"<ul style='font-size:10pt'>{''.join(pblocks)}</ul>")

    return f"""
<div style='font-family:微軟正黑體,Segoe UI;font-size:11pt'>
<p>您好:</p>
<p>依數發部 115/6/8 通知辦理「委外案或活動結束後未移除網址」清查,
{today} 對 <b>{html.escape(org)}</b> 所管網站自動深度掃描(含 AI 複查)結果如下:</p>
{alert}
<ul style='font-size:10pt'>
<li>異常連結:{n_total} 筆(正常項目不列出)</li>
</ul>
{detail_section}
{pending_section}
<p style='font-size:9pt;color:#808080'>本郵件由連結稽核工具自動產生(經 AI 複查過濾誤報後)。
異常分類說明:{" / ".join(f"{k}={v}" for k, v in RISK_LABEL.items())}</p>
</div>"""


def make_subject(org, problems, stamp):
    """產主旨。"""
    today = stamp or datetime.date.today().strftime("%Y-%m-%d")
    confirmed = [p for p in problems if not p.get("_pending_human")]
    n_total = len(confirmed)
    n_susp = sum(1 for p in confirmed if p["risk"] == "SUSPICIOUS")
    has_urgent = any(p.get("_verdict") == "A" for p in confirmed)

    subject = f"網站對外連結深度稽核結果 - {org} {today}"
    subject += f"(異常 {n_total} 筆" + (f",可疑 {n_susp} 筆!)" if n_susp else ")")
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


def send_outlook(to, subject, html_body, attachment):
    import win32com.client
    outlook = win32com.client.Dispatch("Outlook.Application")
    mail = outlook.CreateItem(0)
    mail.To = to
    mail.Subject = subject
    mail.HTMLBody = html_body
    if attachment and os.path.exists(attachment):
        mail.Attachments.Add(os.path.abspath(attachment))
    try:
        mail.Send()
        print(f"  已寄出(outlook): {to}")
    except Exception:
        # Outlook 安全性阻擋 Send(),改開草稿讓使用者手動寄
        mail.Display()
        print(f"  已開草稿(outlook 安全性阻擋自動寄出): {to}")


def send_gmail(cfg, to, subject, html_body, attachment):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.application import MIMEApplication

    user = cfg.get("gmail", "user")
    pwd = cfg.get("gmail", "app_password")
    msg = MIMEMultipart()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    if attachment and os.path.exists(attachment):
        with open(attachment, "rb") as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(attachment))
        part["Content-Disposition"] = f'attachment; filename="{os.path.basename(attachment)}"'
        msg.attach(part)
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg, to_addrs=[to])
    print(f"  已寄出(gmail): {to}")


def send_mail(to, subject, html_body, attachment):
    cfg = _load_mail_config()
    method = cfg.get("mail", "method", fallback="outlook").strip().lower()
    if method == "gmail":
        send_gmail(cfg, to, subject, html_body, attachment)
    else:
        send_outlook(to, subject, html_body, attachment)


# ── 主流程 ──

def run(outdir, mail_to=None, dry_run=False):
    """對報告目錄執行寄信。回傳 (sent_count, skipped_orgs, details)。"""
    sys.stdout.reconfigure(encoding="utf-8")
    mail_to = mail_to or DEFAULT_MAIL_TO
    if not mail_to:
        sys.exit("錯誤: 未指定收件人。請用 --mail-to 或在 config.json 設 mail_override_to")

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

        to = mail_to  # 鐵律 override
        real_email = org_email.get(org, "")
        subject = make_subject(org, problems, stamp)
        body = build_mail_html(org, problems, stamp)

        n_confirmed = len(confirmed)
        n_pending = sum(1 for p in problems if p.get("_pending_human"))
        n_susp = sum(1 for p in confirmed if p["risk"] == "SUSPICIOUS")

        info = {"org": org, "to": to, "real_email": real_email,
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

        # 寫附件
        csv_path = os.path.join(outdir, f"mail_{org.replace(' ', '_')}.csv")
        csv_path = write_org_csv(problems, csv_path)

        try:
            send_mail(to, subject, body, csv_path)
            sent += 1
        except Exception as e:
            print(f"  !! [{org}] 寄信失敗: {e}")

    return sent, skipped, details


def main():
    ap = argparse.ArgumentParser(description="對深掃報告目錄按局處寄信")
    ap.add_argument("outdir", help="報告目錄路徑(full_overnight_* 目錄)")
    ap.add_argument("--mail-to", default=DEFAULT_MAIL_TO or None,
                    help="收件人 override(預設讀 config mail_override_to)")
    ap.add_argument("--dry-run", action="store_true", help="不寄信,只印彙整結果")
    args = ap.parse_args()

    outdir = args.outdir
    if not os.path.isabs(outdir):
        outdir = os.path.join(config.PRIVATE_DIR, "reports", outdir)

    print(f"===== 深掃寄信 {os.path.basename(outdir)} =====")
    print(f"收件人: {args.mail_to}" + (" [DRY-RUN]" if args.dry_run else ""))
    sent, skipped, details = run(outdir, mail_to=args.mail_to, dry_run=args.dry_run)
    print(f"\n完成: 寄出 {sent} 封, 跳過 {len(skipped)} 局處(零真問題)")


if __name__ == "__main__":
    main()
