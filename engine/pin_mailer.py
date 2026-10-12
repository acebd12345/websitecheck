# -*- coding: utf-8 -*-
"""
回饋頁 PIN 碼:產生/輪替 + 經府內 Mail Relay 寄給各局處。

取代 Apps Script 的 MailApp 寄送(Gmail 寄件人+密碼+陌生長網址,像釣魚信)。
PIN 存 Sheet「局處PIN」分頁(回饋頁 GAS 的 verifyPin 讀同一張),收件人讀「局處聯絡人員表」G 欄。
信內按鈕網址帶 #pin=XXXX,回饋頁讀到即自動登入(# 後內容不送伺服器)。

用法:
  python -m engine.pin_mailer --dry-run            # 只印要寄給誰
  python -m engine.pin_mailer --org 資訊局          # 只寄名稱含「資訊局」的局處
  python -m engine.pin_mailer --rotate             # 全部重產 PIN(每季)再寄
"""
import argparse
import datetime
import html
import secrets
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from engine import mailer

CONTACT_SHEET = "局處聯絡人員表"
PIN_SHEET = "局處PIN"
PIN_LEN = 8
PIN_VALID_DAYS = 92
# 須與 private/回饋系統.gs PIN_CHARS 一致:排除 0 O o、1 l I,不含符號(可放進網址、連點兩下可整組選取)
PIN_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"


def gen_pin():
    return "".join(secrets.choice(PIN_CHARS) for _ in range(PIN_LEN))


def _open_sheet():
    import gspread
    gc = gspread.service_account(filename=config.GA_KEY_FILE)
    return gc.open_by_key(config.MASTER_SHEET_ID)


def load_contacts(sh):
    """局處 → 寄信收件人(G 欄)。"""
    out = {}
    for r in sh.worksheet(CONTACT_SHEET).get_all_values()[1:]:
        org = (r[0] if r else "").strip()
        to = (r[6] if len(r) > 6 else "").strip()
        if org and org not in out:
            out[org] = to
    return out


def ensure_pins(sh, orgs, rotate=False):
    """補缺的 PIN;rotate=True 全部重產。回傳 {局處: (PIN, 到期日)}。寫回 Sheet(依局處對鍵,不按列序)。"""
    ws = sh.worksheet(PIN_SHEET)
    rows = ws.get_all_values()
    existing = {r[0].strip(): r for r in rows[1:] if r and r[0].strip()}
    today = datetime.date.today()
    exp = today + datetime.timedelta(days=PIN_VALID_DAYS)
    out, new_rows, changed = {}, [], 0
    for org in orgs:
        e = existing.get(org)
        if e and not rotate and len(e) > 1 and e[1].strip():
            pin, made, until = e[1].strip(), (e[2] if len(e) > 2 else ""), (e[3] if len(e) > 3 else "")
        else:
            pin, made, until = gen_pin(), today.isoformat(), exp.isoformat()
            changed += 1
        out[org] = (pin, until)
        new_rows.append([org, pin, made, until])
    if changed:
        ws.batch_clear([f"A2:D{max(len(rows), len(new_rows) + 1)}"])
        ws.update(values=new_rows, range_name=f"A2:D{len(new_rows) + 1}")
    print(f"PIN:{'輪替' if rotate else '補缺'} {changed} 組,共 {len(out)} 局處")
    return out


def build_pin_mail(org, pin, until):
    url = mailer.FEEDBACK_URL
    btn = ""
    if url.startswith("http"):
        href = html.escape(f"{url}#pin={pin}")
        btn = (f"<table cellpadding='0' cellspacing='0' style='margin:10px 0'><tr>"
               f"<td bgcolor='#1f6feb' style='border-radius:6px;padding:8px 18px'>"
               f"<a href='{href}' style='color:#ffffff;text-decoration:none;font-weight:bold'>"
               f"前往回饋頁(自動帶入 PIN)→</a></td></tr></table>")
    P = [
        "<p>您好:</p>",
        "<div style='font-size:11pt;border:2px solid #2e7d32;background:#e8f5e9;border-radius:6px;"
        "padding:8px 12px;margin:8px 0'>📌 <b>本信只是保管用,現在不需要做任何事。</b></div>",
        f"<p>這是 <b>{html.escape(org)}</b>「網站外連稽核」回饋頁的專屬 PIN 碼。日後收到稽核通知信時,"
        "<b>只有在您確認系統判斷錯誤(連結應保留)時</b>才會用到。</p>",
        "<table cellpadding='0' cellspacing='0' style='margin:8px 0'><tr>"
        "<td style='border:2px solid #555;border-radius:6px;padding:10px 20px;background:#fafafa'>"
        "<span style='font-size:11pt;color:#555'>PIN 碼:</span>"
        f"<span style='font-family:Consolas,monospace;font-size:20pt;font-weight:bold;letter-spacing:3px'>{pin}</span>"
        "</td></tr></table>",
        "<p style='font-size:9.5pt;color:#555;margin-top:0'>(連點兩下即可選取複製;或直接按下方按鈕,免輸入)</p>",
        btn,
        "<p style='font-size:10pt;color:#a32d2d;border:1px solid #a32d2d;border-radius:6px;padding:8px 10px;"
        "background:#fcecec'>※ 本通知為臺北市政府網站對外連結稽核作業,<b>非社交工程(釣魚)演練</b>,"
        "由本府信箱寄出。回饋頁為本府自建之 Google Apps Script 服務,可安心使用。</p>",
        f"<p style='font-size:9pt;color:#808080'>PIN 碼每季更換一次(本組有效至 {html.escape(until)})。"
        "忘記 PIN 碼不用擔心,下次換發時會再寄給您。</p>",
    ]
    body = f"<div style='font-family:微軟正黑體,Segoe UI;font-size:11pt;line-height:1.6'>{''.join(P)}</div>"
    subject = f"【網站外連稽核｜非社交工程演練】{org} 回饋頁專屬 PIN 碼"
    return subject, body


def main():
    ap = argparse.ArgumentParser(description="產生/輪替回饋頁 PIN 並經 Mail Relay 寄給各局處")
    ap.add_argument("--rotate", action="store_true", help="全部重產 PIN(每季)")
    ap.add_argument("--org", default="", help="只寄名稱含此字串的局處")
    ap.add_argument("--mail-to", default=None, help="收件人 override(測試用)")
    ap.add_argument("--dry-run", action="store_true", help="不寫 Sheet、不寄信,只印")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    sh = _open_sheet()
    contacts = load_contacts(sh)
    orgs = list(contacts)
    if args.dry_run:
        print(f"[DRY-RUN] 局處 {len(orgs)} 個;rotate={args.rotate}")
        for o in orgs:
            if args.org in o:
                print(f"  {o} → {args.mail_to or contacts[o] or '(無收件人,跳過)'}")
        return
    pins = ensure_pins(sh, orgs, rotate=args.rotate)
    sent = 0
    for org in orgs:
        if args.org and args.org not in org:
            continue
        to = args.mail_to or contacts.get(org, "")
        if not to:
            print(f"  ⚠ [{org}] 無收件人,跳過")
            continue
        pin, until = pins[org]
        subject, body = build_pin_mail(org, pin, until)
        try:
            mailer.send_mail(to, subject, body)
            sent += 1
        except Exception as e:
            print(f"  !! [{org}] 寄信失敗: {e}")
    print(f"完成:寄出 {sent} 封 PIN 信")


if __name__ == "__main__":
    main()
