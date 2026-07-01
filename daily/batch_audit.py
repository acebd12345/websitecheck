# -*- coding: utf-8 -*-
"""
批次網站連結稽核 + Outlook 逐站寄信回報
1. 讀取 domains.txt,格式: 網址,收件人 (一行一站,多收件人用分號隔開)
2. 逐站整站爬行掃描,只保留有問題的連結(OK 不列)
3. 每站寄一封信給該站收件人,異常明細 CSV 當附件
用法: python batch_audit.py             掃描全部並逐站寄出
     python batch_audit.py --daily     排程用:清單分5組,依今天星期幾掃當天那組
                                       (週一掃第1組...週五第5組,週末不掃)
     python batch_audit.py --draft     改開草稿不直接寄(逐封人工確認)
     python batch_audit.py --no-mail   只掃描產生報告,不寄信
     python batch_audit.py --sample    不掃描,用範例資料開一封草稿確認格式
     python batch_audit.py --only ivoting.taipei,id.taipei   只掃名稱/網址含指定字串的站(逗號分隔)
     python batch_audit.py --to me@x.gov   本次全部改寄給此地址(不用各站原收件人,展示用)
     python batch_audit.py --max-pages 300 覆寫每站爬行頁數上限(展示用,跑快一點)
"""
import os
import sys
import csv
import html
import datetime
import configparser

from audit_links import audit_site, norm_host, CSV_COLS, RISK_ORDER
import config  # 共用設定(專案根目錄, 由執行器設 PYTHONPATH)

OUT_DIR = config.PRIVATE_DIR  # 清單/產出/log 一律放共用 private/

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


def load_config():
    """從共用 config.json 組出 link_audit 需要的設定(取代原 batch_config.ini)。"""
    c = config._cfg
    cfg = configparser.ConfigParser()
    cfg["mail"] = {k: str(v) for k, v in c.get("mail", {}).items()}
    cfg["gmail"] = {k: str(v) for k, v in c.get("gmail", {}).items()}
    cfg["scan"] = {k: str(v) for k, v in c.get("scan", {}).items()}
    cfg["schedule"] = {k: str(v) for k, v in c.get("schedule", {}).items()}
    return cfg


def _normalize_row(name, url, to, cc):
    name, url, to, cc = name.strip(), url.strip(), to.strip(), cc.strip()
    if not url.startswith("http"):
        url = "https://" + url
    return (name, url, to, cc)


def load_sites_from_file():
    """domains.txt 格式: 網站名稱,網址,收件人,副本(收件人/副本可多人,分號隔開)"""
    path = os.path.join(OUT_DIR, "domains.txt")
    sites = []
    with open(path, encoding="utf-8-sig") as f:
        for ln, line in enumerate(f, 1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = [p.strip() for p in s.split(",", 3)]
            if len(parts) < 3 or not parts[2]:
                raise ValueError(
                    f"domains.txt 第 {ln} 行格式錯誤(需: 名稱,網址,收件人[,副本]): {s}")
            while len(parts) < 4:
                parts.append("")
            sites.append(_normalize_row(*parts))
    return sites


def load_sites(cfg):
    """清單一律來自 domains.txt(由 monthly/sync_config.py 從主設定表自動產生)。
    主設定表是唯一手動維護的來源,不再直接讀試算表分頁。"""
    sites = load_sites_from_file()
    print(f"清單來源: domains.txt({len(sites)} 站)")
    return sites


def build_mail_html(site, today, total_links, pages_note, problems, name=""):
    detail_blocks = []
    for p in problems:
        color = RISK_COLOR.get(p["risk"], "#000")
        locs = "".join(
            f"<li>{html.escape(line)}</li>" for line in p["all_locations"].splitlines())
        detail_blocks.append(
            f"<p style='margin:10px 0 2px 0'>"
            f"<b style='color:{color}'>[{p['risk']}]</b> "
            f"{html.escape(p['url'])}<br>"
            f"<span style='font-size:9pt'>狀況:{html.escape(p['note'])}</span></p>"
            f"<ul style='font-size:9pt;margin:0 0 6px 18px'>{locs}</ul>")

    n_total = len(problems)
    n_susp = sum(1 for p in problems if p["risk"] == "SUSPICIOUS")
    if n_susp:
        alert = (f"<p style='color:#c00000;font-weight:bold'>⚠ 發現 {n_susp} 筆疑似遭"
                 f"搶註/導向不當內容之連結,請優先處理!</p>")
    elif n_total == 0:
        alert = "<p style='color:#107c10;font-weight:bold'>本次掃描未發現異常連結。</p>"
    else:
        alert = ""

    detail_section = ""
    if n_total:
        detail_section = (f"<h3 style='font-size:11pt'>異常明細(共 {n_total} 筆,"
                          f"完整資料見附件 CSV)</h3>" + "".join(detail_blocks))

    return f"""
<div style='font-family:微軟正黑體,Segoe UI;font-size:11pt'>
<p>您好:</p>
<p>依數發部 115/6/8 通知辦理「委外案或活動結束後未移除網址」清查,
{today} 對 <b>{html.escape(name + " " if name else "")}{html.escape(site)}</b> 自動掃描結果如下:</p>
{alert}
<ul style='font-size:10pt'>
<li>掃描範圍:{pages_note}</li>
<li>檢出對外連結:{total_links} 筆</li>
<li>異常連結:{n_total} 筆(正常項目不列出)</li>
</ul>
{detail_section}
<p style='font-size:9pt;color:#808080'>本郵件由連結稽核工具自動產生。
異常分類說明:{ " / ".join(f"{k}={v}" for k, v in RISK_LABEL.items()) }</p>
</div>"""


def make_subject(cfg, site, today, problems, name=""):
    n_total = len(problems)
    n_susp = sum(1 for p in problems if p["risk"] == "SUSPICIOUS")
    label = f"{name}({norm_host(site)})" if name else norm_host(site)
    subject = (cfg.get("mail", "subject", fallback="網站對外連結稽核結果")
               + f" - {label} {today}")
    subject += f"(異常 {n_total} 筆" + (f",可疑 {n_susp} 筆!)" if n_susp else ")")
    if n_susp:
        subject = "【急】" + subject
    return subject


def send_outlook(to, cc, subject, html_body, attachment, draft):
    import win32com.client
    outlook = win32com.client.Dispatch("Outlook.Application")
    mail = outlook.CreateItem(0)
    mail.To = to
    if cc:
        mail.CC = cc
    mail.Subject = subject
    mail.HTMLBody = html_body
    if attachment and os.path.exists(attachment):
        mail.Attachments.Add(attachment)
    if draft:
        mail.Display()
        print(f"  已開啟草稿: {to}")
    else:
        mail.Send()
        print(f"  已寄出: {to}")


def send_gmail(cfg, to, cc, subject, html_body, attachment):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.application import MIMEApplication

    user = cfg.get("gmail", "user")
    pwd = cfg.get("gmail", "app_password")
    msg = MIMEMultipart()
    msg["From"] = user
    msg["To"] = ", ".join(a.strip() for a in to.split(";") if a.strip())
    if cc:
        msg["Cc"] = ", ".join(a.strip() for a in cc.split(";") if a.strip())
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    if attachment and os.path.exists(attachment):
        with open(attachment, "rb") as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(attachment))
        part["Content-Disposition"] = f'attachment; filename="{os.path.basename(attachment)}"'
        msg.attach(part)
    recipients = [a.strip() for a in (to + ";" + cc).split(";") if a.strip()]
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg, to_addrs=recipients)
    print(f"  已寄出(gmail): {to}")


def send_mail(cfg, to, cc, subject, html_body, attachment, draft):
    method = cfg.get("mail", "method", fallback="outlook").strip().lower()
    if method == "gmail":
        if draft:
            print("  (gmail 模式不支援草稿,已略過寄信)")
            return
        send_gmail(cfg, to, cc, subject, html_body, attachment)
    else:
        send_outlook(to, cc, subject, html_body, attachment, draft)


def write_problem_csv(problems, out_path):
    try:
        f = open(out_path, "w", newline="", encoding="utf-8-sig")
    except PermissionError:
        # 檔案被 Excel 等程式鎖住時,改寫到備用檔名
        out_path = out_path.replace(".csv", "_new.csv")
        f = open(out_path, "w", newline="", encoding="utf-8-sig")
    with f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(problems)
    return out_path


def purge_old_logs(keep_days):
    """刪除 logs/ 內超過保留天數的排程 log(預設保留 3 天)"""
    import time
    import glob
    cutoff = time.time() - keep_days * 86400
    for p in glob.glob(os.path.join(OUT_DIR, "logs", "*.log")):
        try:
            if os.path.getmtime(p) < cutoff:
                os.remove(p)
                print(f"已清除舊 log: {os.path.basename(p)}")
        except OSError:
            pass  # 當天的 log 正被寫入或無權限,略過


def run_sample(cfg):
    """用範例資料開一封草稿,確認信件格式用(收件人=自己,不需 domains.txt)"""
    today = datetime.date.today().strftime("%Y-%m-%d")
    site = "https://example.gov.taipei/"
    fake = [
        {"risk": "SUSPICIOUS", "url": "http://expired-vendor.com.tw/event2023/",
         "note": "命中可疑關鍵字: 娛樂城, casino",
         "all_locations": "活動成果頁 | https://example.gov.taipei/news/123 | 連結文字: 活動官網"},
        {"risk": "BROKEN", "url": "https://dosw.gov.taipei/cp.aspx?n=XXXX",
         "note": "HTTP 404",
         "all_locations": "福利申請 | https://example.gov.taipei/service/45 | 連結文字: 社會局網站"},
    ]
    body = build_mail_html(site, today, 532, "全站 870 頁(已爬完)", fake)
    subject = make_subject(cfg, site, today, fake) + "(範例信,請確認格式)"
    method = cfg.get("mail", "method", fallback="outlook").strip().lower()
    if method == "gmail":
        me = cfg.get("gmail", "user")
        send_gmail(cfg, me, "", subject, body, None)  # 寄給自己確認
    else:
        send_outlook("", "", subject, body, None, draft=True)


def _argval(flag, default=None):
    """取旗標後面帶的值,如 --to me@x → 回 'me@x';沒帶值或沒這旗標回 default。"""
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    draft = "--draft" in sys.argv
    no_mail = "--no-mail" in sys.argv
    only = _argval("--only")           # 只掃名稱/網址含此字串的站(逗號分隔)
    to_override = _argval("--to")      # 本次全部改寄給此地址
    max_pages_override = _argval("--max-pages")
    cfg = load_config()
    if "--sample" in sys.argv:
        run_sample(cfg)
        return

    max_pages = cfg.getint("scan", "max_pages", fallback=5000)
    if max_pages_override:
        max_pages = int(max_pages_override)
    whitelist = tuple(w.strip().lower() for w in
                      cfg.get("scan", "content_whitelist", fallback="").split(",") if w.strip())
    skip_hosts = tuple(w.strip().lower() for w in
                       cfg.get("scan", "skip_hosts", fallback="").split(",") if w.strip())
    global_cc = cfg.get("mail", "cc", fallback="").strip()
    sites = load_sites(cfg)
    if only:
        toks = [t.strip().lower() for t in only.split(",") if t.strip()]
        sites = [s for s in sites if any(t in s[0].lower() or t in s[1].lower() for t in toks)]
        print(f"--only 過濾: 保留 {len(sites)} 站 ({', '.join(s[1] for s in sites)})")
    today = datetime.date.today().strftime("%Y-%m-%d")

    if "--daily" in sys.argv:
        purge_old_logs(cfg.getint("schedule", "log_keep_days", fallback=3))
        groups = cfg.getint("schedule", "groups", fallback=5)
        weekday = datetime.date.today().weekday()  # 週一=0
        if weekday >= groups:
            print(f"今天(週{'一二三四五六日'[weekday]})不在排程範圍,結束。")
            return
        sites = [s for i, s in enumerate(sites) if i % groups == weekday]
        print(f"--daily 模式:週{'一二三四五六日'[weekday]}掃第 {weekday + 1}/{groups} 組,"
              f"共 {len(sites)} 站")

    print(f"共 {len(sites)} 個網站待掃描\n")

    for i, (name, site, to, cc_site) in enumerate(sites, 1):
        if to_override:
            to, cc = to_override, ""   # 展示/測試: 全部改寄指定地址,不帶副本
        else:
            cc = "; ".join(x for x in (cc_site, global_cc) if x)
        print(f"===== [{i}/{len(sites)}] {name} {site} -> {to}"
              + (f" (cc: {cc})" if cc else "") + " =====")
        tag = norm_host(site).replace(".", "_")
        try:
            results = audit_site(site, max_pages,
                                 links_log_path=os.path.join(OUT_DIR, f"links_{tag}.jsonl"),
                                 content_whitelist=whitelist, skip_hosts=skip_hosts)
            problems = [r for r in results if r["risk"] != "OK"]
            total_links = len(results)
            pages_note = f"全站(上限 {max_pages} 頁)"
        except Exception as e:
            print(f"  掃描失敗: {e}")
            problems = [{"risk": "DEAD", "url": site,
                         "note": f"整站無法掃描: {type(e).__name__}",
                         "all_locations": "", "host": norm_host(site)}]
            total_links = 0
            pages_note = "掃描失敗"
        problems.sort(key=lambda r: (RISK_ORDER.get(r["risk"], 9), r.get("host", "")))

        attachment = ""
        if problems:
            attachment = write_problem_csv(
                problems, os.path.join(OUT_DIR, f"problems_{tag}_{today}.csv"))
        print(f"  => 連結 {total_links} 筆,異常 {len(problems)} 筆")

        if not problems:
            print("  無異常,不寄信")
            continue
        if no_mail:
            continue
        body = build_mail_html(site, today, total_links, pages_note, problems, name)
        subject = make_subject(cfg, site, today, problems, name)
        try:
            send_mail(cfg, to, cc, subject, body, attachment, draft)
        except Exception as e:
            print(f"  !! 寄信失敗: {e}")
        print()

    print("全部完成。")


if __name__ == "__main__":
    main()
